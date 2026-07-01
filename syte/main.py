import json
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
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
from syte.domain_utils import build_direct_url, build_https_url, is_valid_ip, normalize_domain
from syte.self_update import update_syte
from syte import auth
from syte import api_router
from syte import workspace_api
from syte.log_stream import stream_project_logs
import logging

from syte import supervisor

logger = logging.getLogger("syte")

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
    stored_ip = await get_setting("public_ip", "")
    if stored_ip and not is_valid_ip(stored_ip):
        await set_setting("public_ip", "")
        settings.public_ip = ""
    try:
        await supervisor.startup()
    except Exception:
        logger.exception("Supervisor startup failed — GUI will still start")
    task = asyncio.create_task(supervisor.supervisor_loop())
    yield
    supervisor.stop_supervisor()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Syte", version=__version__, lifespan=lifespan, docs_url="/openapi", redoc_url=None)

app.include_router(api_router.router)


class CreateTokenRequest(BaseModel):
    name: str = "default"


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


@app.get("/api/ai.json", include_in_schema=False)
async def api_ai_spec(request: Request):
    """Machine-readable API spec for AI agents."""
    from syte.ai_spec import build_ai_spec
    base = str(request.base_url).rstrip("/")
    return build_ai_spec(base)


@app.get("/api", include_in_schema=False)
@app.get("/api/", include_in_schema=False)
async def api_documentation():
    """API reference documentation page."""
    html = (STATIC_DIR / "api-docs.html").read_text()
    html = html.replace("__VERSION__", __version__)
    return HTMLResponse(html, headers={"Cache-Control": NO_CACHE})


@app.get("/api/tokens")
async def list_tokens():
    tokens = await auth.list_tokens()
    return {"tokens": tokens}


@app.post("/api/tokens")
async def create_token(body: CreateTokenRequest):
    row = await auth.create_token(body.name)
    return {
        "ok": True,
        "token": row.pop("token"),
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "message": "Save this token now — it will not be shown again.",
    }


@app.delete("/api/tokens/{token_id}")
async def revoke_token(token_id: str):
    ok = await auth.revoke_token(token_id)
    if not ok:
        raise HTTPException(404, "Token not found")
    return {"ok": True, "message": "Token revoked"}


def _resolved_ip() -> str:
    stored = settings.public_ip
    if stored and is_valid_ip(stored):
        return stored
    stored_db = ""  # resolved at call time via settings after init
    return _detect_ip()


def _detect_ip() -> str:
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if is_valid_ip(ip) else "127.0.0.1"
    except OSError:
        return "127.0.0.1"


@app.get("/api/system")
async def system_info():
    projects = await list_projects()
    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    direct = build_direct_url(ip, settings.port)
    return {
        "version": __version__,
        "public_ip": ip,
        "admin_email": settings.admin_email,
        "direct_url": direct,
        "gui_url": build_https_url(gui_domain) if gui_domain else direct,
        "domain_url": build_https_url(gui_domain) if gui_domain else "",
        "gui_domain": gui_domain,
        "workspaces_dir": str(settings.resolved_workspaces_dir),
        "service_count": len(projects),
    }


async def _gui_url() -> str:
    domain = normalize_domain(await get_setting("gui_domain", ""))
    if domain:
        return build_https_url(domain)
    return build_direct_url(_resolved_ip(), settings.port)


@app.get("/api/settings")
async def get_settings():
    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    return {
        "public_ip": ip,
        "admin_email": await get_setting("admin_email", settings.admin_email),
        "gui_domain": gui_domain,
        "direct_url": build_direct_url(ip, settings.port),
        "domain_url": build_https_url(gui_domain) if gui_domain else "",
        "version": __version__,
    }


@app.put("/api/settings")
async def save_settings(body: SettingsRequest):
    messages = []
    if body.public_ip is not None:
        ip = body.public_ip.strip()
        if ip and not is_valid_ip(ip):
            raise HTTPException(400, "Public IP must be an IPv4 address (e.g. 152.89.245.113), not a domain.")
        await set_setting("public_ip", ip)
        settings.public_ip = ip
        messages.append(f"Public IP set to {ip}" if ip else "Public IP cleared (auto-detect)")

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
        return {
            "ok": True,
            "messages": messages,
            "gui_url": await _gui_url(),
            "direct_url": build_direct_url(_resolved_ip(), settings.port),
            "domain_url": build_https_url(domain) if domain else "",
        }

    ok, msg = await apply_proxy_config()
    messages.append(msg)
    return {"ok": ok, "messages": messages}


@app.post("/api/system/update")
async def api_update_syte():
    """Pull newest Syte version and restart to apply changes."""
    try:
        ok, message = update_syte()
    except Exception as exc:
        logger.exception("Syte update failed")
        raise HTTPException(500, f"Update failed: {exc}") from exc
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
    return [_enrich(dict(p)) for p in projects]


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return _enrich(project)


@app.post("/api/projects")
async def api_create_project(body: CreateServiceRequest):
    project, message = await deployment.begin_deploy_service(
        name=body.name,
        git_url=body.git_url,
        branch=body.branch,
        start_command=body.start_command,
        env_vars=body.env_vars,
        domain=body.domain,
    )
    if not project:
        raise HTTPException(500, message)
    project = _enrich(project)
    return {
        "project": project,
        "message": message,
        "stream_url": f"/api/projects/{project['id']}/logs/stream",
    }


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
async def api_logs(project_id: str, lines: int = 500):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "logs": process_manager.get_logs(
            project_id, lines, project.get("deploy_type", "shell")
        )
    }


@app.get("/api/projects/{project_id}/workspace/files")
async def api_workspace_files(project_id: str, path: str = ""):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    try:
        files = await workspace_api.list_workspace_files(project_id, path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"uuid": project_id, "path": path or "/", "files": files}


@app.get("/api/projects/{project_id}/logs/stream")
async def api_logs_stream(project_id: str, request: Request, live: bool = False):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if key:
        await auth.verify_api_token_from_request(request)
    return StreamingResponse(
        stream_project_logs(
            project_id,
            project.get("deploy_type", "shell"),
            live_only=live,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/projects/{project_id}/deploy")
async def api_issue_deploy(project_id: str):
    project, message = await deployment.issue_deploy(project_id)
    if not project:
        raise HTTPException(404, message)
    return {
        "project": _enrich(project),
        "message": message,
        "stream_url": f"/api/projects/{project_id}/logs/stream?live=1",
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
    from syte.workspace import ensure_workspace, workspace_path

    p = dict(project)
    p["running"] = _running(p)
    p["url"] = _project_url(p)
    p["env_vars"] = _parse_env(p.get("env_vars"))
    ensure_workspace(p["id"])
    ws = workspace_path(p["id"])
    p["workspace_path"] = str(ws)
    p["app_path"] = str(ws / "app")
    p["data_path"] = str(ws / "data")
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
