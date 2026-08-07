import json
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
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
from syte.litellm_config import LITELLM_PUBLIC_API_URL, LITELLM_PUBLIC_HOST
from syte.self_update import update_syte
from syte.new_feature_agent import run_new_feature_agent
from syte.settings_tabs import get_registered_tabs
from syte import auth
from syte.auth import (
    OPERATOR_SESSION_COOKIE,
    create_bootstrap_operator_session,
    operator_session_status,
    require_same_origin_if_present,
    revoke_operator_session,
    verify_operator_session_or_token,
)
from syte import api_router
from syte import internal_api
from syte import workspace_api
from syte.log_stream import stream_agent_logs, stream_preview_logs, stream_project_logs
from syte.rate_limit import RateLimitMiddleware
import logging

from syte import supervisor

logger = logging.getLogger("syte")

_SYRA_START_LOCK = asyncio.Lock()

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sycord.com",
        "https://www.sycord.com",
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8787",
        "http://127.0.0.1",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8787",
    ],
    allow_origin_regex=r"^https://([a-z0-9-]+\.)?sycord\.com$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a diagnosable JSON error instead of a bare text/plain 500.

    Starlette's default handler emits ``Internal Server Error`` as plain text,
    which the GUI can only render as "Request failed". Surfacing the exception
    type and message keeps operator actions debuggable from the browser.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "detail": {
                "error": "internal_error",
                "message": f"{type(exc).__name__}: {exc}".strip(),
                "path": request.url.path,
            },
        },
    )

app.include_router(api_router.router, prefix="/api")
app.include_router(internal_api.router, prefix="/api/internal")

from syte.sycord.router import router as sycord_router

app.include_router(sycord_router, prefix="/sycord/api")


class CreateTokenRequest(BaseModel):
    name: str = "default"


class OperatorSessionRequest(BaseModel):
    bootstrap_token: str


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
    custom_tls_host: str | None = None
    custom_tls_port: str | None = None
    nine_router_backend_port: str | None = None
    agent_default_model_profile: str | None = None
    agent_syra_nano_api_key: str | None = None
    agent_syra_havy_api_key: str | None = None
    agent_syra_ultra_api_key: str | None = None
    litellm_proxy_url: str | None = None
    litellm_database_url: str | None = None
    agent_max_count: int | None = None
    syra_internal_secret: str | None = None
    turso_database_url: str | None = None
    turso_auth_token: str | None = None


class SyraSecretsRequest(BaseModel):
    """Protected server-side LiteLLM credentials and Syra virtual key."""

    master_key: str | None = None
    salt_key: str | None = None
    agent_api_key: str | None = None


class ModelProviderSetupRequest(BaseModel):
    api_key: str = Field(min_length=1)


class ModelConfigurationRequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=200)
    provider: str = Field(default="9Router", min_length=1, max_length=100)
    thinking_levels: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    thinking_level: str = Field(default="medium", min_length=1, max_length=20)
    enabled: bool = True


class BulkModelConfigurationRequest(BaseModel):
    models: list[ModelConfigurationRequest] = Field(min_length=1, max_length=100)


class ModelPlaygroundRequest(BaseModel):
    model_profile: str = Field(min_length=1, max_length=240)
    prompt: str = Field(min_length=1, max_length=12000)


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


@app.get("/api/operator/session")
async def get_operator_session(request: Request):
    """Report whether this browser has an active operator session."""
    return JSONResponse(operator_session_status(request), headers={"Cache-Control": NO_CACHE})


@app.post("/api/operator/session")
async def start_operator_session(body: OperatorSessionRequest, request: Request):
    """Unlock the operator UI without ever returning the bootstrap key."""
    require_same_origin_if_present(request)
    session = create_bootstrap_operator_session(body.bootstrap_token)
    response = JSONResponse(
        {
            "ok": True,
            "csrf_token": session["csrf_token"],
            "expires_in": session["max_age"],
            "message": "Operator session unlocked.",
        },
        headers={"Cache-Control": NO_CACHE},
    )
    response.set_cookie(
        key=OPERATOR_SESSION_COOKIE,
        value=str(session["session_id"]),
        max_age=int(session["max_age"]),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@app.delete("/api/operator/session")
async def end_operator_session(
    request: Request,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """End the current browser's operator session."""
    revoke_operator_session(request)
    response = JSONResponse({"ok": True, "message": "Operator session locked."}, headers={"Cache-Control": NO_CACHE})
    response.delete_cookie(
        key=OPERATOR_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@app.get("/api/tokens")
async def list_tokens(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    tokens = await auth.list_tokens()
    return {"tokens": tokens}


@app.post("/api/tokens")
async def create_token(
    body: CreateTokenRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
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
async def revoke_token(
    token_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
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
    from syte.resource_monitor import get_resource_monitor_snapshot
    from syte.system_stats import format_ram_label, get_system_stats

    projects = await list_projects()
    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    direct = build_direct_url(ip, settings.port)
    stats = get_system_stats()
    try:
        resource_monitor = await get_resource_monitor_snapshot()
    except Exception:
        resource_monitor = {"ok": False, "sample_ms": 0, "services": []}
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
        "resource_monitor": resource_monitor,
    }


async def _gui_url() -> str:
    domain = normalize_domain(await get_setting("gui_domain", ""))
    if domain:
        return build_https_url(domain)
    return build_direct_url(_resolved_ip(), settings.port)


@app.get("/api/settings")
async def get_settings():
    from syte.ai_providers import provider_catalog
    from syte.cloud_agent import bridge_settings, provider_key_status
    from syte.certificates import cloudflare_tls_status
    from syte.preview_domains import resolve_preview_zone

    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    preview_base_domain = normalize_domain(await get_setting("preview_base_domain", ""))
    preview_zone = await resolve_preview_zone()
    cf_status = await cloudflare_tls_status()
    bridge = await bridge_settings()
    key_status = await provider_key_status()
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
        "custom_tls_host": await get_setting("custom_tls_host", ""),
        "custom_tls_port": await get_setting("custom_tls_port", ""),
        "nine_router_backend_port": await get_setting("nine_router_backend_port", ""),
        "cloudflare_api_token_set": cf_status["token_configured"],
        "cloudflare_tls": cf_status,
        "agent_default_model_profile": bridge["default_profile"],
        "agent_syra_nano_model": bridge["syra_nano_model"],
        "agent_syra_havy_model": bridge["syra_havy_model"],
        "agent_syra_ultra_model": bridge["syra_ultra_model"],
        "agent_builder_profile": bridge.get("builder_profile") or bridge["default_profile"],
        "agent_thinker_profile": bridge.get("thinker_profile"),
        "agent_syra_nano_api_key_set": bool(bridge["syra_nano_api_key"]),
        "agent_syra_havy_api_key_set": bool(bridge["syra_havy_api_key"]),
        "agent_syra_ultra_api_key_set": bool(bridge["syra_ultra_api_key"]),
        "agent_litellm_api_key_set": bool((await get_setting("agent_litellm_api_key", "")).strip()),
        "agent_9router_api_key_set": bool((await get_setting("agent_9router_api_key", "")).strip()),
        "agent_9router_model_name": (await get_setting("agent_9router_model_name", "")).strip(),
        "agent_9router_enabled": (await get_setting("agent_9router_enabled", "0")).strip() == "1",
        "litellm_proxy_url": LITELLM_PUBLIC_API_URL,
        "litellm_public_api_url": LITELLM_PUBLIC_API_URL,
        "litellm_master_key_set": bool((await get_setting("litellm_master_key", "")).strip()),
        "litellm_salt_key_set": bool((await get_setting("litellm_salt_key", "")).strip()),
        "litellm_database_url_set": bool((await get_setting("litellm_database_url", "")).strip()),
        "ai_providers": provider_catalog(),
        "provider_keys": key_status,
        "provider_envs": [
            {"name": "SYRA_NANO_API_KEY", "set": bool(os.environ.get("SYRA_NANO_API_KEY"))},
            {"name": "SYRA_ULTRA_API_KEY", "set": bool(os.environ.get("SYRA_ULTRA_API_KEY"))},
            {"name": "SYRA_HAVY_API_KEY", "set": bool(os.environ.get("SYRA_HAVY_API_KEY"))},
        ],
        "syra_secret_set": syra_secret_set,
        "turso_database_url_set": bool(turso_database_url),
        "turso_auth_token_set": turso_auth_token_set,
        "gui_url": await _gui_url(),
    }
