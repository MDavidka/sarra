"""Syte external API (token-authenticated) — for AI agents and automation."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from syte import deployment, process_manager
from syte.api_responses import build_create_project_response
from syte.auth import verify_api_token
from syte.continue_agent import (
    communicate_with_agent,
    get_agent_logs,
    get_agent_status,
    restart_agent,
    start_agent,
    stop_agent,
    test_agent,
    update_agent_settings,
)
from syte.certificates import apply_proxy_config
from syte.config import settings
from syte.database import get_project, get_setting
from syte.domain_utils import build_direct_url, normalize_domain
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
    model_profile: str | None = Field(None, description="syra-nano | syra-base | syra-havy")


class AgentChangeRequest(BaseModel):
    uuid: str
    message: str = Field(..., description="Change request from sycord.com user")
    model_profile: str | None = Field(None, description="Model profile alias (syra-nano/base/havy)")
    model_name: str | None = Field(None, description="Alias for model_profile from sycord.com")


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
    content = await file.read()
    try:
        ok, message = await workspace_api.upload_file(uuid, path, content)
    except ValueError as exc:
        _http_error(400, "invalid_path", str(exc))
    if not ok:
        _http_error(400, "upload_failed", message)
    return {"ok": True, "message": message, "path": path, "bytes": len(content)}


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


@router.post("/agent_stop")
async def api_agent_stop(body: UuidRequest, request: Request, _token: dict = Depends(verify_api_token)):
    project = await get_project(body.uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    ok, message = await stop_agent(body.uuid)
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
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_activity import list_agent_events

    project = await get_project(uuid)
    if not project:
        _http_error(404, "not_found", "Project not found")
    events = await list_agent_events(uuid, since_id=since_id, limit=limit)
    return {
        "ok": True,
        "uuid": uuid,
        "events": events,
        "since_id": since_id,
        "stream_url": f"/api/projects/{uuid}/agent/activity/stream?live=1",
    }


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
        source="api",
    )
    if not result.get("ok"):
        _http_error(400, result.get("error") or "agent_communicate_failed", result.get("message") or "Communication failed")
    return result


@router.post("/agent_change")
async def api_agent_change(body: AgentChangeRequest, _token: dict = Depends(verify_api_token)):
    from syte.agent_activity import record_agent_event

    profile = body.model_profile or body.model_name
    await record_agent_event(
        body.uuid,
        "request_started",
        role="user",
        title="User",
        detail=body.message[:4000],
        payload={"content": body.message, "model_profile": profile},
        source="sycord",
    )
    result = await communicate_with_agent(
        body.uuid,
        body.message,
        model_profile=profile,
        source="sycord",
        emit_request_started=False,
    )
    if not result.get("ok"):
        _http_error(400, result.get("error") or "agent_change_failed", result.get("message") or "Change request failed")
    return {
        **result,
        "change_applied": bool(result.get("reply")),
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
    from syte.preview_manager import start_preview

    ok, message, meta = await start_preview(body.uuid)
    if not ok:
        _http_error(400, "preview_failed", message)
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}


@router.post("/stop_preview")
async def api_stop_preview(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    from syte.preview_manager import get_preview_status, stop_preview_async

    await stop_preview_async(body.uuid)
    meta, _ = await get_preview_status(body.uuid)
    return {"ok": True, "uuid": body.uuid, "message": "Preview stopped", **(meta or {})}


@router.get("/preview_status")
async def api_preview_status(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    from syte.preview_manager import get_preview_status

    meta, message = await get_preview_status(uuid)
    if not meta:
        _http_error(404, "not_found", message)
    return {"ok": True, **meta}
