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
from syte import internal_api
from syte import workspace_api
from syte.log_stream import stream_agent_logs, stream_preview_logs, stream_project_logs
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
    preview_zone = await get_setting("preview_base_domain", "")
    if preview_zone:
        cleaned = normalize_domain(preview_zone)
        if cleaned != preview_zone:
            await set_setting("preview_base_domain", cleaned)
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

app.include_router(api_router.router, prefix="/api")
app.include_router(internal_api.router, prefix="/api/internal")

from syte.sycord.router import router as sycord_router

app.include_router(sycord_router, prefix="/sycord/api")


class CreateTokenRequest(BaseModel):
    name: str = "default"


class CreateServiceRequest(BaseModel):
    name: str
    git_url: str | None = None
    branch: str = "main"
    start_command: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    domain: str | None = None
    stack: str | None = "nextjs"


class DomainRequest(BaseModel):
    domain: str
    email: str


class SettingsRequest(BaseModel):
    public_ip: str | None = None
    admin_email: str | None = None
    gui_domain: str | None = None
    preview_base_domain: str | None = None
    cloudflare_api_token: str | None = None
    preview_wildcard_tls: str | None = None
    agent_default_model_profile: str | None = None
    agent_syra_nano_api_key: str | None = None
    agent_syra_base_api_key: str | None = None
    agent_syra_havy_api_key: str | None = None
    agent_max_count: int | None = None
    syra_internal_secret: str | None = None
    turso_database_url: str | None = None
    turso_auth_token: str | None = None


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


@app.get("/sycord/api", include_in_schema=False)
@app.get("/sycord/api/", include_in_schema=False)
async def sycord_api_documentation():
    """Sycord deployer API documentation."""
    html = (STATIC_DIR / "sycord-api-docs.html").read_text()
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
    from syte.system_stats import format_ram_label, get_system_stats

    projects = await list_projects()
    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    direct = build_direct_url(ip, settings.port)
    stats = get_system_stats()
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
        "cpu_percent": stats["cpu_percent"],
        "ram_used_mb": stats["ram_used_mb"],
        "ram_total_mb": stats["ram_total_mb"],
        "ram_percent": stats["ram_percent"],
        "ram_label": format_ram_label(stats["ram_used_mb"], stats["ram_total_mb"]),
        "load_dots": stats["load_dots"],
        "load_dots_max": stats["load_dots_max"],
        "overload_percent": stats["overload_percent"],
    }


async def _gui_url() -> str:
    domain = normalize_domain(await get_setting("gui_domain", ""))
    if domain:
        return build_https_url(domain)
    return build_direct_url(_resolved_ip(), settings.port)


@app.get("/api/settings")
async def get_settings():
    from syte.ai_providers import provider_catalog
    from syte.cloud_agent import bridge_settings
    from syte.certificates import cloudflare_tls_status
    from syte.preview_domains import resolve_preview_zone

    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    preview_base_domain = normalize_domain(await get_setting("preview_base_domain", ""))
    preview_zone = await resolve_preview_zone()
    cf_status = await cloudflare_tls_status()
    bridge = await bridge_settings()
    syra_secret_set = bool((await get_setting("syra_internal_secret", "")).strip())
    turso_database_url = (await get_setting("turso_database_url", "")).strip()
    turso_auth_token_set = bool((await get_setting("turso_auth_token", "")).strip())
    return {
        "public_ip": ip,
        "admin_email": await get_setting("admin_email", settings.admin_email),
        "gui_domain": gui_domain,
        "preview_base_domain": preview_base_domain,
        "preview_zone": preview_zone,
        "preview_host_pattern": f"preview{{a-z}}-{{app}}.{preview_zone}" if preview_zone else "",
        "preview_wildcard_tls": await get_setting("preview_wildcard_tls", "auto"),
        "cloudflare_api_token_set": cf_status["token_configured"],
        "cloudflare_tls": cf_status,
        "agent_default_model_profile": bridge["default_profile"],
        "agent_syra_nano_model": bridge["syra_nano_model"],
        "agent_syra_base_model": bridge["syra_base_model"],
        "agent_syra_havy_model": bridge["syra_havy_model"],
        "agent_syra_nano_api_key_set": bool(bridge["syra_nano_api_key"]),
        "agent_syra_base_api_key_set": bool(bridge["syra_base_api_key"]),
        "agent_syra_havy_api_key_set": bool(bridge["syra_havy_api_key"]),
        "ai_providers": provider_catalog(),
        "agent_max_count": int((await get_setting("agent_max_count", "0")).strip() or "0") or None,
        "syra_internal_secret_set": syra_secret_set,
        "turso_database_url": turso_database_url,
        "turso_auth_token_set": turso_auth_token_set,
        "turso_configured": bool(turso_database_url),
        "preview_dns_hint": (
            f"Point wildcard *.{preview_zone} A record to this server (grey cloud / DNS only)."
            if preview_zone
            else "Set preview base domain or GUI domain for HTTPS previews."
        ),
        "direct_url": build_direct_url(ip, settings.port),
        "domain_url": build_https_url(gui_domain) if gui_domain else "",
        "version": __version__,
    }


@app.put("/api/settings")
async def save_settings(body: SettingsRequest):
    from syte.certificates import cloudflare_tls_status

    messages = []
    proxy_updated = False

    if body.public_ip is not None:
        ip = body.public_ip.strip()
        if ip and not is_valid_ip(ip):
            raise HTTPException(400, "Public IP must be an IPv4 address (e.g. 152.89.245.113), not a domain.")
        await set_setting("public_ip", ip)
        settings.public_ip = ip
        messages.append(f"Public IP set to {ip}" if ip else "Public IP cleared (auto-detect)")
        proxy_updated = True

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
            "cloudflare_tls": await cloudflare_tls_status(),
        }

    if body.preview_base_domain is not None:
        zone = normalize_domain(body.preview_base_domain)
        await set_setting("preview_base_domain", zone)
        proxy_updated = True
        if zone:
            messages.append(
                f"Preview base domain set to {zone}. "
                f"Previews use preview{{letter}}-appname.{zone} — "
                f"ensure wildcard *.{zone} DNS points to this server."
            )
        else:
            messages.append(
                "Preview base domain cleared — previews use the same zone as the GUI domain."
            )

    if body.cloudflare_api_token is not None:
        token = body.cloudflare_api_token.strip()
        await set_setting("cloudflare_api_token", token)
        proxy_updated = True
        if token:
            messages.append(
                "Cloudflare API token saved — wildcard TLS via DNS challenge enabled for *.{zone}."
            )
        else:
            messages.append("Cloudflare API token cleared — wildcard TLS disabled.")

    if body.preview_wildcard_tls is not None:
        mode = body.preview_wildcard_tls.strip().lower() or "auto"
        await set_setting("preview_wildcard_tls", mode)
        proxy_updated = True
        messages.append(f"Preview wildcard TLS mode: {mode}")

    if body.agent_default_model_profile is not None:
        from syte.ai_providers import PROFILE_PROVIDERS

        profile = body.agent_default_model_profile.strip() or "syra-base"
        if profile not in PROFILE_PROVIDERS:
            raise HTTPException(400, f"Unknown model profile: {profile}")
        await set_setting("agent_default_model_profile", profile)
        messages.append(f"Default Syte cloud model profile: {profile}")
    if body.agent_syra_nano_api_key is not None:
        await set_setting("agent_syra_nano_api_key", body.agent_syra_nano_api_key.strip())
        messages.append(
            "syra-nano (Verted) API key saved."
            if body.agent_syra_nano_api_key.strip()
            else "syra-nano API key cleared."
        )
    if body.agent_syra_base_api_key is not None:
        await set_setting("agent_syra_base_api_key", body.agent_syra_base_api_key.strip())
        messages.append(
            "syra-base (DeepSeek) API key saved."
            if body.agent_syra_base_api_key.strip()
            else "syra-base API key cleared."
        )
    if body.agent_syra_havy_api_key is not None:
        await set_setting("agent_syra_havy_api_key", body.agent_syra_havy_api_key.strip())
        messages.append(
            "syra-havy (Verted) API key saved."
            if body.agent_syra_havy_api_key.strip()
            else "syra-havy API key cleared."
        )
    if body.agent_max_count is not None:
        count = max(1, int(body.agent_max_count))
        await set_setting("agent_max_count", str(count))
        messages.append(f"Maximum agents (MNOA): {count}")
    if body.syra_internal_secret is not None:
        await set_setting("syra_internal_secret", body.syra_internal_secret.strip())
        messages.append(
            "Syra internal secret saved."
            if body.syra_internal_secret.strip()
            else "Syra internal secret cleared."
        )
    if body.turso_database_url is not None or body.turso_auth_token is not None:
        from syte.turso_store import reset_client_cache

        if body.turso_database_url is not None:
            await set_setting("turso_database_url", body.turso_database_url.strip())
            messages.append(
                "Turso database URL saved."
                if body.turso_database_url.strip()
                else "Turso database URL cleared — agent sessions will not be persisted to Turso."
            )
        if body.turso_auth_token is not None:
            await set_setting("turso_auth_token", body.turso_auth_token.strip())
            messages.append(
                "Turso auth token saved."
                if body.turso_auth_token.strip()
                else "Turso auth token cleared."
            )
        # Drop any cached client so the next agent session picks up the new
        # connection details immediately instead of an out-of-date client.
        reset_client_cache()

    if proxy_updated or not messages:
        ok, msg = await apply_proxy_config()
        messages.append(msg)
    else:
        ok = True

    cf_status = await cloudflare_tls_status()
    if cf_status["token_configured"] and cf_status["hints"]:
        messages.extend(cf_status["hints"])

    return {"ok": ok, "messages": messages, "cloudflare_tls": cf_status}


@app.get("/api/system/update-info")
async def api_update_info():
    from syte.self_update import get_update_info

    return {"ok": True, **get_update_info()}


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
    from syte.preview_manager import ensure_preview_address

    projects = await list_projects()
    enriched = []
    for p in projects:
        p = await ensure_preview_address(dict(p))
        enriched.append(_enrich(p))
    return enriched


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str):
    from syte.preview_manager import ensure_preview_address

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    project = await ensure_preview_address(project)
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
        stack=body.stack,
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


@app.post("/api/projects/{project_id}/preview/start")
async def api_preview_start(project_id: str):
    from syte.preview_manager import start_preview
    ok, message, meta = await start_preview(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {"ok": True, "message": message, **meta}


@app.post("/api/projects/{project_id}/preview/stop")
async def api_preview_stop(project_id: str):
    from syte.preview_manager import get_preview_status, stop_preview_async

    await stop_preview_async(project_id)
    meta, _ = await get_preview_status(project_id)
    return {"ok": True, "message": "Preview stopped", **(meta or {})}


@app.get("/api/projects/{project_id}/agent")
async def api_agent_status_public(project_id: str, request: Request):
    from syte.cloud_agent import get_agent_status

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "ok": True,
        **(
            await get_agent_status(
                project_id,
                request_base=str(request.base_url).rstrip("/"),
                check_backend=False,
            )
        ),
    }


@app.post("/api/projects/{project_id}/agent/warm")
async def api_agent_warm_public(project_id: str):
    """Schedule the persistent Syte cloud runtime without blocking for startup."""
    from syte.cloud_agent import warm_agent

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await warm_agent(project_id, source="gui")
    return {
        **result,
        "status_url": f"/api/projects/{project_id}/agent",
        "sessions_url": f"/api/projects/{project_id}/agent/sessions",
    }


@app.post("/api/projects/{project_id}/agent/start")
async def api_agent_start_public(project_id: str, request: Request):
    from syte.cloud_agent import get_agent_status, start_agent

    ok, message, _meta = await start_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(
            await get_agent_status(
                project_id,
                request_base=str(request.base_url).rstrip("/"),
                check_backend=False,
            )
        ),
    }


@app.post("/api/projects/{project_id}/agent/stop")
async def api_agent_stop_public(project_id: str, request: Request):
    from syte.cloud_agent import get_agent_status, stop_agent

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    ok, message = await stop_agent(project_id)
    return {
        "ok": ok,
        "message": message,
        **(
            await get_agent_status(
                project_id,
                request_base=str(request.base_url).rstrip("/"),
                check_backend=False,
            )
        ),
    }


@app.post("/api/projects/{project_id}/agent/interrupt")
async def api_agent_interrupt_public(project_id: str, request: Request):
    """Cancel the active Syte cloud turn without discarding conversation history."""
    from syte.cloud_agent import get_agent_status, interrupt_agent

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    ok, message = await interrupt_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(
            await get_agent_status(
                project_id,
                request_base=str(request.base_url).rstrip("/"),
                check_backend=False,
            )
        ),
    }


@app.post("/api/projects/{project_id}/agent/restart")
async def api_agent_restart_public(project_id: str, request: Request):
    from syte.cloud_agent import get_agent_status, restart_agent

    ok, message, _meta = await restart_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(
            await get_agent_status(
                project_id,
                request_base=str(request.base_url).rstrip("/"),
                check_backend=False,
            )
        ),
    }


@app.get("/api/projects/{project_id}/agent/turso_sync")
async def api_agent_turso_sync_public(project_id: str):
    """Aggregate 'all messages saved to Turso' status for the brain indicator.

    ``all_saved: true`` -> green brain (every message in the current session
    was durably written to the shared Turso ``agent_message`` table).
    ``all_saved: false`` -> red brain (at least one message failed to sync,
    or Turso is unreachable for a message that was already appended
    locally).
    """
    from syte.cloud_agent import turso_message_sync_status

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "project_id": project_id, **(await turso_message_sync_status(project_id))}


@app.get("/api/projects/{project_id}/agent/logs")
async def api_agent_logs_public(project_id: str, lines: int = 200):
    from syte.cloud_agent import get_agent_logs

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "logs": get_agent_logs(project_id, max(1, min(lines, 2000)))}


@app.get("/api/projects/{project_id}/agent/logs/stream")
async def api_agent_logs_stream(project_id: str, request: Request, live: bool = False):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if key:
        await auth.verify_api_token_from_request(request)
    return StreamingResponse(
        stream_agent_logs(project_id, live_only=live),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/projects/{project_id}/agent/activity")
async def api_agent_activity_public(
    project_id: str,
    request: Request,
    since_id: int = 0,
    limit: int = 200,
    session: str = "",
):
    """Local SQLite activity snapshot (fast, always available; not durable across DB moves).

    For the durable, UUID-addressable record of a turn use the Turso session
    routes instead: ``GET /api/agent_session/{session_id}`` or
    ``GET /api/projects/{project_id}/agent/sessions`` to list recent session ids.
    """
    from syte.agent_activity import list_agent_events

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if key:
        await auth.verify_api_token_from_request(request)
    events = await list_agent_events(
        project_id,
        since_id=since_id,
        limit=limit,
        session=session or None,
    )
    return {
        "ok": True,
        "project_id": project_id,
        "events": events,
        "since_id": since_id,
        "session": session or None,
        "sessions_url": f"/api/projects/{project_id}/agent/sessions",
    }


@app.get("/api/projects/{project_id}/agent/sessions")
async def api_agent_sessions_public(project_id: str, limit: int = 50):
    """List durable Turso agent-session UUIDs for a project (newest first)."""
    from syte.turso_store import list_sessions_for_project, turso_configured

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not await turso_configured():
        return {
            "ok": True,
            "project_id": project_id,
            "turso_configured": False,
            "sessions": [],
            "message": "Turso is not configured — set turso_database_url in Settings -> AI tab.",
        }
    sessions = await list_sessions_for_project(project_id, limit=limit)
    return {
        "ok": True,
        "project_id": project_id,
        "turso_configured": True,
        "sessions": [
            {**s, "session_url": f"/api/agent_session/{s['id']}"} for s in sessions
        ],
    }


@app.get("/api/agent_session/{session_id}")
async def api_get_agent_session(session_id: str, since_id: int = 0):
    """Fetch a durable agent activity session by UUID from Turso.

    This is the Turso access route that replaces the old activity SSE stream.
    Asking the agent something still happens over the normal request/response
    API (``agent_communicate`` / ``agent_change`` / the GUI chat endpoint,
    which return this session's ``id``); to observe what happened, poll this
    route by that ``id`` instead of opening a streaming connection. Pass
    ``since_id`` to fetch only events recorded after a previously-seen event
    id (useful for polling a session that is still ``open``).
    """
    from syte.turso_store import get_session, turso_configured

    if not await turso_configured():
        raise HTTPException(
            503,
            "Turso is not configured — set turso_database_url (and turso_auth_token) "
            "in Settings -> AI tab before fetching agent sessions.",
        )
    session = await get_session(session_id, since_id=since_id)
    if not session:
        raise HTTPException(404, "Agent session not found")
    return {"ok": True, **session}


class AgentChatRequest(BaseModel):
    message: str
    model_profile: str | None = None


class AgentTestRequest(BaseModel):
    model_profile: str | None = None


class AgentAccessRequest(BaseModel):
    action: str
    url: str | None = None
    lines: int | None = None


class AgentAccessConfigRequest(BaseModel):
    custom_urls: list[str] = []


class AgentServiceRequest(BaseModel):
    action: str
    command: str | None = None
    cwd: str = "app"
    lines: int | None = None
    timeout: int | None = None


@app.get("/api/projects/{project_id}/agent/service")
async def api_agent_service_capabilities(project_id: str):
    from syte.agent_service import list_service_capabilities

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return await list_service_capabilities(project_id)


@app.post("/api/projects/{project_id}/agent/service")
async def api_agent_service_action(project_id: str, body: AgentServiceRequest):
    from syte.agent_activity import record_agent_event
    from syte.agent_service import run_service_action

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await run_service_action(
        project_id,
        body.action,
        command=body.command,
        cwd=body.cwd,
        lines=body.lines or 200,
        timeout=body.timeout or 300,
        source="agent",
    )
    detail = body.command or result.get("message") or body.action
    if result.get("output"):
        detail = str(result.get("output"))[:4000]
    elif result.get("logs"):
        detail = str(result.get("logs"))[:4000]
    await record_agent_event(
        project_id,
        "service_action",
        role="assistant",
        title=f"Service: {body.action}",
        detail=detail[:4000],
        payload={"action": body.action, "result": {k: result.get(k) for k in ("ok", "action", "exit_code")}},
        source="agent",
    )
    return result


@app.get("/api/projects/{project_id}/agent/access")
async def api_agent_access_capabilities(project_id: str):
    from syte.preview_access import list_access_capabilities

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return await list_access_capabilities(project_id)


@app.get("/api/projects/{project_id}/agent/access-config")
async def api_agent_access_config_get(project_id: str):
    from syte.agent_skills import read_access_config
    from syte.cloud_agent import agent_root

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, **(await read_access_config(project_id, agent_root(project_id)))}


@app.put("/api/projects/{project_id}/agent/access-config")
async def api_agent_access_config_put(project_id: str, body: AgentAccessConfigRequest):
    from syte.agent_skills import read_access_config, write_access_config
    from syte.cloud_agent import agent_root, write_agent_config

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    root = agent_root(project_id)
    path = await write_access_config(project_id, body.model_dump(), root)
    await write_agent_config(project)
    return {"ok": True, "path": str(path), **(await read_access_config(project_id, root))}


@app.post("/api/projects/{project_id}/agent/access")
async def api_agent_access_action(project_id: str, body: AgentAccessRequest):
    from syte.agent_activity import record_agent_event
    from syte.preview_access import run_access_action

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await run_access_action(
        project_id,
        body.action,
        url=body.url,
        lines=body.lines or 200,
    )
    if result.get("ok"):
        await record_agent_event(
            project_id,
            "service_action",
            role="assistant",
            title=f"Preview: {body.action}",
            detail=(body.url or result.get("preview_url") or body.action)[:4000],
            payload={"action": body.action, "access": True},
            source="gui",
        )
    return result


@app.get("/api/agent_dashboard")
async def api_agent_dashboard_gui():
    from syte.agent_metrics import get_dashboard_metrics

    return {"ok": True, **(await get_dashboard_metrics())}


@app.get("/api/projects/{project_id}/agent/debug")
async def api_agent_debug_gui(project_id: str, profile: str | None = None):
    from syte.agent_debug import build_ai_debug_report

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, **(await build_ai_debug_report(project_id, model_profile=profile))}


@app.post("/api/projects/{project_id}/agent/test")
async def api_agent_test_gui(project_id: str, body: AgentTestRequest | None = None):
    from syte.cloud_agent import test_agent

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    profile = body.model_profile if body else None
    return await test_agent(project_id, source="gui", model_profile=profile)


@app.post("/api/projects/{project_id}/agent/chat")
async def api_agent_chat_gui(project_id: str, body: AgentChatRequest, wait: bool = False):
    from syte.cloud_agent import communicate_with_agent

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not (body.message or "").strip():
        raise HTTPException(400, "Message cannot be empty")
    try:
        result = await communicate_with_agent(
            project_id,
            body.message.strip(),
            model_profile=body.model_profile,
            source="gui",
            background=not wait,
        )
    except Exception as exc:
        return {"ok": False, "error": "agent_communicate_failed", "message": str(exc)}
    if not result.get("ok"):
        return result
    return result


@app.get("/api/projects/{project_id}/preview/status")
async def api_preview_status(project_id: str, quick: bool = False):
    from syte.preview_manager import get_preview_status
    meta, message = await get_preview_status(project_id, quick=quick)
    if not meta:
        raise HTTPException(404, message)
    return {"ok": True, **meta}


@app.get("/api/projects/{project_id}/preview/iframe-check")
async def api_preview_iframe_check(project_id: str):
    """Iframe embed debug checklist for preview hosters."""
    from syte.preview_manager import preview_iframe_status

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, **await preview_iframe_status(project)}


@app.get("/api/projects/{project_id}/preview/logs/stream")
async def api_preview_logs_stream(project_id: str, request: Request, live: bool = False):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if key:
        await auth.verify_api_token_from_request(request)
    return StreamingResponse(
        stream_preview_logs(project_id, live_only=live),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    from syte.preview_manager import preview_meta
    from syte.project_enrich import enrich_ssl
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
    p.update(preview_meta(p))
    p["ssl"] = enrich_ssl(p)
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
