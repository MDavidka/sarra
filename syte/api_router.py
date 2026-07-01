"""Syte external API (token-authenticated) — for AI agents and automation."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from syte import deployment, process_manager
from syte.auth import verify_api_token
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
    name: str
    uuid: str | None = None
    git_url: str | None = None
    git_provider: str | None = Field(
        None,
        description="Shorthand: github.com/user/repo.git → https://github.com/user/repo.git",
    )
    branch: str = "main"
    start_command: str | None = None
    domain: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)


def _http_error(status: int, error: str, message: str):
    raise HTTPException(status, detail={"error": error, "message": message})


@router.get("/server_info")
async def api_server_info(_token: dict = Depends(verify_api_token)):
    """Server metadata useful for AI (public IP, version, base URLs)."""
    from syte import __version__
    ip = settings.resolved_public_ip
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    return {
        "ok": True,
        "version": __version__,
        "public_ip": ip,
        "gui_port": settings.port,
        "direct_url": build_direct_url(ip, settings.port),
        "gui_domain": gui_domain,
        "api_base": "/api",
        "docs_url": "/api/",
        "ai_spec_url": "/api/ai.json",
        "workspaces_dir": str(settings.resolved_workspaces_dir),
    }


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
    """Run any custom shell command in the workspace (npm, yarn, mkdir, ls, etc.)."""
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


@router.post("/create_project")
async def api_create_project(body: CreateProjectRequest, _token: dict = Depends(verify_api_token)):
    project, message = await deployment.begin_deploy_service(
        name=body.name,
        git_url=body.git_url,
        branch=body.branch,
        start_command=body.start_command,
        env_vars=body.env_vars,
        domain=body.domain,
        git_provider=body.git_provider,
        project_uuid=body.uuid,
    )
    if not project:
        _http_error(400, "create_failed", message)
    ws = await workspace_api.workspace_get(project["id"])
    return {
        "ok": True,
        "uuid": project["id"],
        "name": project["name"],
        "port": project["port"],
        "message": message,
        "workspace": ws,
        "stream_url": f"/api/projects/{project['id']}/logs/stream?live=1",
    }


@router.post("/issue_deploy")
async def api_issue_deploy(body: UuidRequest, _token: dict = Depends(verify_api_token)):
    project, message = await deployment.issue_deploy(body.uuid)
    if not project:
        _http_error(404, "not_found", message)
    return {
        "ok": True,
        "uuid": project["id"],
        "message": message,
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
