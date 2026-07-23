"""Syte external API (token-authenticated) — for AI agents and automation."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from syte import deployment, process_manager
from syte.api_responses import build_create_project_response
from syte.auth import verify_api_token
from syte.cloud_agent import (
    communicate_with_agent,
    get_agent_logs,
    get_agent_status,
    interrupt_agent,
    restart_agent,
    start_agent,
    stop_agent,
    test_agent,
    update_agent_settings,
    warm_agent,
)
from syte.certificates import apply_proxy_config
from syte.config import settings
from syte.database import get_project, get_setting
from syte.domain_utils import build_direct_url, normalize_domain
from syte.upload_limits import UPLOAD_CHUNK_BYTES
from syte import workspace_api

router = APIRouter(tags=["Syte API"])


class ExecuteCommandRequest(BaseModel):
    uuid: str = Field(..., description="Project/workspace UUID")
    command: str = Field(..., description="Any shell command (npm install, mkdir, cat, etc.)")
    cwd: str = Field("app", description="Relative working directory inside workspace")
    timeout: int = Field(300, ge=1, le=1800)
    env: dict[str, str] = Field(default_factory=dict, description="Extra env vars for this command")


class CommandStep(BaseModel):
    command: str
    cwd: str = "app"
    timeout: int = 300
    stop_on_error: bool = True


class ExecuteCommandsRequest(BaseModel):
    uuid: str
    commands: list[CommandStep]
    env: dict[str, str] = Field(default_factory=dict)


class PathRequest(BaseModel):
    uuid: str
    path: str = ""


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
    name: str = Field(..., description="Project display name (only required field)")
    uuid: str | None = Field(None, description="Optional custom UUID")
    git_url: str | None = Field(None, description="Optional — cloned on issue_deploy, not required at create")
    git_provider: str | None = Field(
        None,
        description="Shorthand: github.com/user/repo.git",
    )
    branch: str = "main"
    start_command: str | None = None
    domain: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    deploy: bool = Field(
        False,
        description="If true, start deploy immediately after create. Default: false (empty workspace only).",
    )


class AgentSettingsRequest(BaseModel):
    uuid: str
    model_profile: str | None = None


class AgentCommunicateRequest(BaseModel):
    uuid: str
    message: str
    model_profile: str | None = Field(None, description="syra-nano | syra-base | syra-havy | syra-ultra")
    thinking_level: int | None = Field(
        None, ge=1, le=5, description="1 Instant … 5 Max — per-request depth (does not persist model_profile)"
    )
    improve_from_screenshot: bool = False
    visual_analysis_id: str | None = None


class AgentChangeRequest(BaseModel):
    uuid: str
    message: str = Field(..., description="Change request from sycord.com user")
    model_profile: str | None = Field(None, description="Model profile alias (syra-nano/base/havy)")
    model_name: str | None = Field(None, description="Alias for model_profile from sycord.com")
    thinking_level: int | None = Field(
        None, ge=1, le=5, description="1 Instant … 5 Max — per-request depth (does not persist model_profile)"
    )
    improve_from_screenshot: bool = False
    visual_analysis_id: str | None = None
    idempotency_key: str | None = Field(
        None, description="Optional client key — retries return the same request_id"
    )


class AgentQuestionAnswerBody(BaseModel):
    uuid: str
    question_id: str
    answer: str | int | float | list[str] | dict


class AgentMcpConnectBody(BaseModel):
    uuid: str
    addon: str


class AgentMcpCallBody(BaseModel):
    uuid: str
    addon: str
    tool: str
    arguments: dict = Field(default_factory=dict)


class AgentMcpRegisterBody(BaseModel):
    uuid: str
    name: str
    command: str
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
    parameters: dict[str, str] = Field(default_factory=dict)


class AgentSkillDisableBody(BaseModel):
    uuid: str
    skill_id: str


class AgentSkillAddBody(BaseModel):
    uuid: str
    name: str
    content: str
    description: str = ""
    parameters: dict[str, str] = Field(default_factory=dict)
    enable: bool = True
    skill_id: str | None = None


class AgentSkillUpdateBody(BaseModel):
    uuid: str
    skill_id: str
    name: str | None = None
    content: str | None = None
    description: str | None = None
    parameters: dict[str, str] | None = None


class AgentSkillDeleteBody(BaseModel):
    uuid: str
    skill_id: str


def _http_error(status: int, error: str, message: str):
    raise HTTPException(status, detail={"error": error, "message": message})


@router.get("/server_info")
async def api_server_info(_token: dict = Depends(verify_api_token)):
    """Server metadata useful for AI (public IP, version, base URLs)."""
    from syte import __version__
    ip = settings.resolved_public_ip
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    from syte.preview_domains import resolve_preview_zone
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
        "ai_spec_url": "/api/ai.json",
        "workspaces_dir": str(settings.resolved_workspaces_dir),
    }


@router.get("/validate_design")
async def api_validate_design(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Run Sycord Design Contract linter on project workspace."""
    from syte.design_linter import validate_design

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", f"Project not found: {uuid}")
    return validate_design(uuid)


@router.get("/workspace_list")
async def api_workspace_list(_token: dict = Depends(verify_api_token)):
    workspaces = await workspace_api.workspace_list()
    return {"ok": True, "count": len(workspaces), "workspaces": workspaces}


@router.get("/workspace_get")
async def api_workspace_get(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    ws = await workspace_api.workspace_get(uuid)
    if not ws:
        _http_error(404, "not_found", f"Workspace not found: {uuid}")
    return {"ok": True, "workspace": ws}


@router.get("/list_files")
async def api_list_files(
    uuid: str = Query(...),
    path: str = Query("", description="Subdirectory relative to workspace root"),
    _token: dict = Depends(verify_api_token),
):
    try:
        files = await workspace_api.list_workspace_files(uuid, path)
    except ValueError as exc:
        _http_error(404, "not_found", str(exc))
    return {"ok": True, "path": path or "/", "files": files}


@router.post("/read_file")
async def api_read_file(body: ReadFileRequest, _token: dict = Depends(verify_api_token)):
    ok, content, kind = await workspace_api.read_file(body.uuid, body.path)
    if not ok:
        _http_error(404, "read_failed", str(content))
    if kind == "binary":
        import base64
        return {"ok": True, "path": body.path, "encoding": "base64", "content": base64.b64encode(content).decode()}
    return {"ok": True, "path": body.path, "encoding": "utf-8", "content": content}


@router.post("/write_file")
async def api_write_file(body: WriteFileRequest, _token: dict = Depends(verify_api_token)):
    try:
        ok, message = await workspace_api.write_file(body.uuid, body.path, body.content)
    except ValueError as exc:
        _http_error(400, "invalid_path", str(exc))
    if not ok:
        _http_error(400, "write_failed", message)
    return {"ok": True, "message": message, "path": body.path, "bytes": len(body.content.encode())}


@router.post("/execute_command")
async def api_execute_command(body: ExecuteCommandRequest, _token: dict = Depends(verify_api_token)):
    """Run shell commands for scaffolding/lint — npm run build is FORBIDDEN, use issue_deploy."""
    code, output = await workspace_api.execute_command(
        body.uuid, body.command, body.cwd, body.timeout, body.env
    )
    return {"ok": code == 0, "exit_code": code, "output": output, "command": body.command}


@router.post("/execute_commands")
async def api_execute_commands(body: ExecuteCommandsRequest, _token: dict = Depends(verify_api_token)):
    """Run multiple custom commands in sequence."""
    steps = [s.model_dump() for s in body.commands]
    results = await workspace_api.execute_commands(body.uuid, steps, env=body.env)
    all_ok = all(r["ok"] for r in results)
    return {"ok": all_ok, "results": results}


@router.post("/delete_file")
async def api_delete_file(body: DeleteFileRequest, _token: dict = Depends(verify_api_token)):
    try:
        ok, message = await workspace_api.delete_file(body.uuid, body.path)
    except ValueError as exc:
        _http_error(400, "invalid_path", str(exc))
    if not ok:
        _http_error(404, "delete_failed", message)
    return {"ok": True, "message": message}


@router.post("/upload_file")
async def api_upload_file(
    uuid: str = Form(...),
    path: str = Form(...),
    file: UploadFile = File(...),
    _token: dict = Depends(verify_api_token),
):
    async def chunks():
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk

    try:
        ok, message, written = await workspace_api.upload_file_stream(uuid, path, chunks())
    except workspace_api.UploadTooLargeError as exc:
        _http_error(413, "upload_too_large", str(exc))
    except ValueError as exc:
        _http_error(400, "invalid_path", str(exc))
    if not ok:
        _http_error(400, "upload_failed", message)
    return {"ok": True, "message": message, "path": path, "bytes": written}


@router.post("/set_env")
async def api_set_env(body: SetEnvRequest, _token: dict = Depends(verify_api_token)):
    ok, message = await workspace_api.set_env_vars(body.uuid, body.env_vars, body.merge)
    if not ok:
        _http_error(404, "not_found", message)
    return {"ok": True, "message": message}


@router.post("/set_domain")
async def api_set_domain(body: SetDomainRequest, _token: dict = Depends(verify_api_token)):
    domain = normalize_domain(body.domain)
    if not domain:
        _http_error(400, "invalid_domain", "Domain is required")
    email = await get_setting("admin_email", settings.admin_email)
    project, message = await deployment.set_custom_domain(body.uuid, domain, email)
    if not project:
        _http_error(404, "not_found", message)
    ws = await workspace_api.workspace_get(body.uuid)
    return {"ok": True, "message": message, "domain": domain, "workspace": ws}


@router.get("/get_logs")
async def api_get_logs(
    uuid: str = Query(...),
    lines: int = Query(200, ge=1, le=2000),
    _token: dict = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    logs = process_manager.get_logs(uuid, lines, project.get("deploy_type", "shell"))
    return {
        "ok": True,
        "uuid": uuid,
        "logs": logs,
        "stream_url": f"/api/projects/{uuid}/logs/stream?live=1",
    }


@router.get("/agent_status")
async def api_agent_status(
    uuid: str = Query(..., description="Project UUID"),
    request: Request = None,
    _token: dict = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    base = str(request.base_url).rstrip("/") if request else ""
    return {"ok": True, "uuid": uuid, **(await get_agent_status(uuid, request_base=base))}


@router.post("/agent_start")
async def api_agent_start(body: UuidRequest, request: Request, _token: dict = Depends(verify_api_token)):
    ok, message, meta = await start_agent(body.uuid)
    if not ok:
        _http_error(400, "agent_start_failed", message)
    meta = await get_agent_status(body.uuid, request_base=str(request.base_url).rstrip("/"))
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}


@router.post("/agent_warm")
async def api_agent_warm(
    body: UuidRequest,
    _token: dict = Depends(verify_api_token),
):
    """Schedule the persistent runtime without waiting for cold startup."""
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {
        "uuid": body.uuid,
        **(await warm_agent(body.uuid, source="external_api")),
    }


@router.post("/agent_stop")
async def api_agent_stop(body: UuidRequest, request: Request, _token: dict = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    ok, message = await stop_agent(body.uuid)
    meta = await get_agent_status(body.uuid, request_base=str(request.base_url).rstrip("/"))
    return {"ok": ok, "uuid": body.uuid, "message": message, **meta}


@router.post("/agent_interrupt")
async def api_agent_interrupt(body: UuidRequest, request: Request, _token: dict = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    from syte.agent_jobs import cancel_agent_job

    ok, message = await cancel_agent_job(body.uuid)
    if not ok:
        _http_error(400, "agent_interrupt_failed", message)
    meta = await get_agent_status(body.uuid, request_base=str(request.base_url).rstrip("/"))
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}


@router.post("/agent_cancel")
async def api_agent_cancel(body: UuidRequest, request: Request, _token: dict = Depends(verify_api_token)):
    """Cancel the active agent job/turn without submitting a new message."""
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    from syte.agent_jobs import cancel_agent_job

    ok, message = await cancel_agent_job(body.uuid)
    meta = await get_agent_status(body.uuid, request_base=str(request.base_url).rstrip("/"))
    return {"ok": ok, "uuid": body.uuid, "message": message, **meta}


@router.post("/agent_restart")
async def api_agent_restart(body: UuidRequest, request: Request, _token: dict = Depends(verify_api_token)):
    ok, message, meta = await restart_agent(body.uuid)
    if not ok:
        _http_error(400, "agent_restart_failed", message)
    meta = await get_agent_status(body.uuid, request_base=str(request.base_url).rstrip("/"))
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}


@router.post("/agent_settings")
async def api_agent_settings(body: AgentSettingsRequest, _token: dict = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    meta = await update_agent_settings(body.uuid, model_profile=body.model_profile)
    return {"ok": True, "uuid": body.uuid, **meta}


@router.get("/agent_logs")
async def api_agent_logs(
    uuid: str = Query(...),
    lines: int = Query(200, ge=1, le=2000),
    _token: dict = Depends(verify_api_token),
):
    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {
        "ok": True,
        "uuid": uuid,
        "logs": get_agent_logs(uuid, lines),
        "stream_url": f"/api/projects/{uuid}/agent/logs/stream?live=1",
    }


@router.get("/agent_activity")
async def api_agent_activity(
    uuid: str = Query(...),
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
    session: str = Query("", description="last | session number — load only that session"),
    _token: dict = Depends(verify_api_token),
):
    """Local SQLite activity snapshot. For the durable, UUID-addressable record
    of a turn use ``GET /api/agent_session/{session_id}`` (see ``sessions_url``
    below and ``GET /api/agent_sessions?uuid=``) instead of streaming."""
    from syte.agent_activity import list_agent_events

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    events = await list_agent_events(
        uuid, since_id=since_id, limit=limit, session=session or None,
    )
    return {
        "ok": True,
        "uuid": uuid,
        "events": events,
        "since_id": since_id,
        "session": session or None,
        "sessions_url": f"/api/agent_sessions?uuid={uuid}",
    }


@router.get("/agent_screenshots")
async def api_agent_screenshots(
    uuid: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import list_screenshots

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, "screenshots": await list_screenshots(uuid, limit=limit)}


@router.get("/agent_plans")
async def api_agent_plans(
    uuid: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import list_plans

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, "plans": await list_plans(uuid, limit=limit)}


@router.get("/agent_questions")
async def api_agent_questions(
    uuid: str = Query(...),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import list_questions

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {
        "ok": True,
        "uuid": uuid,
        "questions": await list_questions(uuid, status=status, limit=limit),
    }


@router.post("/agent_answer_question")
async def api_agent_answer_question(
    body: AgentQuestionAnswerBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_activity import record_agent_event
    from syte.agent_artifacts import answer_question
    from syte.cloud_agent_store import current_turso_session_id

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await answer_question(body.uuid, body.question_id, body.answer)
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "answer_failed",
            result.get("message") or "Failed to answer question",
        )
    if not result.get("already_answered"):
        turso_session_id = await current_turso_session_id(body.uuid)
        await record_agent_event(
            body.uuid,
            "question_answered",
            role="user",
            title="Answer",
            detail=str(result.get("answer") or "")[:4000],
            payload={"question_id": body.question_id, "answer": result.get("answer")},
            source="external_api",
            turso_session_id=turso_session_id,
        )
    return result


@router.get("/agent_stops")
async def api_agent_stops(
    uuid: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import list_session_stops

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, "stops": await list_session_stops(uuid, limit=limit)}


@router.get("/agent_mcp")
async def api_agent_mcp_list(
    uuid: str = Query(...),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import list_mcp_addons

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, "addons": await list_mcp_addons(uuid)}


@router.post("/agent_mcp_register")
async def api_agent_mcp_register(
    body: AgentMcpRegisterBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import register_mcp_addon

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    addon = await register_mcp_addon(
        body.uuid,
        name=body.name,
        description=body.description,
        command=body.command,
        args=body.args,
        env=body.env,
        transport=body.transport,
    )
    return {"ok": True, **addon}


@router.post("/agent_mcp_connect")
async def api_agent_mcp_connect(
    body: AgentMcpConnectBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import connect_mcp_addon

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await connect_mcp_addon(body.uuid, body.addon)
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "mcp_connect_failed",
            result.get("message") or "Failed to connect MCP addon",
        )
    return result


@router.post("/agent_mcp_call")
async def api_agent_mcp_call(
    body: AgentMcpCallBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import call_mcp_addon

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return await call_mcp_addon(body.uuid, body.addon, body.tool, body.arguments)


@router.post("/agent_mcp_update")
async def api_agent_mcp_update(
    body: AgentMcpUpdateBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import update_mcp_addon

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await update_mcp_addon(
        body.uuid,
        body.addon,
        name=body.name,
        description=body.description,
        command=body.command,
        args=body.args,
        env=body.env,
        transport=body.transport,
    )
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "mcp_update_failed",
            result.get("message") or "Failed to update MCP addon",
        )
    return result


@router.post("/agent_mcp_disconnect")
async def api_agent_mcp_disconnect(
    body: AgentMcpDisconnectBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_artifacts import disconnect_mcp_addon

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await disconnect_mcp_addon(body.uuid, body.addon)
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "mcp_disconnect_failed",
            result.get("message") or "Failed to disconnect MCP addon",
        )
    return result


@router.get("/agent_skills")
async def api_agent_skills_list(
    uuid: str = Query(...),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_skills import get_project_skills

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, "skills": await get_project_skills(uuid)}


@router.post("/agent_skills_add")
async def api_agent_skills_add(
    body: AgentSkillAddBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_skills import add_custom_skill

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await add_custom_skill(
        body.uuid,
        name=body.name,
        description=body.description,
        content=body.content,
        parameters=body.parameters,
        enable=body.enable,
        skill_id=body.skill_id,
    )
    if not result.get("ok"):
        _http_error(
            400,
            result.get("error") or "skill_add_failed",
            result.get("message") or "Failed to add skill",
        )
    return result


@router.post("/agent_skills_update")
async def api_agent_skills_update(
    body: AgentSkillUpdateBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_skills import update_custom_skill

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await update_custom_skill(
        body.uuid,
        body.skill_id,
        name=body.name,
        description=body.description,
        content=body.content,
        parameters=body.parameters,
    )
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "skill_update_failed",
            result.get("message") or "Failed to update skill",
        )
    return result


@router.post("/agent_skills_enable")
async def api_agent_skills_enable(
    body: AgentSkillEnableBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_skills import enable_skill

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await enable_skill(body.uuid, body.skill_id, body.parameters)
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "skill_enable_failed",
            result.get("message") or "Failed to enable skill",
        )
    return result


@router.post("/agent_skills_disable")
async def api_agent_skills_disable(
    body: AgentSkillDisableBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_skills import disable_skill

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await disable_skill(body.uuid, body.skill_id)
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "skill_disable_failed",
            result.get("message") or "Failed to disable skill",
        )
    return result


@router.post("/agent_skills_delete")
async def api_agent_skills_delete(
    body: AgentSkillDeleteBody,
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_skills import delete_custom_skill

    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    result = await delete_custom_skill(body.uuid, body.skill_id)
    if not result.get("ok"):
        _http_error(
            404 if result.get("error") == "not_found" else 400,
            result.get("error") or "skill_delete_failed",
            result.get("message") or "Failed to delete skill",
        )
    return result


@router.get("/agent_turso_sync")
async def api_agent_turso_sync(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Aggregate 'all messages saved to Turso' status for the current session.

    Drives the GUI's green/red brain indicator: ``all_saved: true`` means
    every message appended locally for the project's current chat session
    has been durably mirrored into the shared Turso ``agent_message`` table
    (see ``docs/cloud-agent-contract.md``); ``all_saved: false`` means at
    least one has not.
    """
    from syte.cloud_agent import turso_message_sync_status

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, **(await turso_message_sync_status(uuid))}


@router.get("/agent_turso_debug")
async def api_agent_turso_debug(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Diagnose why the brain indicator is red — live Turso connectivity + schema check."""
    from syte.turso_store import turso_debug_status

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, **(await turso_debug_status())}


@router.get("/agent_sessions")
async def api_agent_sessions(
    uuid: str = Query(..., description="Project UUID"),
    limit: int = Query(50, ge=1, le=500),
    resume: int = Query(0, ge=0, le=1),
    _token: dict = Depends(verify_api_token),
):
    """List durable Turso agent-session UUIDs for a project (newest first)."""
    from syte.agent_memory import project_memory_snapshot
    from syte.turso_store import list_sessions_for_project, turso_configured

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    memory = await project_memory_snapshot(uuid)
    base = {
        "ok": True,
        "uuid": uuid,
        "memory": memory,
        "resume_session": memory.get("resume_session"),
        "open_session": memory.get("open_session"),
        "last_work": memory.get("last_work"),
        "active_files": memory.get("active_files") or [],
        "latest_summary": memory.get("latest_summary"),
    }
    if resume:
        base["resume"] = 1
    configured = await turso_configured()
    sessions = await list_sessions_for_project(uuid, limit=limit)
    payload = {
        **base,
        "turso_configured": configured,
        "sessions": [
            {**s, "session_url": f"/api/agent_session/{s['id']}"} for s in sessions
        ],
    }
    if not configured:
        payload["message"] = (
            "Remote Turso is not configured — sessions are stored locally on this deployer. "
            "Set turso_database_url in Settings → AI for cross-host durability."
        )
    return payload


@router.get("/agent_session/{session_id}")
async def api_get_agent_session(
    session_id: str,
    since_id: int = Query(0, ge=0),
    uuid: str | None = None,
    project_id: str | None = None,
    _token: dict = Depends(verify_api_token),
):
    """Turso access route — fetch a durable agent activity session by UUID.

    This replaces the old activity SSE stream. Asking the agent something
    still happens over ``agent_communicate``/``agent_change`` (which return
    this session's id); poll this route by that id to see what happened.
    API tokens are host-global in this single-tenant service; pass ``uuid`` or
    ``project_id`` to additionally verify the session belongs to that project.
    """
    from syte.turso_store import get_session

    session = await get_session(session_id, since_id=since_id)
    if not session:
        _http_error(404, "not_found", "Agent session not found")
    expected_project_id = project_id or uuid
    if expected_project_id and str(session.get("project_id") or "") != expected_project_id:
        _http_error(403, "forbidden", "Agent session does not belong to the requested project")
    return {"ok": True, **session}


@router.get("/agent_dashboard")
async def api_agent_dashboard(_token: dict = Depends(verify_api_token)):
    from syte.agent_metrics import get_dashboard_metrics

    return {"ok": True, **(await get_dashboard_metrics())}


@router.post("/agent_test")
async def api_agent_test(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    result = await test_agent(body.uuid, source="api")
    if not result.get("ok"):
        _http_error(400, result.get("error") or "agent_test_failed", result.get("message") or "Agent test failed")
    return result


@router.post("/agent_communicate")
async def api_agent_communicate(body: AgentCommunicateRequest, _token: dict = Depends(verify_api_token)):
    result = await communicate_with_agent(
        body.uuid,
        body.message,
        model_profile=body.model_profile,
        thinking_level=body.thinking_level,
        source="api",
        improve_from_screenshot=bool(body.improve_from_screenshot),
        visual_analysis_id=body.visual_analysis_id,
    )
    if not result.get("ok"):
        _http_error(400, result.get("error") or "agent_communicate_failed", result.get("message") or "Communication failed")
    return result


@router.post("/agent_change")
async def api_agent_change(body: AgentChangeRequest, _token: dict = Depends(verify_api_token)):
    profile = body.model_profile or body.model_name
    result = await communicate_with_agent(
        body.uuid,
        body.message,
        model_profile=profile,
        thinking_level=body.thinking_level,
        source="sycord",
        background=True,
        improve_from_screenshot=bool(body.improve_from_screenshot),
        visual_analysis_id=body.visual_analysis_id,
        idempotency_key=body.idempotency_key,
    )
    if not result.get("ok"):
        _http_error(400, result.get("error") or "agent_change_failed", result.get("message") or "Change request failed")
    return {
        **result,
        "change_applied": None,
        "status": result.get("status", "accepted"),
    }


@router.post("/create_project")
async def api_create_project(body: CreateProjectRequest, _token: dict = Depends(verify_api_token)):
    """Create empty project (no git/files required). Returns uuid + how to call execute_command."""
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
    ws = await workspace_api.workspace_get(project["id"])
    return build_create_project_response(project, ws, message)


@router.post("/issue_deploy")
async def api_issue_deploy(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    project, message = await deployment.issue_deploy(body.uuid)
    if not project:
        _http_error(404, "not_found", message)
    return {
        "ok": True,
        "uuid": project["id"],
        "message": message,
        "description": "Git pull (if git_url) + docker build (npm run build inside Dockerfile) + container restart",
        "stream_url": f"/api/projects/{project['id']}/logs/stream?live=1",
    }


@router.post("/deploy_cancel")
async def api_deploy_cancel(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    ok, message = await deployment.cancel_deploy(body.uuid)
    return {"ok": ok, "uuid": body.uuid, "message": message}


@router.post("/start_service")
async def api_start_service(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    project, message = await deployment.start_service(body.uuid)
    if not project:
        _http_error(404, "not_found", message)
    return {"ok": True, "uuid": body.uuid, "message": message, "running": True}


@router.post("/stop_service")
async def api_stop_service(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    project, message = await deployment.stop_service(body.uuid)
    if not project:
        _http_error(404, "not_found", message)
    return {"ok": True, "uuid": body.uuid, "message": message, "running": False}


@router.post("/delete_project")
async def api_delete_project(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    ok, message = await deployment.remove_service(body.uuid)
    if not ok:
        _http_error(404, "not_found", message)
    return {"ok": True, "message": message}


@router.post("/start_preview")
async def api_start_preview(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    """Start fast dev preview (next dev / vite) with HMR — seconds, not minutes."""
    from syte.sycord import service as preview_service

    ok, message, meta = await preview_service.preview_start(body.uuid)
    if not ok:
        _http_error(400, "preview_failed", message)
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}


@router.post("/stop_preview")
async def api_stop_preview(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    from syte.sycord import service as preview_service

    ok, message, meta = await preview_service.preview_stop(body.uuid)
    if not ok:
        _http_error(400, "preview_failed", message)
    return {"ok": True, "uuid": body.uuid, "message": message, **(meta or {})}


@router.get("/preview_status")
async def api_preview_status(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    from syte.sycord import service as preview_service

    meta, message = await preview_service.preview_status(uuid)
    if not meta:
        _http_error(404, "not_found", message)
    return {"ok": True, **meta}
