import json
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from syte import __version__
from syte.config import settings
from syte.database import (
    get_project,
    get_setting,
    init_db,
    list_projects,
    set_setting,
    update_project,
)
from syte import deployment, process_manager
from syte.certificates import apply_proxy_config, set_gui_domain
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte import supervisor

STATIC_DIR = Path(__file__).resolve().parent / "static"
NO_CACHE = "no-cache, no-store, must-revalidate"


class VersionedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = NO_CACHE
        response.headers["Pragma"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    custom_ip = await get_setting("public_ip")
    if custom_ip:
        settings.public_ip = custom_ip
    custom_email = await get_setting("admin_email")
    if custom_email:
        settings.admin_email = custom_email
    gui_domain = await get_setting("gui_domain", "")
    if gui_domain:
        cleaned = normalize_domain(gui_domain)
        if cleaned != gui_domain:
            await set_setting("gui_domain", cleaned)
    await supervisor.startup()
    task = asyncio.create_task(supervisor.supervisor_loop())
    yield
    supervisor.stop_supervisor()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Syte", version=__version__, lifespan=lifespan)


class CreateServiceRequest(BaseModel):
    name: str
    git_url: str | None = None
    branch: str = "main"
    start_command: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    domain: str | None = None


class DomainRequest(BaseModel):
    domain: str
    email: str


class SettingsRequest(BaseModel):
    public_ip: str | None = None
    admin_email: str | None = None
    gui_domain: str | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    git_url: str | None = None
    branch: str | None = None
    start_command: str | None = None
    env_vars: dict[str, str] | None = None
    domain: str | None = None


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/system")
async def system_info():
    projects = await list_projects()
    ip = settings.resolved_public_ip
    gui_domain = await get_setting("gui_domain", "")
    direct = build_direct_url(ip, settings.port)
    return {
        "version": __version__,
        "public_ip": ip,
        "admin_email": settings.admin_email,
        "direct_url": direct,
        "gui_url": build_https_url(gui_domain) if gui_domain else direct,
        "domain_url": build_https_url(gui_domain) if gui_domain else "",
        "gui_domain": normalize_domain(gui_domain) if gui_domain else "",
        "workspaces_dir": str(settings.resolved_workspaces_dir),
        "service_count": len(projects),
    }


async def _gui_url() -> str:
    domain = await get_setting("gui_domain", "")
    if domain:
        return build_https_url(domain)
    return build_direct_url(settings.resolved_public_ip, settings.port)


@app.get("/api/settings")
async def get_settings():
    return {
        "public_ip": await get_setting("public_ip", settings.resolved_public_ip),
        "admin_email": await get_setting("admin_email", settings.admin_email),
        "gui_domain": await get_setting("gui_domain", ""),
        "version": __version__,
    }


@app.put("/api/settings")
async def save_settings(body: SettingsRequest):
    messages = []
    if body.public_ip is not None:
        await set_setting("public_ip", body.public_ip)
        settings.public_ip = body.public_ip
        messages.append(f"Public IP set to {body.public_ip}")

    if body.admin_email is not None:
        await set_setting("admin_email", body.admin_email)
        settings.admin_email = body.admin_email
        messages.append(f"Admin email set to {body.admin_email}")

    if body.gui_domain is not None:
        domain = normalize_domain(body.gui_domain)
        if domain:
            email = settings.admin_email
            if not email or "@" not in email or email.endswith("@localhost"):
                raise HTTPException(
                    400,
                    "A valid admin email is required before setting a GUI domain "
                    "(used for TLS certificate registration).",
                )
            await set_setting("gui_domain", domain)
            try:
                ok, msg = await set_gui_domain(domain, email)
            except Exception as exc:
                await set_setting("gui_domain", "")
                raise HTTPException(500, f"Failed to configure domain: {exc}") from exc
            if not ok:
                await set_setting("gui_domain", "")
                raise HTTPException(500, msg)
            messages.append(msg)
        else:
            await set_setting("gui_domain", "")
            ok, msg = await apply_proxy_config()
            messages.append("GUI domain removed." if ok else msg)
        return {"ok": True, "messages": messages, "gui_url": await _gui_url(),
                "direct_url": build_direct_url(settings.resolved_public_ip, settings.port),
                "domain_url": build_https_url(domain) if domain else ""}

    ok, msg = await apply_proxy_config()
    messages.append(msg)
    return {"ok": ok, "messages": messages}


@app.post("/api/system/update")
async def api_update_syte():
    """Pull newest Syte version and restart to apply changes."""
    ok, message = update_syte()
    if not ok:
        raise HTTPException(500, message)
    return {"ok": True, "message": message}


def _running(project: dict) -> bool:
    return process_manager.is_running(
        project["id"], project.get("deploy_type", "shell")
    )


@app.get("/api/projects")
async def api_list_projects():
    projects = await list_projects()
    enriched = []
    for p in projects:
        p = dict(p)
        p["running"] = _running(p)
        p["url"] = _project_url(p)
        p["env_vars"] = _parse_env(p.get("env_vars"))
        enriched.append(p)
    return enriched


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    project = dict(project)
    project["running"] = _running(project)
    project["url"] = _project_url(project)
    project["env_vars"] = _parse_env(project.get("env_vars"))
    return project


@app.post("/api/projects")
async def api_create_project(body: CreateServiceRequest):
    project, message = await deployment.deploy_service(
        name=body.name,
        git_url=body.git_url,
        branch=body.branch,
        start_command=body.start_command,
        env_vars=body.env_vars,
        domain=body.domain,
    )
    if not project:
        raise HTTPException(500, message)
    project = dict(project)
    project["running"] = _running(project)
    project["url"] = _project_url(project)
    project["env_vars"] = _parse_env(project.get("env_vars"))
    return {"project": project, "message": message}


@app.put("/api/projects/{project_id}")
async def api_update_project(project_id: str, body: UpdateProjectRequest):
    updates = body.model_dump(exclude_none=True)
    project = await update_project(project_id, updates)
    if not project:
        raise HTTPException(404, "Project not found")
    ok, msg = await apply_proxy_config()
    project = dict(project)
    project["running"] = _running(project)
    project["url"] = _project_url(project)
    return {"project": project, "message": msg}


@app.post("/api/projects/{project_id}/start")
async def api_start(project_id: str):
    project, message = await deployment.start_service(project_id)
    if not project:
        raise HTTPException(404, message)
    return {"project": _enrich(project), "message": message}


@app.post("/api/projects/{project_id}/stop")
async def api_stop(project_id: str):
    project, message = await deployment.stop_service(project_id)
    if not project:
        raise HTTPException(404, message)
    return {"project": _enrich(project), "message": message}


@app.post("/api/projects/{project_id}/update")
async def api_git_update(project_id: str):
    """Pull newest git version and restart app. Data is preserved on VM."""
    project, message = await deployment.update_service(project_id)
    if not project:
        raise HTTPException(404, message)
    return {"project": _enrich(project), "message": message}


@app.post("/api/projects/{project_id}/domain")
async def api_set_domain(project_id: str, body: DomainRequest):
    project, message = await deployment.set_custom_domain(
        project_id, body.domain, body.email
    )
    if not project:
        raise HTTPException(404, message)
    return {"project": _enrich(project), "message": message}


@app.delete("/api/projects/{project_id}")
async def api_delete(project_id: str):
    ok, message = await deployment.remove_service(project_id)
    if not ok:
        raise HTTPException(404, message)
    return {"ok": True, "message": message}


@app.get("/api/projects/{project_id}/logs")
async def api_logs(project_id: str, lines: int = 100):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "logs": process_manager.get_logs(
            project_id, lines, project.get("deploy_type", "shell")
        )
    }


def _parse_env(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _project_url(project: dict) -> str:
    if project.get("domain"):
        from syte.domain_utils import build_https_url
        return build_https_url(project["domain"])
    ip = settings.resolved_public_ip
    return f"http://{ip}:{project['port']}"


def _enrich(project: dict) -> dict:
    p = dict(project)
    p["running"] = _running(p)
    p["url"] = _project_url(p)
    p["env_vars"] = _parse_env(p.get("env_vars"))
    return p


@app.get("/")
async def index():
    html = (STATIC_DIR / "index.html").read_text()
    html = html.replace("__VERSION__", __version__)
    return HTMLResponse(
        html,
        headers={"Cache-Control": NO_CACHE, "Pragma": "no-cache"},
    )


app.mount("/static", VersionedStaticFiles(directory=STATIC_DIR), name="static")
