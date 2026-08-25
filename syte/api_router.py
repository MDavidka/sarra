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
