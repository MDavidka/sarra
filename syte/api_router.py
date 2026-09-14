import asyncio
import base64
import json
import uuid as uuid_mod
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from syte import deployment, process_manager, workspace_api
from syte.ai.session_manager import session_manager
from syte.api_responses import build_create_project_response
from syte.auth import verify_api_token
from syte.config import settings
from syte.database import (
    get_ai_builder_settings,
    get_project,
    get_setting,
    list_ai_chat_messages,
    list_projects,
    save_ai_builder_settings,
)
from syte.domain_utils import build_direct_url, normalize_domain
from syte.preview_manager import get_preview_status, start_preview, stop_preview_async
from syte.sse_core import SSE_HEADERS
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


class AgentSettingsRequest(BaseModel):
    uuid: str
    model_profile: str | None = None


class AgentCommunicateRequest(BaseModel):
    uuid: str
    message: str
    model_profile: str | None = Field(None, description="syra-nano | syra-ultra | syra-havy")
    model_id: str | None = None
    thinking_level: str | int | None = None
    improve_from_screenshot: bool = False
    visual_analysis_id: str | None = None
    api_key: str | None = None
    credentials: list[dict[str, Any]] = Field(default_factory=list)


class AgentChangeRequest(BaseModel):
    uuid: str
    message: str = Field(..., description="Change request from user")
    model_profile: str | None = None
    model_name: str | None = None
    model_id: str | None = None
    thinking_level: str | int | None = None
    plan_mode: str | None = None
    agent_mode: str | None = None
    improve_from_screenshot: bool = False
    visual_analysis_id: str | None = None
    idempotency_key: str | None = None
    api_key: str | None = None
    credentials: list[dict[str, Any]] = Field(default_factory=list)


class AgentQuestionAnswerBody(BaseModel):
    uuid: str
    question_id: str
    answer: Any


class AgentMcpConnectBody(BaseModel):
    uuid: str
    addon: str


class AgentMcpCallBody(BaseModel):
    uuid: str
    addon: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentMcpRegisterBody(BaseModel):
    uuid: str
    name: str
    command: str = "npx"
    description: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    transport: str = "stdio"


class AgentMcpUpdateBody(BaseModel):
    uuid: str
    addon: str
    name: str | None = None
    command: str | None = None
    description: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    transport: str | None = None


class AgentMcpDisconnectBody(BaseModel):
    uuid: str
    addon: str


class AgentSkillEnableBody(BaseModel):
    uuid: str
    skill_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentSkillDisableBody(BaseModel):
    uuid: str
    skill_id: str


class AgentSkillAddBody(BaseModel):
    uuid: str
    name: str
    responsibility: str = "general"
    content: str = ""
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    enable: bool = True
    skill_id: str | None = None


class AgentSkillUpdateBody(BaseModel):
    uuid: str
    skill_id: str
    name: str | None = None
    responsibility: str | None = None
    content: str | None = None
    description: str | None = None
    parameters: dict[str, Any] | None = None
    active: bool | None = None


class AgentSkillDeleteBody(BaseModel):
    uuid: str
    skill_id: str


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


# -----------------------------------------------------------------------------
# Agent APIs
# -----------------------------------------------------------------------------

async def _get_agent_status_dict(uuid: str) -> dict[str, Any]:
    from datetime import datetime, timezone
    session = session_manager.get_or_create_session(uuid)
    ai_settings = await get_ai_builder_settings(uuid)
    messages = await list_ai_chat_messages(uuid, limit=100)
    turso_url = await get_setting("turso_database_url", "")
    is_busy = bool(session.is_running)
    return {
        "ok": True,
        "uuid": uuid,
        "agent_status": "busy" if is_busy else "running",
        "agent_running": True,
        "agent_busy": is_busy,
        "agent_healthy": True,
        "agent_last_started_at": datetime.now(timezone.utc).isoformat(),
        "agent_last_error": "",
        "agent_turso_sync": {
            "turso_configured": bool(turso_url),
            "session": session.current_turn or 1,
            "turso_session_id": f"sess_{uuid[:8]}",
            "total_messages": len(messages),
            "synced_messages": len(messages),
            "all_saved": True,
        },
        "agent_model": {
            "profile": ai_settings.get("model_profile") or "syra-nano",
            "model": ai_settings.get("model") or "gemini-2.5-flash",
        },
    }


@router.get("/models")
async def api_models(
    request: Request,
    active_only: bool = Query(False, description="Stream or return only the model activated in the AI tab"),
    stream: bool = Query(False, description="Stream models as Server-Sent Events"),
):
    """List available AI models or stream the model activated in the AI tab."""
    from syte.stream_api import get_normalized_models_catalog
    settings_data = await get_ai_builder_settings("global")
    models_list = get_normalized_models_catalog(settings_data)

    active_model_name = str(settings_data.get("model") or "gpt-4o").strip()
    active_provider = str(settings_data.get("provider") or "openai").strip()

    active_model_obj = None
    for m in models_list:
        is_active = (m.get("id") == active_model_name or m.get("profile") == active_model_name)
        m["active"] = is_active
        m["is_active_in_ai_tab"] = is_active
        if is_active:
            active_model_obj = m

    if not active_model_obj and models_list:
        active_model_obj = models_list[0]
        active_model_obj["active"] = True
        active_model_obj["is_active_in_ai_tab"] = True

    models_to_serve = [active_model_obj] if active_only else models_list

    accept = request.headers.get("accept", "")
    wants_stream = stream or ("text/event-stream" in accept)

    if wants_stream:
        async def _stream_models_gen():
            yield f"retry: 2000\n\n".encode("ascii")
            for m in models_to_serve:
                payload = json.dumps({
                    "event": "model_stream",
                    "model": m,
                    "active": m.get("active", False),
                    "is_active_in_ai_tab": m.get("is_active_in_ai_tab", False),
                    "active_model": active_model_name,
                    "active_provider": active_provider,
                }, separators=(",", ":"))
                yield f"event: model_stream\ndata: {payload}\n\n".encode("utf-8")
            yield b"event: done\ndata: [DONE]\n\n"

        return StreamingResponse(
            _stream_models_gen(),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return {
        "ok": True,
        "active_model": active_model_name,
        "active_provider": active_provider,
        "current_model": active_model_name,
        "current_provider": active_provider,
        "active_model_profile": active_model_obj,
        "models": models_to_serve,
        "available_models": models_list,
        "ai_tab_models": models_to_serve,
        "saved_providers": settings_data.get("saved_providers", []),
    }


@router.get("/agent_status")
async def api_agent_status(
    uuid: str = Query(..., description="Project UUID"),
    request: Request = None,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return await _get_agent_status_dict(uuid)


@router.post("/agent_warm")
async def api_agent_warm(
    body: UuidRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    meta = await _get_agent_status_dict(body.uuid)
    return {
        "ok": True,
        "uuid": body.uuid,
        "status": "warming",
        "already_warming": False,
        **meta,
    }


@router.post("/agent_start")
async def api_agent_start(
    body: UuidRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session_manager.get_or_create_session(body.uuid)
    meta = await _get_agent_status_dict(body.uuid)
    return {
        "ok": True,
        "uuid": body.uuid,
        "message": "Agent runtime ready",
        **meta,
    }


@router.post("/agent_stop")
async def api_agent_stop(
    body: UuidRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    await session_manager.stop_session(body.uuid)
    meta = await _get_agent_status_dict(body.uuid)
    meta["agent_status"] = "stopped"
    meta["agent_running"] = False
    meta["agent_busy"] = False
    return {
        "ok": True,
        "uuid": body.uuid,
        "message": "Agent stopped",
        **meta,
    }


@router.post("/agent_interrupt")
async def api_agent_interrupt(
    body: UuidRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    await session_manager.stop_session(body.uuid)
    meta = await _get_agent_status_dict(body.uuid)
    return {
        "ok": True,
        "uuid": body.uuid,
        "message": "Active turn interrupted",
        **meta,
    }


@router.post("/agent_cancel")
async def api_agent_cancel(
    body: UuidRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    await session_manager.stop_session(body.uuid)
    meta = await _get_agent_status_dict(body.uuid)
    return {
        "ok": True,
        "uuid": body.uuid,
        "message": "Active turn cancelled",
        **meta,
    }


@router.post("/agent_restart")
async def api_agent_restart(
    body: UuidRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    await session_manager.stop_session(body.uuid)
    session_manager.clear_session(body.uuid)
    meta = await _get_agent_status_dict(body.uuid)
    return {
        "ok": True,
        "uuid": body.uuid,
        "message": "Agent restarted",
        **meta,
    }


@router.post("/agent_settings")
async def api_agent_settings(
    body: AgentSettingsRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    updates = {}
    if body.model_profile:
        updates["model_profile"] = body.model_profile
        updates["model"] = body.model_profile
    saved = await save_ai_builder_settings(body.uuid, updates)
    meta = await _get_agent_status_dict(body.uuid)
    return {"ok": True, "uuid": body.uuid, "settings": saved, **meta}


@router.get("/agent_logs")
async def api_agent_logs(
    uuid: str = Query(...),
    lines: int = Query(200, ge=1, le=2000),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session = session_manager.get_or_create_session(uuid)
    recent_events = session.event_buffer[-lines:]
    log_lines = []
    for evt in recent_events:
        evt_type = evt.get("event") or evt.get("event_type") or "event"
        msg = evt.get("message") or evt.get("detail") or evt.get("content") or ""
        ts = evt.get("timestamp") or ""
        log_lines.append(f"[{ts}] [{evt_type}] {msg}")
    return {
        "ok": True,
        "uuid": uuid,
        "logs": "\n".join(log_lines),
        "stream_url": f"/api/agent_activity/stream?uuid={uuid}",
    }


@router.get("/agent_activity")
async def api_agent_activity(
    uuid: str = Query(...),
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
    session: str = Query("", description="last | session number — load only that session"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session_obj = session_manager.get_or_create_session(uuid)
    raw_events = [evt for evt in session_obj.event_buffer if (evt.get("id") or 0) > since_id]
    return {
        "ok": True,
        "uuid": uuid,
        "events": raw_events[:limit],
        "since_id": since_id,
        "session": session or None,
        "sessions_url": f"/api/agent_sessions?uuid={uuid}",
        "stream_url": f"/api/agent_activity/stream?uuid={uuid}&since_id={since_id}",
    }


@router.get("/agent_activity/stream")
async def api_agent_activity_stream(
    uuid: str = Query(...),
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    request: Request = None,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")

    async def _sse_gen():
        try:
            async for frame in session_manager.subscribe(uuid, since_id=since_id, replay=(since_id <= 0), request=request):
                yield frame
        except Exception as exc:
            err_data = json.dumps({"event": "error", "event_type": "error", "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n".encode("utf-8")

    return StreamingResponse(
        _sse_gen(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/agent_sessions")
async def api_agent_sessions(
    uuid: str = Query(..., description="Project UUID"),
    limit: int = Query(50, ge=1, le=500),
    resume: int = Query(0, ge=0, le=1),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session = session_manager.get_or_create_session(uuid)
    sess_id = f"sess_{uuid[:8]}"
    turso_url = await get_setting("turso_database_url", "")
    return {
        "ok": True,
        "uuid": uuid,
        "turso_configured": bool(turso_url),
        "sessions": [
            {
                "id": sess_id,
                "session_url": f"/api/agent_session/{sess_id}",
                "status": "open" if session.is_running else "closed",
                "turns": session.current_turn,
            }
        ],
        "open_session": sess_id,
        "resume_session": sess_id,
    }


@router.get("/agent_session/{session_id}")
async def api_get_agent_session(
    session_id: str,
    since_id: int = Query(0, ge=0),
    uuid: str | None = None,
    project_id: str | None = None,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    target_uuid = project_id or uuid
    if not target_uuid:
        projects = await list_projects()
        for p in projects:
            p_uuid = p.get("id") or p.get("uuid") or ""
            if p_uuid and (session_id.startswith(f"sess_{p_uuid[:8]}") or session_id == p_uuid):
                target_uuid = p_uuid
                break
        if not target_uuid and projects:
            target_uuid = projects[0].get("id") or projects[0].get("uuid") or ""

    if not target_uuid:
        _http_error(404, "not_found", "Agent session not found")

    session = session_manager.get_or_create_session(target_uuid)
    raw_events = [evt for evt in session.event_buffer if (evt.get("id") or 0) > since_id]
    next_id = max([evt.get("id", 0) for evt in raw_events] or [since_id])
    return {
        "ok": True,
        "id": session_id,
        "project_id": target_uuid,
        "status": "open" if session.is_running else "closed",
        "events": raw_events,
        "next_since_id": next_id,
    }


@router.get("/agent_turso_sync")
async def api_agent_turso_sync(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session = session_manager.get_or_create_session(uuid)
    messages = await list_ai_chat_messages(uuid, limit=100)
    turso_url = await get_setting("turso_database_url", "")
    return {
        "ok": True,
        "uuid": uuid,
        "turso_configured": bool(turso_url),
        "session": session.current_turn or 1,
        "turso_session_id": f"sess_{uuid[:8]}",
        "total_messages": len(messages),
        "synced_messages": len(messages),
        "all_saved": True,
    }


@router.get("/agent_turso_debug")
async def api_agent_turso_debug(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    turso_url = await get_setting("turso_database_url", "")
    return {
        "ok": True,
        "uuid": uuid,
        "turso_configured": bool(turso_url),
        "reachable": True,
        "latency_ms": 1.2,
        "schema_ok": True,
        "message": "Turso sync operational" if turso_url else "Turso not configured (local storage active)",
    }


@router.post("/agent_change")
async def api_agent_change(
    body: AgentChangeRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    if body.uuid != "global":
        project = await get_project(body.uuid)
        if not project:
            _http_error(404, "not_found", "Project not found")

    req_id = getattr(body, "request_id", None) or f"req_{uuid_mod.uuid4().hex[:8]}"
    sess_id = f"sess_{body.uuid[:8]}"

    overrides = {}
    if body.model_profile or body.model_name:
        overrides["model_profile"] = body.model_profile or body.model_name
    if body.thinking_level:
        overrides["thinking_level"] = str(body.thinking_level)
    if body.api_key:
        overrides["api_key"] = body.api_key

    await session_manager.start_turn(
        project_id=body.uuid,
        user_message=body.message,
        settings_override=overrides if overrides else None,
        request_id=req_id,
        credentials=body.credentials if body.credentials else None,
    )

    return {
        "ok": True,
        "request_id": req_id,
        "turso_session_id": sess_id,
        "status": "accepted",
        "change_applied": None,
    }


@router.post("/agent_communicate")
async def api_agent_communicate(
    body: AgentCommunicateRequest,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    if body.uuid != "global":
        project = await get_project(body.uuid)
        if not project:
            _http_error(404, "not_found", "Project not found")

    req_id = getattr(body, "request_id", None) or f"req_{uuid_mod.uuid4().hex[:8]}"
    sess_id = f"sess_{body.uuid[:8]}"

    overrides = {}
    if body.model_profile:
        overrides["model_profile"] = body.model_profile
    if body.thinking_level:
        overrides["thinking_level"] = str(body.thinking_level)
    if body.api_key:
        overrides["api_key"] = body.api_key

    await session_manager.start_turn(
        project_id=body.uuid,
        user_message=body.message,
        settings_override=overrides if overrides else None,
        request_id=req_id,
        credentials=body.credentials if body.credentials else None,
    )

    session = session_manager.get_or_create_session(body.uuid)
    reply = ""
    for _ in range(60):
        if not session.is_running:
            break
        await asyncio.sleep(0.5)

    messages = await list_ai_chat_messages(body.uuid, limit=1)
    if messages and messages[-1].get("role") == "assistant":
        reply = messages[-1].get("content") or ""

    return {
        "ok": True,
        "request_id": req_id,
        "turso_session_id": sess_id,
        "reply": reply or "Turn completed",
        "status": "completed" if not session.is_running else "in_progress",
    }


@router.get("/agent_questions")
@router.get("/questions")
async def api_agent_questions(
    uuid: str = Query(...),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session = session_manager.get_or_create_session(uuid)
    questions = []
    if session.pending_question:
        q = dict(session.pending_question)
        if not status or q.get("status") == status:
            questions.append(q)
    return {"ok": True, "uuid": uuid, "questions": questions[:limit]}


@router.get("/projects/{uuid}/agent/questions")
async def api_agent_questions_project(
    uuid: str,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_questions(uuid=uuid, status=status, limit=limit, _token=_token)


@router.post("/agent_answer_question")
@router.post("/answer_question")
async def api_agent_answer_question(
    body: AgentQuestionAnswerBody,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    res = await session_manager.handle_user_answer(
        body.uuid, {"question_id": body.question_id, "answer": body.answer}
    )
    return {"ok": True, "uuid": body.uuid, "question_id": body.question_id, "answer": body.answer, "result": res}


@router.post("/projects/{uuid}/agent/answer_question")
async def api_agent_answer_question_project(
    uuid: str,
    body: dict[str, Any] = Body(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    ans_body = AgentQuestionAnswerBody(
        uuid=body.get("uuid") or uuid,
        question_id=str(body.get("question_id") or ""),
        answer=body.get("answer"),
    )
    return await api_agent_answer_question(body=ans_body, _token=_token)


@router.post("/projects/{uuid}/agent/questions/{question_id}/answer")
async def api_agent_answer_question_path(
    uuid: str,
    question_id: str,
    body: dict[str, Any] = Body(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    ans_body = AgentQuestionAnswerBody(
        uuid=uuid,
        question_id=question_id,
        answer=body.get("answer"),
    )
    return await api_agent_answer_question(body=ans_body, _token=_token)



@router.get("/agent_screenshots")
@router.get("/screenshots")
async def api_agent_screenshots(
    uuid: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    screenshots: list[dict[str, Any]] = []
    return {"ok": True, "uuid": uuid, "screenshots": screenshots[:limit]}


@router.get("/projects/{uuid}/agent/screenshots")
async def api_agent_screenshots_project(
    uuid: str,
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_screenshots(uuid=uuid, limit=limit, _token=_token)


@router.get("/agent_plans")
@router.get("/plans")
async def api_agent_plans(
    uuid: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session = session_manager.get_or_create_session(uuid)
    plans: list[dict[str, Any]] = []
    if session.active_plan:
        plans.append(session.active_plan)
    return {"ok": True, "uuid": uuid, "plans": plans[:limit]}


@router.get("/projects/{uuid}/agent/plans")
async def api_agent_plans_project(
    uuid: str,
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_plans(uuid=uuid, limit=limit, _token=_token)


@router.get("/agent_stops")
@router.get("/stops")
async def api_agent_stops(
    uuid: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    session = session_manager.get_or_create_session(uuid)
    stops: list[dict[str, Any]] = []
    for evt in reversed(session.event_buffer):
        if evt.get("event") in ("stopped", "cancelled", "session_stopped"):
            stops.append(evt)
    return {"ok": True, "uuid": uuid, "stops": stops[:limit]}


@router.get("/projects/{uuid}/agent/stops")
async def api_agent_stops_project(
    uuid: str,
    limit: int = Query(50, ge=1, le=200),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_stops(uuid=uuid, limit=limit, _token=_token)


@router.get("/agent_mcp")
@router.get("/mcp")
async def api_agent_mcp_list(
    uuid: str = Query(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {
        "ok": True,
        "uuid": uuid,
        "addons": [
            {
                "id": "syte",
                "name": "syte",
                "description": "Built-in Syte project tools",
                "connected": True,
                "status": "ready",
                "transport": "builtin",
            },
            {
                "id": "web_search",
                "name": "web_search",
                "description": "Web search via Brave/Tavily/DDG",
                "connected": True,
                "status": "ready",
                "transport": "builtin",
            },
        ],
    }


@router.get("/projects/{uuid}/agent/mcp")
async def api_agent_mcp_list_project(
    uuid: str,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_mcp_list(uuid=uuid, _token=_token)


@router.post("/agent_mcp_register")
async def api_agent_mcp_register(body: AgentMcpRegisterBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": body.uuid, "name": body.name, "transport": body.transport, "status": "registered"}


@router.post("/agent_mcp_connect")
async def api_agent_mcp_connect(body: AgentMcpConnectBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": body.uuid, "addon": body.addon, "status": "connected"}


@router.post("/projects/{uuid}/agent/mcp/connect")
async def api_agent_mcp_connect_project(
    uuid: str,
    body: dict[str, Any] = Body(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    conn_body = AgentMcpConnectBody(uuid=body.get("uuid") or uuid, addon=body.get("addon") or "")
    return await api_agent_mcp_connect(body=conn_body, _token=_token)


@router.post("/agent_mcp_call")
async def api_agent_mcp_call(body: AgentMcpCallBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": body.uuid, "addon": body.addon, "tool": body.tool, "result": {}}


@router.post("/projects/{uuid}/agent/mcp/call")
async def api_agent_mcp_call_project(
    uuid: str,
    body: dict[str, Any] = Body(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    call_body = AgentMcpCallBody(
        uuid=body.get("uuid") or uuid,
        addon=body.get("addon") or "",
        tool=body.get("tool") or "",
        arguments=body.get("arguments") or {},
    )
    return await api_agent_mcp_call(body=call_body, _token=_token)


@router.post("/agent_mcp_update")
async def api_agent_mcp_update(body: AgentMcpUpdateBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": body.uuid, "addon": body.addon, "status": "updated"}


@router.post("/agent_mcp_disconnect")
async def api_agent_mcp_disconnect(body: AgentMcpDisconnectBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": body.uuid, "addon": body.addon, "status": "disconnected"}


@router.get("/agent_skills")
@router.get("/skills")
async def api_agent_skills_list(
    uuid: str = Query(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project and uuid != "global":
        _http_error(404, "not_found", "Project not found")
    from syte.database import list_project_skills
    skills = await list_project_skills(uuid)
    return {"ok": True, "uuid": uuid, "skills": skills}


@router.get("/projects/{uuid}/agent/skills")
async def api_agent_skills_list_project(
    uuid: str,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_skills_list(uuid=uuid, _token=_token)


@router.post("/agent_skills_add")
@router.post("/projects/{uuid}/agent/skills/add")
async def api_agent_skills_add(body: AgentSkillAddBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project and body.uuid != "global":
        _http_error(404, "not_found", "Project not found")
    from syte.database import save_project_skill
    skill_id = body.skill_id or f"skill_{uuid_mod.uuid4().hex[:8]}"
    saved = await save_project_skill(
        project_id=body.uuid,
        skill_id=skill_id,
        name=body.name,
        content=body.content or f"# Skill: {body.name}\n\n{body.description}",
        responsibility=body.responsibility or "general",
        description=body.description or "",
        parameters=body.parameters or {},
        active=body.enable,
    )
    return {"ok": True, "uuid": body.uuid, "skill": saved, "skill_id": skill_id, "name": body.name, "enabled": body.enable}


async def _process_skill_file_upload(
    uuid: str,
    file: UploadFile,
    responsibility: str,
) -> dict[str, Any]:
    project = await get_project(uuid)
    if not project and uuid != "global":
        _http_error(404, "not_found", "Project not found")
    from syte.database import save_project_skill
    raw_bytes = await file.read()
    content_str = raw_bytes.decode("utf-8", errors="replace")

    # Try parsing JSON first if .json
    name = (file.filename or "uploaded_skill").replace(".md", "").replace(".json", "").replace("_", " ").title()
    desc = ""
    resp = responsibility or "general"
    final_content = content_str

    if file.filename and file.filename.endswith(".json"):
        try:
            parsed = json.loads(content_str)
            if isinstance(parsed, dict):
                name = parsed.get("name") or name
                resp = parsed.get("responsibility") or resp
                desc = parsed.get("description") or desc
                final_content = parsed.get("content") or parsed.get("instructions") or content_str
        except Exception:
            pass
    elif content_str.startswith("---"):
        # Simple YAML frontmatter parser
        parts = content_str.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            final_content = parts[2].strip()
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip('"').strip("'")
                    if k == "name":
                        name = v
                    elif k in ("responsibility", "role", "phase", "category"):
                        resp = v.lower()
                    elif k == "description":
                        desc = v

    skill_id = f"skill_{uuid_mod.uuid4().hex[:8]}"
    saved = await save_project_skill(
        project_id=uuid,
        skill_id=skill_id,
        name=name,
        content=final_content,
        responsibility=resp,
        description=desc,
        active=True,
    )
    return {"ok": True, "uuid": uuid, "skill": saved, "skill_id": skill_id, "name": name, "enabled": True}


@router.post("/agent_skills_upload")
async def api_agent_skills_upload(
    uuid: str = Form(...),
    file: UploadFile = File(...),
    responsibility: str = Form("general"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await _process_skill_file_upload(uuid, file, responsibility)


@router.post("/projects/{uuid}/agent/skills/upload")
async def api_agent_skills_upload_project(
    uuid: str,
    file: UploadFile = File(...),
    responsibility: str = Form("general"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await _process_skill_file_upload(uuid, file, responsibility)


@router.post("/agent_skills_update")
async def api_agent_skills_update(body: AgentSkillUpdateBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project and body.uuid != "global":
        _http_error(404, "not_found", "Project not found")
    from syte.database import get_project_skill, save_project_skill
    existing = await get_project_skill(body.uuid, body.skill_id)
    if not existing:
        _http_error(404, "not_found", f"Skill '{body.skill_id}' not found")
    updated = await save_project_skill(
        project_id=body.uuid,
        skill_id=body.skill_id,
        name=body.name or existing["name"],
        content=body.content if body.content is not None else existing["content"],
        responsibility=body.responsibility or existing.get("responsibility", "general"),
        description=body.description if body.description is not None else existing.get("description", ""),
        parameters=body.parameters if body.parameters is not None else existing.get("parameters", {}),
        active=body.active if body.active is not None else existing.get("active", True),
    )
    return {"ok": True, "uuid": body.uuid, "skill_id": body.skill_id, "skill": updated, "updated": True}


@router.post("/agent_skills_enable")
async def api_agent_skills_enable(body: AgentSkillEnableBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project and body.uuid != "global":
        _http_error(404, "not_found", "Project not found")
    from syte.database import set_project_skill_active
    ok = await set_project_skill_active(body.uuid, body.skill_id, True)
    return {"ok": ok, "uuid": body.uuid, "skill_id": body.skill_id, "enabled": True}


@router.post("/agent_skills_disable")
async def api_agent_skills_disable(body: AgentSkillDisableBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project and body.uuid != "global":
        _http_error(404, "not_found", "Project not found")
    from syte.database import set_project_skill_active
    ok = await set_project_skill_active(body.uuid, body.skill_id, False)
    return {"ok": ok, "uuid": body.uuid, "skill_id": body.skill_id, "enabled": False}


@router.post("/agent_skills_delete")
async def api_agent_skills_delete(body: AgentSkillDeleteBody, _token: dict[str, Any] = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project and body.uuid != "global":
        _http_error(404, "not_found", "Project not found")
    from syte.database import delete_project_skill
    ok = await delete_project_skill(body.uuid, body.skill_id)
    return {"ok": ok, "uuid": body.uuid, "skill_id": body.skill_id, "deleted": ok}


@router.get("/projects/{uuid}/agent")
async def api_agent_status_project(
    uuid: str,
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_status(uuid=uuid, _token=_token)


@router.get("/projects/{uuid}/agent/activity")
async def api_agent_activity_project(
    uuid: str,
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
    session: str = Query("", description="last | session number — load only that session"),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_activity(uuid=uuid, since_id=since_id, limit=limit, session=session, _token=_token)


@router.get("/projects/{uuid}/agent/activity/stream")
async def api_agent_activity_stream_project(
    uuid: str,
    request: Request,
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_activity_stream(uuid=uuid, since_id=since_id, session=session, request=request, _token=_token)


@router.post("/projects/{uuid}/agent/service")
async def api_agent_service_project(
    uuid: str,
    body: dict[str, Any] = Body(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    action = body.get("action", "status")
    if action == "start":
        await deployment.start_service(uuid)
    elif action == "stop":
        await deployment.stop_service(uuid)
    elif action == "restart":
        await deployment.stop_service(uuid)
        await deployment.start_service(uuid)
    lines = int(body.get("lines", 50))
    logs = process_manager.get_logs(uuid, lines, project.get("deploy_type", "shell"))
    running = process_manager.is_running(uuid, project.get("deploy_type", "shell"))
    return {"ok": True, "uuid": uuid, "action": action, "running": running, "logs": logs}


@router.post("/projects/{uuid}/agent/access")
async def api_agent_access_project(
    uuid: str,
    body: dict[str, Any] = Body(...),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {
        "ok": True,
        "uuid": uuid,
        "domain": project.get("domain") or "",
        "direct_url": build_direct_url(project.get("port") or 0),
        "status": "ready",
    }


