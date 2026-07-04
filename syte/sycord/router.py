"""Sycord API routes — /sycord/api/* for external Sycord website integration."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from syte.auth import verify_api_token
from syte.database import get_project
from syte.domain_utils import build_https_url, normalize_domain
from syte.sycord import service
from syte.sycord.scaffold import STACKS
from syte.sycord.integration_guide import build_backend_integration
from syte.sycord.spec import build_sycord_spec
from syte.workspace import workspace_path

router = APIRouter(tags=["Sycord API"])


def _err(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"error": code, "message": message})


class ProjectConnectRequest(BaseModel):
    name: str = Field(..., description="Project name — used for subdomain slug")
    stack: str = Field("nextjs", description="nextjs | python | javascript | html5")
    uuid: str | None = Field(None, description="Optional custom Syte project UUID — save if you provide your own")
    env_vars: dict[str, str] = Field(default_factory=dict)


class UuidBody(BaseModel):
    uuid: str = Field(..., description="Syte project UUID from project_connect")


class DomainBody(BaseModel):
    uuid: str = Field(..., description="Syte project UUID from project_connect")
    domain: str = Field(..., description="Production hostname e.g. myapp.sycord.site")


def _project_record(project: dict) -> dict:
    domain = normalize_domain(project.get("domain") or "")
    return {
        "uuid": project["id"],
        "name": project["name"],
        "domain": domain or None,
        "url": build_https_url(domain) if domain else None,
        "stack": service.project_stack(project),
        "workspace_path": str(workspace_path(project["id"])),
        "app_path": str(workspace_path(project["id"]) / "app"),
        "status": project.get("status"),
        "port": project.get("port"),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
    }


def _persist_block(project_id: str) -> dict:
    return {
        "save_uuid": True,
        "uuid": project_id,
        "instruction": (
            "Save uuid in your application database before any other Sycord API call. "
            "Required for upload, issue_deployment, container_get, and domain."
        ),
        "endpoints_using_uuid": [
            "POST /sycord/api/upload — form field uuid",
            "POST /sycord/api/issue_deployment — JSON body.uuid",
            "GET /sycord/api/container_get?uuid=",
            "POST /sycord/api/domain — JSON body.uuid",
        ],
    }


def _project_urls(project: dict) -> dict:
    return _project_record(project)


@router.get("/spec.json", include_in_schema=False)
async def sycord_spec():
    return build_sycord_spec()


@router.get("/integration.json", include_in_schema=False)
async def sycord_integration(request: Request):
    """Step-by-step backend integration: what to call, what JSON you get, what to save."""
    base = str(request.base_url).rstrip("/")
    return build_backend_integration(base)


@router.post("/project_connect")
async def api_project_connect(
    body: ProjectConnectRequest,
    _token: dict = Depends(verify_api_token),
):
    """
    Connect a Sycord project to Syte: create workspace, scaffold stack, assign subdomain.
    Example subdomain: testproject.sycord.site
    """
    if body.stack.lower() not in STACKS:
        _err(400, "invalid_stack", f"stack must be one of: {', '.join(STACKS)}")
    project, message = await service.project_connect(
        body.name,
        stack=body.stack,
        env_vars=body.env_vars,
        project_uuid=body.uuid,
    )
    if not project:
        _err(400, "connect_failed", message)
    base_zone = await service.resolve_base_zone()
    record = _project_record(project)
    project_id = record["uuid"]
    return {
        "ok": True,
        "uuid": project_id,
        "message": message,
        "persist": _persist_block(project_id),
        "project": record,
        "subdomain_pattern": f"{{slug}}.{base_zone}",
        "next_steps": {
            "save_uuid": project_id,
            "upload": "POST /sycord/api/upload",
            "deploy": "POST /sycord/api/issue_deployment",
            "container": f"GET /sycord/api/container_get?uuid={project_id}",
        },
    }


@router.get("/container_get")
async def api_container_get(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Docker container status and URLs for a connected project."""
    payload = await service.container_get_async(uuid)
    if not payload:
        _err(404, "not_found", "Project not found")
    return {"ok": True, **payload}


@router.post("/upload")
async def api_upload(
    uuid: str = Form(...),
    path: str = Form(..., description="Relative path under workspace, e.g. app/src/page.tsx"),
    file: UploadFile = File(...),
    _token: dict = Depends(verify_api_token),
):
    """Upload a file into the project workspace (multipart)."""
    content = await file.read()
    ok, message = await service.upload_file(uuid, path, content)
    if not ok:
        _err(400, "upload_failed", message)
    return {"ok": True, "uuid": uuid, "path": path, "bytes": len(content), "message": message}


@router.post("/domain")
async def api_domain(body: DomainBody, _token: dict = Depends(verify_api_token)):
    """Set or update production HTTPS domain (Caddy auto TLS)."""
    project, message = await service.set_domain(body.uuid, body.domain)
    if not project:
        _err(404, "not_found", message)
    return {
        "ok": True,
        "message": message,
        "uuid": body.uuid,
        "project": _project_record(project),
    }


@router.post("/issue_deployment")
async def api_issue_deployment(body: UuidBody, _token: dict = Depends(verify_api_token)):
    """Build and deploy project (docker build + container start)."""
    project, message = await service.issue_deployment(body.uuid)
    if not project:
        _err(404, "not_found", message)
    return {
        "ok": True,
        "message": message,
        "uuid": body.uuid,
        "stream_url": f"/api/projects/{body.uuid}/logs/stream?live=1",
        "status": project.get("status"),
    }
