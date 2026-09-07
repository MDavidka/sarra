"""Token-authenticated workspace and deployment API."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from syte import deployment, process_manager, workspace_api
from syte.api_responses import build_create_project_response
from syte.auth import verify_api_token
from syte.config import settings
from syte.database import get_project, get_setting
from syte.domain_utils import build_direct_url, normalize_domain
from syte.preview_manager import get_preview_status, start_preview, stop_preview_async
from syte.stack_detector import preflight
from syte.upload_limits import UPLOAD_CHUNK_BYTES

router = APIRouter(tags=["Syte API"])


class ExecuteCommandRequest(BaseModel):
    uuid: str = Field(..., description="Project/workspace UUID")
    command: str = Field(..., description="Command executed inside the workspace")
    cwd: str = Field("app", description="Relative workspace directory")
    timeout: int = Field(300, ge=1, le=1800)
    env: dict[str, str] = Field(default_factory=dict)


class CommandStep(BaseModel):
    command: str
    cwd: str = "app"
    timeout: int = 300
    stop_on_error: bool = True


class ExecuteCommandsRequest(BaseModel):
    uuid: str
    commands: list[CommandStep]
    env: dict[str, str] = Field(default_factory=dict)


class ReadFileRequest(BaseModel):
    uuid: str
    path: str


class WriteFileRequest(BaseModel):
    uuid: str
    path: str
    content: str


class DeleteFileRequest(BaseModel):
    uuid: str
    path: str


class UuidRequest(BaseModel):
    uuid: str


class SetDomainRequest(BaseModel):
    uuid: str
    domain: str


class SetEnvRequest(BaseModel):
    uuid: str
    env_vars: dict[str, str]
    merge: bool = True


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    uuid: str | None = None
    git_url: str | None = None
    git_provider: str | None = None
    branch: str = "main"
    start_command: str | None = None
    domain: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    deploy: bool = False


def _http_error(status: int, error: str, message: str) -> None:
    raise HTTPException(status, detail={"error": error, "message": message})


@router.get("/server_info")
async def api_server_info(_token: dict[str, Any] = Depends(verify_api_token)):
    """Return deployment-server metadata without exposing sensitive settings."""
    from syte import __version__
    from syte.preview_domains import resolve_preview_zone

    ip = settings.resolved_public_ip
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    preview_zone = await resolve_preview_zone()
    return {
        "ok": True,
        "version": __version__,
        "public_ip": ip,
        "gui_port": settings.port,
        "direct_url": build_direct_url(ip, settings.port),
        "gui_domain": gui_domain,
        "preview_zone": preview_zone,
        "preview_host_pattern": f"preview{{a-z}}-{{app}}.{preview_zone}" if preview_zone else "",
        "api_base": "/api",
        "docs_url": "/api/",
        "workspaces_dir": str(settings.resolved_workspaces_dir),
    }


@router.get("/deploy_preflight")
async def api_deploy_preflight(
    uuid: str = Query(..., description="Project UUID"),
    start_command: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", f"Project not found: {uuid}")
    return {"ok": True, **preflight(uuid, project, start_command)}


@router.get("/workspace_list")
async def api_workspace_list(_token: dict[str, Any] = Depends(verify_api_token)):
    workspaces = await workspace_api.workspace_list()
    return {"ok": True, "count": len(workspaces), "workspaces": workspaces}


@router.get("/workspace_get")
async def api_workspace_get(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    workspace = await workspace_api.workspace_get(uuid)
    if not workspace:
        _http_error(404, "not_found", f"Workspace not found: {uuid}")
    return {"ok": True, "workspace": workspace}


@router.get("/list_files")
async def api_list_files(
    uuid: str = Query(...),
    path: str = Query("", description="Subdirectory relative to workspace root"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    try:
        files = await workspace_api.list_workspace_files(uuid, path)
    except ValueError as error:
        _http_error(404, "not_found", str(error))
    return {"ok": True, "path": path or "/", "files": files}


@router.post("/read_file")
async def api_read_file(body: ReadFileRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    ok, content, kind = await workspace_api.read_file(body.uuid, body.path)
    if not ok:
        _http_error(404, "read_failed", str(content))
    if kind == "binary":
        return {"ok": True, "path": body.path, "encoding": "base64", "content": base64.b64encode(content).decode()}
    return {"ok": True, "path": body.path, "encoding": "utf-8", "content": content}


@router.post("/write_file")
async def api_write_file(body: WriteFileRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    try:
        ok, message = await workspace_api.write_file(body.uuid, body.path, body.content)
    except ValueError as error:
        _http_error(400, "invalid_path", str(error))
    if not ok:
        _http_error(400, "write_failed", message)
    return {"ok": True, "message": message, "path": body.path, "bytes": len(body.content.encode())}


@router.post("/execute_command")
async def api_execute_command(body: ExecuteCommandRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    code, output = await workspace_api.execute_command(body.uuid, body.command, body.cwd, body.timeout, body.env)
    return {"ok": code == 0, "exit_code": code, "output": output, "command": body.command}


@router.post("/execute_commands")
async def api_execute_commands(body: ExecuteCommandsRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    results = await workspace_api.execute_commands(body.uuid, [step.model_dump() for step in body.commands], env=body.env)
    return {"ok": all(result["ok"] for result in results), "results": results}


@router.post("/delete_file")
async def api_delete_file(body: DeleteFileRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    try:
        ok, message = await workspace_api.delete_file(body.uuid, body.path)
    except ValueError as error:
        _http_error(400, "invalid_path", str(error))
    if not ok:
        _http_error(404, "delete_failed", message)
    return {"ok": True, "message": message}


@router.post("/upload_file")
async def api_upload_file(
    uuid: str = Form(...),
    path: str = Form(...),
    file: UploadFile = File(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    async def chunks():
        while chunk := await file.read(UPLOAD_CHUNK_BYTES):
            yield chunk

    try:
        ok, message, written = await workspace_api.upload_file_stream(uuid, path, chunks())
    except workspace_api.UploadTooLargeError as error:
        _http_error(413, "upload_too_large", str(error))
    except ValueError as error:
        _http_error(400, "invalid_path", str(error))
    if not ok:
        _http_error(400, "upload_failed", message)
    return {"ok": True, "message": message, "path": path, "bytes": written}


@router.post("/set_env")
async def api_set_env(body: SetEnvRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    ok, message = await workspace_api.set_env_vars(body.uuid, body.env_vars, body.merge)
    if not ok:
        _http_error(404, "not_found", message)
    return {"ok": True, "message": message}


@router.post("/set_domain")
async def api_set_domain(body: SetDomainRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    domain = normalize_domain(body.domain)
    if not domain:
        _http_error(400, "invalid_domain", "Domain is required")
    email = await get_setting("admin_email", settings.admin_email)
    project, message = await deployment.set_custom_domain(body.uuid, domain, email)
    if not project:
        _http_error(404, "not_found", message)
    return {"ok": True, "message": message, "domain": domain, "workspace": await workspace_api.workspace_get(body.uuid)}


@router.get("/get_logs")
async def api_get_logs(
    uuid: str = Query(...),
    lines: int = Query(200, ge=1, le=2000),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {
        "ok": True,
        "uuid": uuid,
        "logs": process_manager.get_logs(uuid, lines, project.get("deploy_type", "shell")),
        "stream_url": f"/api/projects/{uuid}/logs/stream?live=1",
    }


@router.post("/create_project")
async def api_create_project(body: CreateProjectRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    project, message = await deployment.create_project_record(
        name=body.name,
        git_url=body.git_url,
        branch=body.branch,
        start_command=body.start_command,
        env_vars=body.env_vars,
        domain=body.domain,
        git_provider=body.git_provider,
        project_uuid=body.uuid,
        deploy_now=body.deploy,
    )
    if not project:
        _http_error(400, "create_failed", message)
    return build_create_project_response(project, await workspace_api.workspace_get(project["id"]), message)


@router.post("/issue_deploy")
async def api_issue_deploy(body: UuidRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    project, message = await deployment.issue_deploy(body.uuid)
    if not project:
        _http_error(404, "not_found", message)
    return {"ok": True, "uuid": project["id"], "message": message, "stream_url": f"/api/projects/{project['id']}/logs/stream?live=1"}


@router.post("/deploy_cancel")
async def api_deploy_cancel(body: UuidRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    if not await get_project(body.uuid):
        _http_error(404, "not_found", "Project not found")
    ok, message = await deployment.cancel_deploy(body.uuid)
    return {"ok": ok, "uuid": body.uuid, "message": message}


@router.post("/start_service")
async def api_start_service(body: UuidRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    project, message = await deployment.start_service(body.uuid)
    if not project:
        _http_error(404, "not_found", message)
    return {"ok": True, "uuid": body.uuid, "message": message, "running": True}


@router.post("/stop_service")
async def api_stop_service(body: UuidRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    project, message = await deployment.stop_service(body.uuid)
    if not project:
        _http_error(404, "not_found", message)
    return {"ok": True, "uuid": body.uuid, "message": message, "running": False}


@router.post("/delete_project")
async def api_delete_project(body: UuidRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    ok, message = await deployment.remove_service(body.uuid)
    if not ok:
        _http_error(404, "not_found", message)
    return {"ok": True, "message": message}


@router.post("/start_preview")
async def api_start_preview(body: UuidRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    ok, message, meta = await start_preview(body.uuid)
    if not ok:
        _http_error(400, "preview_failed", message)
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}


@router.post("/stop_preview")
async def api_stop_preview(body: UuidRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    await stop_preview_async(body.uuid)
    meta, _ = await get_preview_status(body.uuid)
    return {"ok": True, "uuid": body.uuid, "message": "Preview stopped", **(meta or {})}


@router.get("/preview_status")
async def api_preview_status(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    meta, message = await get_preview_status(uuid)
    if not meta:
        _http_error(404, "not_found", message)
    return {"ok": True, **meta}


class AgentChangeRequest(BaseModel):
    uuid: str = Field(..., description="Project UUID")
    message: str = Field(..., description="Agent message prompt")
    model_profile: str | None = Field(None, description="Requested model profile")
    plan_mode: str | None = "off"
    agent_mode: str | None = "build"
    thinking_level: str | None = Field(None, description="Requested thinking effort: low, medium, high, extra_high")
    execution_speed: str | None = Field(None, description="Requested execution speed: ultra_fast, balanced, deep_reasoning")


@router.post("/agent_change")
async def api_agent_change(body: AgentChangeRequest, _token: dict[str, Any] = Depends(verify_api_token)):
    from syte.ai.session_manager import session_manager
    import uuid as _uuid_mod
    project_id = body.uuid
    project = await get_project(project_id)
    if not project:
        _http_error(404, "not_found", f"Project not found: {project_id}")

    session_id = str(_uuid_mod.uuid4())
    overrides: dict[str, Any] = {
        "plan_mode": body.plan_mode,
        "agent_mode": body.agent_mode,
    }
    if body.model_profile:
        overrides["model"] = body.model_profile
    if body.thinking_level:
        overrides["thinking_level"] = body.thinking_level
    if body.execution_speed:
        overrides["execution_speed"] = body.execution_speed

    await session_manager.start_turn(
        project_id=project_id,
        user_message=body.message,
        session_id=session_id,
        settings_override=overrides,
    )

    request_id = str(_uuid_mod.uuid4())
    return {
        "ok": True,
        "request_id": request_id,
        "turso_session_id": session_id,
        "session_number": 1,
        "status": "accepted",
    }


@router.get("/agent_sessions")
async def api_agent_sessions(
    uuid: str = Query(..., description="Project UUID"),
    limit: int = Query(50, ge=1, le=100),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from syte.ai.session_manager import session_manager
    project_id = uuid
    project = await get_project(project_id)
    if not project:
        _http_error(404, "not_found", f"Project not found: {project_id}")

    # Return matching sessions for this project
    sessions_list = []
    for key, sess in list(session_manager.sessions.items()):
        if sess.project_id == project_id:
            summary = sess.get_status_summary()
            sessions_list.append({
                "id": sess.session_id,
                "session_number": sess.current_turn or 1,
                "status": "open" if sess.is_running else "completed",
                "created_at": getattr(sess, "created_at", None),
                "updated_at": getattr(sess, "last_activity", None),
                "session_url": f"/api/agent_session/{sess.session_id}",
            })

    return {
        "ok": True,
        "uuid": project_id,
        "turso_configured": True,
        "sessions": sessions_list[:limit],
    }


@router.get("/agent_session/{session_id}")
async def api_agent_session(
    session_id: str,
    since_id: int = Query(0, ge=0),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from syte.ai.session_manager import session_manager
    sess = session_manager.get_session_by_id(session_id)
    if not sess:
        # Check if direct key exists
        for key, candidate in list(session_manager.sessions.items()):
            if candidate.session_id == session_id:
                sess = candidate
                break

    if not sess:
        _http_error(404, "not_found", f"Agent session not found: {session_id}")

    events = []
    for idx, evt in enumerate(list(sess.event_buffer)):
        evt_id = idx + 1
        if evt_id > since_id:
            events.append({
                "id": evt_id,
                "event_type": evt.get("event") or evt.get("event_type") or "message",
                "role": evt.get("role"),
                "title": evt.get("title"),
                "detail": evt.get("message") or evt.get("detail") or evt.get("text"),
                "payload": evt,
                "created_at": evt.get("timestamp"),
            })

    return {
        "ok": True,
        "id": session_id,
        "project_id": sess.project_id,
        "session_number": sess.current_turn or 1,
        "status": "open" if sess.is_running else "completed",
        "events": events,
    }


@router.get("/agent_activity/stream")
async def api_agent_activity_stream(
    uuid: str = Query(..., description="Project UUID"),
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from fastapi.responses import StreamingResponse
    from syte.ai.session_manager import session_manager
    import json

    project_id = uuid
    target_session_id = session

    async def sse_activity_broadcaster():
        try:
            async for event_payload in session_manager.subscribe(project_id, session_id=target_session_id, replay=True):
                evt_name = event_payload.get("event", "message")
                data_str = json.dumps(event_payload)
                yield f"event: {evt_name}\ndata: {data_str}\n\n"
                if evt_name in ("done", "stopped"):
                    break
        except Exception as exc:
            err_data = json.dumps({"event": "error", "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n"

    return StreamingResponse(
        sse_activity_broadcaster(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/projects/{uuid}/agent/activity/stream")
async def api_project_agent_activity_stream(
    uuid: str,
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from fastapi.responses import StreamingResponse
    from syte.ai.session_manager import session_manager
    import json

    project_id = uuid
    target_session_id = session

    async def sse_activity_broadcaster():
        try:
            async for event_payload in session_manager.subscribe(project_id, session_id=target_session_id, replay=True):
                evt_name = event_payload.get("event", "message")
                data_str = json.dumps(event_payload)
                yield f"event: {evt_name}\ndata: {data_str}\n\n"
                if evt_name in ("done", "stopped"):
                    break
        except Exception as exc:
            err_data = json.dumps({"event": "error", "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n"

    return StreamingResponse(
        sse_activity_broadcaster(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/agent_activity")
async def api_agent_activity(
    uuid: str = Query(..., description="Project UUID"),
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    session: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from syte.ai.session_manager import session_manager
    project_id = uuid
    sess = None
    if session:
        sess = session_manager.get_session_by_id(session)
    if not sess:
        sess = session_manager.get_or_create_session(project_id)

    events = []
    for idx, evt in enumerate(list(sess.event_buffer)):
        evt_id = idx + 1
        if evt_id > since_id:
            events.append({
                "id": evt_id,
                "project_id": project_id,
                "event_type": evt.get("event") or evt.get("event_type") or "message",
                "role": evt.get("role"),
                "title": evt.get("title"),
                "detail": evt.get("message") or evt.get("detail") or evt.get("text") or evt.get("delta"),
                "payload": evt,
                "source": "session_buffer",
                "created_at": evt.get("timestamp"),
            })

    return {
        "ok": True,
        "uuid": project_id,
        "events": events[:limit],
        "count": len(events[:limit]),
        "since_id": since_id,
    }


@router.post("/agent_interrupt")
@router.post("/agent_stop")
async def api_agent_stop(
    body: dict[str, Any],
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from syte.ai.session_manager import session_manager
    project_id = body.get("uuid")
    if not project_id:
        _http_error(400, "missing_param", "uuid is required")
    res = await session_manager.stop_session(project_id)
    return {"ok": True, "result": res}


@router.get("/agent_questions")
async def api_agent_questions(
    uuid: str = Query(..., description="Project UUID"),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from syte.ai.session_manager import session_manager
    sess = session_manager.get_or_create_session(uuid)
    questions = []
    if getattr(sess, "pending_question", None):
        questions.append(sess.pending_question)
    return {"ok": True, "questions": questions}


@router.post("/agent_answer_question")
async def api_agent_answer_question(
    body: dict[str, Any],
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from syte.ai.session_manager import session_manager
    project_id = body.get("uuid")
    if not project_id:
        _http_error(400, "missing_param", "uuid is required")
    res = await session_manager.handle_user_answer(project_id, body)
    return {"ok": True, "answer": body.get("answer"), "result": res}


@router.get("/agent_skills")
async def api_agent_skills(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    from syte.ai.skills import SKILLS_REGISTRY, SKILLS_CATEGORIES_CATALOG
    skills = []
    for skill_id, skill_data in SKILLS_REGISTRY.items():
        skills.append({
            "id": skill_id,
            "name": skill_data.get("name") or skill_id.replace("_", " ").title(),
            "active": True,
            "builtin": True,
            "custom": False,
            "description": skill_data.get("description") or f"Skill blueprint for {skill_id}",
        })
    for cat_name, cat_data in SKILLS_CATEGORIES_CATALOG.items():
        cat_id = cat_name.lower().replace(" ", "_").replace("&", "and")
        skills.append({
            "id": cat_id,
            "name": cat_name,
            "active": True,
            "builtin": True,
            "custom": False,
            "description": cat_data.get("description") or f"Category guidelines for {cat_name}",
        })
    return {"ok": True, "skills": skills}


@router.post("/agent_skills_enable")
async def api_agent_skills_enable(
    body: dict[str, Any],
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return {"ok": True, "skill_id": body.get("skill_id"), "enabled": True}


@router.post("/agent_skills_disable")
async def api_agent_skills_disable(
    body: dict[str, Any],
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return {"ok": True, "skill_id": body.get("skill_id"), "disabled": True}


@router.get("/agent_mcp")
async def api_agent_mcp(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return {
        "ok": True,
        "addons": [
            {
                "id": "filesystem",
                "name": "Local Filesystem & Workspace AST",
                "connected": True,
                "builtin": True,
                "description": "Full access to workspace files, line editing, search, and directory tree.",
            },
            {
                "id": "terminal",
                "name": "Host Terminal & Command Runner",
                "connected": True,
                "builtin": True,
                "description": "Isolated bash execution with exit codes and stdout/stderr capture.",
            },
            {
                "id": "preview",
                "name": "Hot-Reloading Preview Dev Server",
                "connected": True,
                "builtin": True,
                "description": "Vite and Next.js instant preview daemon with automatic port forwarding.",
            },
        ],
    }


@router.post("/agent_mcp_connect")
async def api_agent_mcp_connect(
    body: dict[str, Any],
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return {"ok": True, "addon": body.get("addon"), "connected": True}


@router.post("/agent_mcp_disconnect")
async def api_agent_mcp_disconnect(
    body: dict[str, Any],
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return {"ok": True, "addon": body.get("addon"), "connected": False}


@router.post("/agent_mcp_register")
async def api_agent_mcp_register(
    body: dict[str, Any],
    _token: dict[str, Any] = Depends(verify_api_token),
):
    name = body.get("name") or "custom_addon"
    return {
        "ok": True,
        "addon": {
            "id": name.lower().replace(" ", "_"),
            "name": name,
            "connected": True,
            "builtin": False,
            "description": body.get("description") or "Custom MCP stdio provider",
        },
    }


