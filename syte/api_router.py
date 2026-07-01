"""Syte external API (token-authenticated)."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from syte import deployment
from syte.auth import verify_api_token
from syte.database import get_project
from syte import workspace_api

router = APIRouter(tags=["Syte API"])


class ExecuteCommandRequest(BaseModel):
    uuid: str = Field(..., description="Project/workspace UUID")
    command: str = Field(..., description="Shell command to run inside workspace")
    cwd: str = Field("app", description="Relative working directory (default: app)")
    timeout: int = Field(120, ge=1, le=600)


class DeleteFileRequest(BaseModel):
    uuid: str = Field(..., description="Project/workspace UUID")
    path: str = Field(..., description="Relative file path inside workspace")


class CreateProjectRequest(BaseModel):
    name: str = Field(..., description="Human-readable project name")
    uuid: str | None = Field(None, description="Optional custom project UUID")
    git_url: str | None = Field(None, description="Git repository URL")
    git_provider: str | None = Field(
        None,
        description="Optional git host prefix, e.g. github.com/user/repo.git",
    )
    branch: str = Field("main", description="Git branch")
    start_command: str | None = Field(None, description="Shell start command (if no Dockerfile)")
    domain: str | None = Field(None, description="Custom HTTPS domain")
    env_vars: dict[str, str] = Field(default_factory=dict)


class IssueDeployRequest(BaseModel):
    uuid: str = Field(..., description="Project UUID to deploy or redeploy")


@router.get("/workspace_list")
async def api_workspace_list(_token: dict = Depends(verify_api_token)):
    """List all workspaces/projects."""
    workspaces = await workspace_api.workspace_list()
    return {"ok": True, "workspaces": workspaces}


@router.post("/execute_command")
async def api_execute_command(
    body: ExecuteCommandRequest,
    _token: dict = Depends(verify_api_token),
):
    """Run a shell command inside a project workspace."""
    code, output = await workspace_api.execute_command(
        body.uuid, body.command, body.cwd, body.timeout
    )
    return {
        "ok": code == 0,
        "exit_code": code,
        "output": output,
    }


@router.post("/delete_file")
async def api_delete_file(
    body: DeleteFileRequest,
    _token: dict = Depends(verify_api_token),
):
    """Delete a file inside a workspace (path must be relative)."""
    try:
        ok, message = await workspace_api.delete_file(body.uuid, body.path)
    except ValueError as exc:
        raise HTTPException(400, {"error": "invalid_path", "message": str(exc)}) from exc
    if not ok:
        raise HTTPException(404, {"error": "delete_failed", "message": message})
    return {"ok": True, "message": message}


@router.post("/upload_file")
async def api_upload_file(
    uuid: str = Form(...),
    path: str = Form(...),
    file: UploadFile = File(...),
    _token: dict = Depends(verify_api_token),
):
    """Upload a file into a workspace."""
    content = await file.read()
    try:
        ok, message = await workspace_api.upload_file(uuid, path, content)
    except ValueError as exc:
        raise HTTPException(400, {"error": "invalid_path", "message": str(exc)}) from exc
    if not ok:
        raise HTTPException(400, {"error": "upload_failed", "message": message})
    return {"ok": True, "message": message, "bytes": len(content)}


@router.post("/create_project")
async def api_create_project(
    body: CreateProjectRequest,
    _token: dict = Depends(verify_api_token),
):
    """Create a project and start deployment (async — stream logs separately)."""
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
        raise HTTPException(400, {"error": "create_failed", "message": message})
    return {
        "ok": True,
        "uuid": project["id"],
        "name": project["name"],
        "port": project["port"],
        "message": message,
        "stream_url": f"/api/projects/{project['id']}/logs/stream",
    }


@router.post("/issue_deploy")
async def api_issue_deploy(
    body: IssueDeployRequest,
    _token: dict = Depends(verify_api_token),
):
    """Trigger deploy/redeploy for an existing project."""
    project, message = await deployment.issue_deploy(body.uuid)
    if not project:
        raise HTTPException(404, {"error": "not_found", "message": message})
    return {
        "ok": True,
        "uuid": project["id"],
        "message": message,
        "stream_url": f"/api/projects/{project['id']}/logs/stream",
    }
