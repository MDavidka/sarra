import json
import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx

from syte import __version__
from syte.config import settings
from syte.database import (
    create_deployment_run,
    get_project,
    get_setting,
    init_db,
    list_deployment_runs,
    list_projects,
    set_setting,
    update_project,
    count_operator_accounts,
    create_operator_account,
    get_operator_account,
    get_operator_account_by_email,
    update_operator_account,
)
from syte import deployment, process_manager
from syte.certificates import apply_proxy_config, set_gui_domain
from syte.caddy_routes import NINE_ROUTER_PUBLIC_HOST
from syte.domain_utils import build_direct_url, build_https_url, is_valid_ip, normalize_domain
from syte.litellm_config import LITELLM_PUBLIC_API_URL, LITELLM_PUBLIC_HOST
from syte.self_update import update_syte
from syte.new_feature_agent import run_new_feature_agent
from syte.settings_tabs import get_registered_tabs
from syte import auth
from syte.auth import (
    OPERATOR_SESSION_COOKIE,
    authenticate_operator_account,
    create_account_operator_session,
    create_bootstrap_operator_session,
    hash_password,
    operator_session_status,
    require_same_origin_if_present,
    revoke_operator_session,
    verify_operator_session_or_token,
)
from syte import api_router
from syte import internal_api
from syte import workspace_api
from syte import platform_api
from syte.platform.backup_scheduler import backup_scheduler_loop
from syte.platform.store import ensure_bootstrap, init_platform_db
from syte.log_stream import stream_agent_logs, stream_preview_logs, stream_project_logs
from syte.rate_limit import RateLimitMiddleware
import logging

from syte import supervisor

logger = logging.getLogger("syte")

_SYRA_START_LOCK = asyncio.Lock()
_ROUTER_START_LOCK = asyncio.Lock()

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
    await init_platform_db()
    await ensure_bootstrap()
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
    backup_stop = asyncio.Event()
    backup_task = asyncio.create_task(backup_scheduler_loop(backup_stop))
    yield
    backup_stop.set()
    supervisor.stop_supervisor()
    task.cancel()
    try:
        await backup_task
    except asyncio.CancelledError:
        backup_task.cancel()
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
app.include_router(platform_api.router, prefix="/api")
app.include_router(internal_api.router, prefix="/api/internal")

from syte.sycord.router import router as sycord_router

app.include_router(sycord_router, prefix="/sycord/api")


class CreateTokenRequest(BaseModel):
    name: str = "default"


class OperatorSessionRequest(BaseModel):
    bootstrap_token: str


class AccountLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class FirstAccountRequest(AccountLoginRequest):
    display_name: str = Field(default="", max_length=120)


class AccountProfileRequest(BaseModel):
    display_name: str = Field(default="", max_length=120)
    avatar_icon: str = Field(default="user", max_length=40)


class CreateServiceRequest(BaseModel):
    name: str
    git_url: str | None = None
    branch: str = "main"
    start_command: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    domain: str | None = None
    stack: str | None = "nextjs"


class ProjectRepositoryImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    git_url: str = Field(min_length=8, max_length=2048)
    branch: str = Field(default="main", min_length=1, max_length=255)
    base_directory: str = Field(default="/", max_length=255)


class GitHubConnectedImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    repository: str = Field(min_length=3, max_length=255)
    branch: str = Field(default="main", min_length=1, max_length=255)
    base_directory: str = Field(default="/", max_length=255)


class GitHubOAuthConfigRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str = Field(min_length=1, max_length=1024)
    encryption_key: str = Field(min_length=1, max_length=255)


class ProjectSourceAnalysisRequest(BaseModel):
    base_directory: str = Field(default="/", max_length=255)


class DetectedDeploymentRequest(ProjectSourceAnalysisRequest):
    env_vars: dict[str, str] = Field(default_factory=dict)
    start_command: str | None = Field(default=None, max_length=1000)
    domain: str | None = Field(default=None, max_length=253)


class DeploymentConfigRequest(BaseModel):
    branch: str | None = None
    start_command: str | None = None
    deploy_type: str | None = None
    dockerfile_path: str | None = None
    docker_image: str | None = None
    compose_file: str | None = None
    healthcheck_path: str | None = None
    healthcheck_interval: int | None = Field(default=None, ge=5, le=3600)
    auto_deploy: bool | None = None
    resource_memory: str | None = None
    resource_cpus: str | None = None
    env_vars: dict[str, str] | None = None


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
    nine_router_upstream: str | None = None
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


class GitHubSettingsRequest(BaseModel):
    repo: str | None = None
    token: str | None = None


class GitHubMergeRequest(BaseModel):
    method: str = "squash"
    force: bool = False


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


def _account_payload(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account["id"], "email": account["email"], "display_name": account.get("display_name") or account["email"].split("@", 1)[0],
        "avatar_icon": account.get("avatar_icon") or "user", "role": account.get("role") or "operator",
    }


def _set_account_session(response: JSONResponse, session: dict[str, Any]) -> JSONResponse:
    response.set_cookie(
        key=OPERATOR_SESSION_COOKIE, value=str(session["session_id"]), max_age=int(session["max_age"]), path="/",
        secure=True, httponly=True, samesite="strict",
    )
    return response


@app.get("/api/auth/setup")
async def auth_setup_status() -> dict[str, Any]:
    """Public status used only to determine whether the first admin account must be created."""
    return {"needs_first_account": (await count_operator_accounts()) == 0}


@app.post("/api/auth/setup")
async def create_first_account(body: FirstAccountRequest, request: Request):
    require_same_origin_if_present(request)
    if await count_operator_accounts():
        raise HTTPException(409, detail={"error": "account_setup_complete", "message": "An operator account already exists. Sign in instead."})
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(422, detail={"error": "invalid_email", "message": "Enter a valid email address."})
    account = await create_operator_account({
        "id": str(uuid.uuid4()), "email": email, "password_hash": hash_password(body.password),
        "display_name": body.display_name.strip() or email.split("@", 1)[0], "avatar_icon": "user", "role": "owner",
    })
    session = create_account_operator_session(account)
    return _set_account_session(JSONResponse({"ok": True, "csrf_token": session["csrf_token"], "expires_in": session["max_age"], "account": session["account"]}, headers={"Cache-Control": NO_CACHE}), session)


@app.post("/api/auth/login")
async def login_account(body: AccountLoginRequest, request: Request):
    require_same_origin_if_present(request)
    account = await authenticate_operator_account(body.email.strip().lower(), body.password)
    if not account:
        raise HTTPException(401, detail={"error": "invalid_credentials", "message": "Email or password is incorrect."})
    session = create_account_operator_session(account)
    return _set_account_session(JSONResponse({"ok": True, "csrf_token": session["csrf_token"], "expires_in": session["max_age"], "account": session["account"]}, headers={"Cache-Control": NO_CACHE}), session)


@app.get("/api/auth/session")
async def get_account_session(request: Request):
    return JSONResponse(operator_session_status(request), headers={"Cache-Control": NO_CACHE})


@app.delete("/api/auth/session")
async def end_account_session(request: Request, _operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    revoke_operator_session(request)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": NO_CACHE})
    response.delete_cookie(key=OPERATOR_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response


@app.get("/api/auth/profile")
async def get_account_profile(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)) -> dict[str, Any]:
    account_id = str(_operator.get("id", ""))
    account = await get_operator_account(account_id)
    if not account:
        raise HTTPException(401, detail={"error": "account_session_required", "message": "Sign in with an email and password to manage your profile."})
    return {"account": _account_payload(account)}


@app.put("/api/auth/profile")
async def update_account_profile(body: AccountProfileRequest, _operator: dict[str, Any] = Depends(verify_operator_session_or_token)) -> dict[str, Any]:
    account_id = str(_operator.get("id", ""))
    if body.avatar_icon not in {"user", "sparkles", "shield", "rocket", "leaf", "heart", "camera"}:
        raise HTTPException(422, detail={"error": "invalid_avatar", "message": "Choose one of the supported profile icons."})
    account = await update_operator_account(account_id, {"display_name": body.display_name.strip(), "avatar_icon": body.avatar_icon})
    if not account:
        raise HTTPException(401, detail={"error": "account_session_required", "message": "Sign in with an email and password to manage your profile."})
    return {"ok": True, "account": _account_payload(account), "message": "Profile updated."}


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
    from syte.cloud_agent import bridge_settings, provider_key_status
    from syte.certificates import cloudflare_tls_status
    from syte.preview_domains import resolve_preview_zone

    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    preview_base_domain = normalize_domain(await get_setting("preview_base_domain", ""))
    preview_zone = await resolve_preview_zone()
    cf_status = await cloudflare_tls_status()
    router_public_enabled = (await get_setting("nine_router_public_enabled", "0")).strip() == "1"
    if router_public_enabled:
        from syte.ssl_status import monitor_endpoint

        nine_router_local_tls = await monitor_endpoint(
            "9Router dashboard",
            NINE_ROUTER_PUBLIC_HOST,
            expect_dedicated=True,
        )
    else:
        nine_router_local_tls = {
            "configured": False,
            "serving": False,
            "state": "unreachable",
            "hostname": NINE_ROUTER_PUBLIC_HOST,
            "target": "external-gateway",
            "detail": "Remote gateway SNI verification requires public access.",
        }
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
        "nine_router_upstream": await get_setting("nine_router_upstream", ""),
        "nine_router_public_enabled": (await get_setting("nine_router_public_enabled", "0")).strip() == "1",
        "nine_router_local_tls": nine_router_local_tls,
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
            {
                "name": row["secret_env"],
                "profile": row["profile"],
                "set": bool(row["env_set"]),
                "hint": row["env_hint"] or "",
                "used": row["source"] == "env",
            }
            for row in key_status
        ],
        "agent_max_count": int((await get_setting("agent_max_count", "0")).strip() or "0") or None,
        "syra_internal_secret_set": syra_secret_set,
        "turso_database_url_set": bool(turso_database_url),
        "turso_auth_token_set": turso_auth_token_set,
        "turso_configured": bool(turso_database_url),
        "preview_dns_hint": (
            f"Point wildcard *.{preview_zone} A record to this server (grey cloud / DNS only)."
            if preview_zone
            else "Set preview base domain or GUI domain for HTTPS previews."
        ),
        "direct_url": build_direct_url(ip, settings.port),
        "domain_url": build_https_url(gui_domain) if gui_domain else "",
        "github_repo": (await get_setting("github_repo", "")).strip(),
        "github_token_set": bool((await get_setting("github_token", "")).strip()),
        "github_token_source": (await _github_token_source()),
        "version": __version__,
    }


@app.get("/api/settings/9router-tls")
async def get_nine_router_local_tls():
    """Check the active 9Router HTTPS path without probing a retired listener.
    Note: the local loopback TLS listener was removed to prevent Caddy automation
    policy conflicts. Thus, when not using the managed router, this status is
    unavailable via local probe."""
    enabled = (await get_setting("nine_router_public_enabled", "0")).strip() == "1"
    if enabled:
        from syte.ssl_status import monitor_endpoint

        return await monitor_endpoint(
            "9Router dashboard",
            NINE_ROUTER_PUBLIC_HOST,
            expect_dedicated=True,
        )
    return {
        "configured": False,
        "serving": False,
        "state": "unreachable",
        "hostname": NINE_ROUTER_PUBLIC_HOST,
        "target": "external-gateway",
        "detail": "Remote gateway SNI verification requires public access.",
    }


async def _github_token_source() -> str:
    """Return the configured GitHub token source without exposing its value."""
    from syte.github_prs import resolve_token

    _token, source = await resolve_token()
    return source


async def _model_configuration() -> dict[str, Any]:
    """Return the user-managed 9Router catalog without exposing its key."""
    from syte.ai_providers import resolved_nine_router_api_base
    from syte.model_catalog import (
        configured_models,
        enabled_model_options,
        fetch_router_models,
        model_profile,
        router_catalog_state,
        router_models_cached,
    )

    key_set = bool((await get_setting("agent_9router_api_key", "")).strip())
    # Pull the live router catalog so pickers list everything the router serves,
    # not only models that were registered by hand. Failures are non-fatal.
    await fetch_router_models()
    curated = await configured_models()
    router_models = router_models_cached()
    # `models` stays the curated catalog because the Models tab CRUD routes
    # address rows by their stored id. Only explicitly enabled curated models are
    # offered in the picker — the live router catalog is not injected here so the
    # agent model picker stays in sync with what the user enabled in the Models tab.
    available_models = enabled_model_options(curated)
    # Keep the former field while callers move to the catalog response.
    primary = curated[0] if curated else None
    api_base = await resolved_nine_router_api_base()
    return {
        "provider": {
            "name": "9Router",
            "api_base": api_base,
            "api_key_set": key_set,
        },
        "router_catalog": router_catalog_state(),
        "model": {
            "profile": "9router",
            "name": primary["name"],
            "thinking_levels": primary["thinking_levels"],
            "enabled": primary["enabled"],
        } if primary else None,
        "models": [{**row, "profile": model_profile(row["id"])} for row in curated],
        "router_models": enabled_model_options(router_models),
        "available_models": available_models,
    }


@app.get("/api/models")
async def get_models():
    return await _model_configuration()


@app.get("/api/models/available")
async def get_available_models():
    """Models offered to agent pickers: curated catalog + live router catalog."""
    config = await _model_configuration()
    return {
        "models": config["available_models"],
        "router_models": config["router_models"],
        "router_catalog": config["router_catalog"],
        "provider": config["provider"],
    }


@app.post("/api/models/router/refresh")
async def refresh_router_models():
    """Force a re-read of the router's /v1/models list."""
    from syte.model_catalog import fetch_router_models, router_catalog_state

    ok = await fetch_router_models(force=True)
    state = router_catalog_state()
    return {
        "ok": ok,
        "message": (
            f"Loaded {state['count']} models from the router."
            if ok else (state["error"] or "Could not reach the router model list.")
        ),
        **(await _model_configuration()),
    }


@app.put("/api/models/provider")
async def save_model_provider(body: ModelProviderSetupRequest):
    from syte.model_catalog import fetch_router_models, reset_router_models_cache

    await set_setting("agent_9router_api_key", body.api_key.strip())
    # The cached model list was fetched with the previous key.
    reset_router_models_cache()
    await fetch_router_models(force=True)
    return {"ok": True, "message": "9Router API key saved.", **(await _model_configuration())}


async def _save_model_records(records: list[dict[str, Any]]) -> None:
    """Persist catalog and retain single-model settings for old installations."""
    import json

    await set_setting("agent_9router_models", json.dumps(records, separators=(",", ":")))
    primary = records[0] if records else None
    await set_setting("agent_9router_model_name", primary["name"] if primary else "")
    await set_setting("agent_9router_thinking_levels", ",".join(map(str, primary["thinking_levels"])) if primary else "1,2,3,4,5")
    await set_setting("agent_9router_enabled", "1" if primary and primary["enabled"] else "0")


def _checked_model(body: ModelConfigurationRequest) -> dict[str, Any]:
    name = body.model_name.strip()
    provider = " ".join(body.provider.split())
    if not name:
        raise HTTPException(400, "Enter a model name.")
    if not provider:
        raise HTTPException(400, "Enter a provider name.")
    levels = sorted(set(body.thinking_levels))
    if not levels or any(level < 1 or level > 5 for level in levels):
        raise HTTPException(400, "Choose one or more thinking levels between 1 and 5.")
    thinking_level = body.thinking_level.strip().lower()
    if thinking_level not in {"minimal", "low", "medium", "high", "max", "xhigh"}:
        raise HTTPException(400, "Choose Minimal, Low, Medium, High, Max, or Xhigh thinking.")
    return {
        "name": name,
        "provider": provider,
        "thinking_levels": levels,
        "thinking_level": thinking_level,
        "enabled": body.enabled,
    }


def _same_provider_model(left: dict[str, Any], right: dict[str, Any]) -> bool:
    from syte.model_catalog import normalize_provider

    return (
        normalize_provider(str(left.get("provider") or ""))
        == normalize_provider(str(right.get("provider") or ""))
        and str(left.get("name") or "").strip().casefold()
        == str(right.get("name") or "").strip().casefold()
    )


async def _require_provider_key_if_enabled(records: list[dict[str, Any]]) -> None:
    if any(record["enabled"] for record in records) and not (await get_setting("agent_9router_api_key", "")).strip():
        raise HTTPException(400, "Save the 9Router API key before enabling this model.")


@app.post("/api/models")
async def add_model(body: ModelConfigurationRequest):
    from syte.model_catalog import configured_models, new_model_id

    record = _checked_model(body)
    records = await configured_models()
    record["id"] = new_model_id(record["name"], record["provider"])
    if any(_same_provider_model(item, record) for item in records):
        raise HTTPException(400, "That provider already has this model in the list.")
    records.append(record)
    await _require_provider_key_if_enabled(records)
    await _save_model_records(records)
    return {"ok": True, "message": "Model added.", **(await _model_configuration())}


@app.post("/api/models/bulk")
async def add_models_bulk(body: BulkModelConfigurationRequest):
    from syte.model_catalog import configured_models, new_model_id

    records = await configured_models()
    added = 0
    for item in body.models:
        record = _checked_model(item)
        record["id"] = new_model_id(record["name"], record["provider"])
        if not any(_same_provider_model(existing, record) for existing in records):
            records.append(record)
            added += 1
    if not added:
        raise HTTPException(400, "All submitted models are already in the list.")
    await _require_provider_key_if_enabled(records)
    await _save_model_records(records)
    return {"ok": True, "message": f"{added} models added.", **(await _model_configuration())}


@app.put("/api/models/{model_id}")
async def update_model(model_id: str, body: ModelConfigurationRequest):
    from syte.model_catalog import configured_models

    records = await configured_models()
    record = _checked_model(body)
    for index, existing in enumerate(records):
        if existing["id"] == model_id:
            if any(
                other["id"] != model_id and _same_provider_model(other, record)
                for other in records
            ):
                raise HTTPException(400, "That provider already has this model in the list.")
            records[index] = {**record, "id": model_id}
            break
    else:
        raise HTTPException(404, "Model not found.")
    await _require_provider_key_if_enabled(records)
    await _save_model_records(records)
    return {"ok": True, "message": "Model updated.", **(await _model_configuration())}


@app.delete("/api/models/{model_id}")
async def delete_model(model_id: str):
    """Delete a model from the catalog globally."""
    from syte.model_catalog import configured_models

    records = await configured_models()
    found = False
    for index, existing in enumerate(records):
        if existing["id"] == model_id:
            records.pop(index)
            found = True
            break
    
    if not found:
        raise HTTPException(404, "Model not found.")
    
    await _save_model_records(records)
    return {"ok": True, "message": "Model deleted.", **(await _model_configuration())}


@app.put("/api/models/default")
async def save_default_model(body: ModelConfigurationRequest):
    """Compatibility endpoint: create or update the first catalog model."""
    from syte.model_catalog import configured_models, new_model_id

    records = await configured_models()
    record = _checked_model(body)
    if records:
        records[0] = {**record, "id": records[0]["id"]}
    else:
        records.append({**record, "id": new_model_id(record["name"])})
    await _require_provider_key_if_enabled(records)
    await _save_model_records(records)
    return {"ok": True, "message": "Model saved.", **(await _model_configuration())}


@app.post("/api/models/playground")
async def run_model_playground(body: ModelPlaygroundRequest):
    """Run a short, tool-free prompt with one enabled catalog model."""
    from syte.cloud_agent import _provider_completion, is_catalog_model_profile, model_metadata_for_profile
    from syte.thinking_levels import resolve_thinking_config

    profile = body.model_profile.strip()
    if not await is_catalog_model_profile(profile):
        raise HTTPException(400, "Choose an enabled model from the Models tab.")
    model = await model_metadata_for_profile(profile)
    if not str(model.get("api_key") or "").strip():
        raise HTTPException(400, "Update the provider API key before using this model.")
    response = await _provider_completion(
        model,
        [
            {"role": "system", "content": "You are the Sarra model playground. Answer directly and concisely."},
            {"role": "user", "content": body.prompt.strip()},
        ],
        tools=[],
        thinking_config=resolve_thinking_config(3, fallback_profile=profile),
    )
    return {
        "ok": True,
        "model": model.get("model") or "",
        "provider": model.get("label") or "",
        "response": str(response.get("content") or ""),
        "usage": response.get("_usage") or {},
    }


async def _solar_status() -> dict[str, Any]:
    from syte.solar_runtime import solar_status

    return await solar_status()


@app.get("/api/ai/solar/status")
async def get_solar_status():
    return await _solar_status()


@app.delete("/api/ai/solar")
async def delete_solar_model():
    from syte.solar_runtime import delete_solar

    return await delete_solar()


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

    if body.custom_tls_host is not None:
        host = normalize_domain(body.custom_tls_host)
        await set_setting("custom_tls_host", host)
        proxy_updated = True
        messages.append(f"Global custom TLS host set to {host}" if host else "Global custom TLS host cleared.")
    if body.custom_tls_port is not None:
        await set_setting("custom_tls_port", body.custom_tls_port.strip())
        proxy_updated = True
        messages.append("Global custom TLS port set.")

    if body.nine_router_backend_port is not None:
        raw = body.nine_router_backend_port.strip()
        if raw:
            try:
                port = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(400, "9Router backend port must be an integer (1-65535).")
            if not 1 <= port <= 65535:
                raise HTTPException(400, "9Router backend port must be between 1 and 65535.")
            await set_setting("nine_router_backend_port", str(port))
            messages.append(f"Legacy 9Router backend port saved as {port}; public 9Router Caddy traffic remains on the remote gateway.")
        else:
            await set_setting("nine_router_backend_port", "")
            messages.append("9Router backend port reset to the default gateway port.")
        proxy_updated = True

    if body.nine_router_upstream is not None:
        raw = body.nine_router_upstream.strip()
        if raw:
            from syte.certificates import normalize_remote_nine_router_upstream

            upstream = normalize_remote_nine_router_upstream(raw)
            if not upstream:
                raise HTTPException(
                    400,
                    "9Router upstream must be a remote hostname or global IPv4 address with port; "
                    "localhost and private/local IPs are not allowed.",
                )
            await set_setting("nine_router_upstream", upstream)
            messages.append(f"9Router upstream set to {upstream} — Caddy terminates SSL and forwards there.")
        else:
            await set_setting("nine_router_upstream", "")
            messages.append("9Router upstream reset to the real gateway (65.75.203.134:20128).")
        proxy_updated = True

    if body.agent_default_model_profile is not None:
        from syte.ai_providers import DEFAULT_PROFILE
        from syte.cloud_agent import is_catalog_model_profile

        profile = body.agent_default_model_profile.strip() or DEFAULT_PROFILE
        if not await is_catalog_model_profile(profile):
            raise HTTPException(400, f"Unknown model profile: {profile}")
        await set_setting("agent_default_model_profile", profile)
        messages.append(f"Default Syte cloud model profile: {profile}")
    if body.agent_syra_nano_api_key is not None:
        await set_setting("agent_syra_nano_api_key", body.agent_syra_nano_api_key.strip())
        messages.append(
            "Go (Gemini · gemini-2.5-flash) API key saved."
            if body.agent_syra_nano_api_key.strip()
            else "syra-nano API key cleared."
        )
    if body.agent_syra_havy_api_key is not None:
        await set_setting("agent_syra_havy_api_key", body.agent_syra_havy_api_key.strip())
        messages.append(
            "Metal (VyceAI · claude-sonnet-4-6) API key saved."
            if body.agent_syra_havy_api_key.strip()
            else "syra-havy API key cleared."
        )
    if body.agent_syra_ultra_api_key is not None:
        ultra_key = body.agent_syra_ultra_api_key.strip()
        if ultra_key.lower().startswith("sk-or-"):
            raise HTTPException(
                400,
                "syra-ultra no longer accepts OpenRouter keys (sk-or-…). "
                "Paste an Aliyun Token Plan key (sk-sp-…) or a Model Studio sk- key.",
            )
        await set_setting("agent_syra_ultra_api_key", ultra_key)
        messages.append(
            "Air (Aliyun · qwen3.7-plus) API key saved."
            if ultra_key
            else "syra-ultra API key cleared."
        )
    if body.litellm_proxy_url is not None:
        requested_url = body.litellm_proxy_url.strip().rstrip("/")
        if requested_url and requested_url != LITELLM_PUBLIC_API_URL:
            raise HTTPException(
                400,
                f"LiteLLM is deployed at {LITELLM_PUBLIC_API_URL}; custom proxy URLs are not supported.",
            )
        await set_setting("litellm_proxy_url", LITELLM_PUBLIC_API_URL)
        proxy_updated = True
        messages.append(f"LiteLLM public API URL: {LITELLM_PUBLIC_API_URL}")
    if body.litellm_database_url is not None:
        from syte.litellm_manager import validate_litellm_database_url

        requested_database_url = body.litellm_database_url.strip()
        try:
            validated_database_url = validate_litellm_database_url(requested_database_url)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        await set_setting("litellm_database_url", validated_database_url)
        proxy_updated = True
        messages.append(
            "Custom LiteLLM PostgreSQL database URL saved; restart LiteLLM to apply it."
            if validated_database_url
            else "Custom LiteLLM database URL cleared; the managed PostgreSQL database will be used."
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


async def _save_syra_secrets(body: SyraSecretsRequest) -> dict[str, Any]:
    """Persist protected LiteLLM credentials without returning their values."""
    messages: list[str] = []
    if body.master_key is not None:
        await set_setting("litellm_master_key", body.master_key.strip())
        messages.append("LiteLLM master key saved." if body.master_key.strip() else "LiteLLM master key cleared.")
    if body.salt_key is not None:
        await set_setting("litellm_salt_key", body.salt_key.strip())
        messages.append("LiteLLM salt key saved." if body.salt_key.strip() else "LiteLLM salt key cleared.")
    if body.agent_api_key is not None:
        await set_setting("agent_litellm_api_key", body.agent_api_key.strip())
        messages.append("LiteLLM virtual API key saved." if body.agent_api_key.strip() else "LiteLLM virtual API key cleared.")
    if not messages:
        raise HTTPException(400, "Provide at least one LiteLLM credential to update.")
    return {"ok": True, "messages": messages}


@app.put("/api/settings/syra/secrets")
async def api_syra_save_secrets(
    body: SyraSecretsRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Save server-side LiteLLM credentials using an operator API token."""
    return await _save_syra_secrets(body)


@app.get("/api/settings/github")
async def api_github_settings(
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Return GitHub tracking configuration without exposing the token."""
    from syte.github_prs import resolve_token

    token, source = await resolve_token()
    return {
        "ok": True,
        "repo": (await get_setting("github_repo", "")).strip(),
        "token_configured": bool(token),
        "token_source": source,
    }


@app.put("/api/settings/github")
async def api_save_github_settings(
    body: GitHubSettingsRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Save the repository/token used by the web Git and PR tracker."""
    from syte.update_source import parse_github_repo

    if body.repo is None and body.token is None:
        raise HTTPException(400, "Provide a repository or token to update.")

    messages: list[str] = []
    if body.repo is not None:
        raw_repo = body.repo.strip()
        repo = parse_github_repo(raw_repo) or raw_repo.strip("/")
        parts = repo.split("/") if repo else []
        if len(parts) != 2 or any(not part or any(char.isspace() for char in part) for part in parts):
            raise HTTPException(400, "GitHub repository must be owner/repo or a GitHub URL.")
        await set_setting("github_repo", repo)
        messages.append(f"GitHub repository set to {repo}.")
    if body.token is not None:
        token = body.token.strip()
        await set_setting("github_token", token)
        messages.append("GitHub token saved." if token else "GitHub token cleared.")
    return {"ok": True, "messages": messages}


@app.get("/api/github/status")
async def api_github_status(
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Return local branch/update state for the web Git tracker."""
    from syte.github_prs import git_status

    return await git_status()


@app.get("/api/github/pulls")
async def api_github_pulls(
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """List tracked repository pull requests with merge-readiness metadata."""
    from syte.github_prs import GitHubError, list_open_prs

    try:
        return await list_open_prs()
    except GitHubError as error:
        raise HTTPException(error.status or 502, str(error)) from error


@app.post("/api/github/pulls/{number}/merge")
async def api_github_merge_pull(
    number: int,
    body: GitHubMergeRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Merge a ready tracked pull request after an explicit web confirmation."""
    from syte.github_prs import GitHubError, merge_pr

    try:
        result = await merge_pr(number, method=body.method, force=body.force)
    except GitHubError as error:
        raise HTTPException(error.status or 502, str(error)) from error
    if not result.get("ok"):
        return JSONResponse(result, status_code=409)
    return result


@app.get("/api/system/update-info")
async def api_update_info():
    from syte.self_update import get_update_info

    info = {"ok": True, **get_update_info(), "recent_mergeable_commits": []}
    try:
        from syte.github_prs import recent_mergeable_commits

        info["recent_mergeable_commits"] = await recent_mergeable_commits(limit=3)
    except Exception as error:  # noqa: BLE001 - update info remains useful without GitHub
        logger.warning("Could not load recent mergeable commits: %s", error)
    return info


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


@app.get("/api/settings/new-feature/info")
async def api_new_feature_info():
    """Return info for the new feature tab: current version, update target, and registered tabs."""
    from syte.new_feature_agent import get_current_version, get_update_target_info

    return {
        "ok": True,
        "version": get_current_version(),
        "update_target": get_update_target_info(),
        "tabs": get_registered_tabs(),
    }


# ---------------------------------------------------------------------------
# Syra / LiteLLM proxy management
# ---------------------------------------------------------------------------


@app.get("/api/settings/syra/status")
async def api_syra_status(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Get LiteLLM proxy status and configuration."""
    from syte.litellm_manager import litellm_health, litellm_status
    from syte.ssl_status import litellm_api_ssl_status

    status = await litellm_status()
    health = await litellm_health() if status["running"] else {"healthy": False}
    ssl = litellm_api_ssl_status()
    
    return {
        "ok": True,
        **status,
        "health": health,
        "proxy_url": LITELLM_PUBLIC_API_URL,
        "public_api_url": LITELLM_PUBLIC_API_URL,
        "web_gui_url": f"https://{LITELLM_PUBLIC_HOST}/",
        "public_host": LITELLM_PUBLIC_HOST,
        "ssl": ssl,
        "dns_hint": (
            f"Start prepares AlmaLinux, Docker, Caddy, firewalld, DNS, and TLS for {LITELLM_PUBLIC_HOST}."
        ),
        "master_key_set": bool((await get_setting("litellm_master_key", "")).strip()),
        "salt_key_set": bool((await get_setting("litellm_salt_key", "")).strip()),
        "agent_api_key_set": bool((await get_setting("agent_litellm_api_key", "")).strip()),
    }


# ---------------------------------------------------------------------------
# Managed 9Router deployment
# ---------------------------------------------------------------------------

def _suggested_gui_domain() -> str:
    """A distinct subdomain on the same zone, offered as a one-click fix."""
    from syte.caddy_routes import host_zone

    zone = host_zone(NINE_ROUTER_PUBLIC_HOST)
    return f"console.{zone}" if zone else ""


async def _router_gui_guard() -> dict[str, Any] | None:
    """Require a separate Syte origin before handing 9router.sycord.site to 9Router.

    ``gui_domain`` may be unset or set to ``9router.sycord.site`` on fresh hosts,
    and either state conflicts with the host the managed Router needs to take
    over. Without this guard the operator would silently lose the Syte console;
    the response instead carries enough information
    (``gui_domain_conflict`` + ``suggested_gui_domain``) for the Router tab to
    offer a one-click fix rather than sending the operator to hunt through
    Settings for the cause.
    """
    from syte.nine_router_manager import router_status

    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    if gui_domain and gui_domain != NINE_ROUTER_PUBLIC_HOST:
        return None
    status = await router_status()
    return {
        **status,
        "ok": False,
        "gui_domain_conflict": True,
        "suggested_gui_domain": _suggested_gui_domain(),
        "message": (
            "Configure a separate GUI domain in Settings before deploying 9Router. "
            f"The managed Router takes over https://{NINE_ROUTER_PUBLIC_HOST}/."
        ),
    }


async def _set_router_public_state(enabled: bool, *, force: bool = False) -> tuple[bool, str]:
    """Apply the Caddy route and roll the setting back if application fails."""
    from syte.nine_router_manager import NINE_ROUTER_ENABLED_SETTING

    previous = (await get_setting(NINE_ROUTER_ENABLED_SETTING, "0")).strip() == "1"
    if previous == enabled and not force:
        return True, "Router public route already has the requested state."
    await set_setting(NINE_ROUTER_ENABLED_SETTING, "1" if enabled else "0")
    ok, message = await apply_proxy_config()
    if ok:
        return True, message

    await set_setting(NINE_ROUTER_ENABLED_SETTING, "1" if previous else "0")
    rollback_ok, rollback_message = await apply_proxy_config()
    if rollback_ok:
        return False, f"{message}; previous Router route state was restored."
    return False, f"{message}; route-state rollback also failed: {rollback_message}"


async def _router_start() -> dict[str, Any]:
    from syte.host_setup import prepare_router_host
    from syte.nine_router_manager import (
        record_router_debug,
        router_status,
        start_router,
        stop_router,
    )

    async with _ROUTER_START_LOCK:
        guard = await _router_gui_guard()
        if guard:
            record_router_debug("configuration", guard.get("message", "GUI domain conflict"), ok=False)
            return guard

        host_setup = await prepare_router_host()
        for step in host_setup.get("steps", []):
            record_router_debug("AlmaLinux host setup", step, ok=host_setup.get("ok"))
        if not host_setup.get("ok"):
            record_router_debug("AlmaLinux host setup", host_setup.get("message", "Host setup failed"), ok=False)
            return {
                **await router_status(),
                "ok": False,
                "host_setup": host_setup,
                "message": host_setup.get("message", "Could not prepare the AlmaLinux host."),
            }

        before = await router_status()
        result = await start_router()
        result["host_setup"] = host_setup
        if not result.get("ok"):
            # If a previously enabled container failed to start, do not leave
            # Caddy pointing at a dead upstream. Restore the fallback route.
            if before.get("enabled"):
                route_ok, route_message = await _set_router_public_state(False, force=True)
                record_router_debug("Caddy fallback route", route_message, ok=route_ok)
                result["proxy_configured"] = route_ok
                result["proxy_message"] = route_message
            return result

        route_ok, route_message = await _set_router_public_state(True, force=True)
        record_router_debug("Caddy managed Router route", route_message, ok=route_ok)
        result["proxy_configured"] = route_ok
        result["proxy_message"] = route_message
        if not route_ok:
            # First restore the fallback route. Only stop a newly-created
            # container after the safe route is confirmed, otherwise a failed
            # Caddy reload could leave the enabled flag pointing at a dead
            # upstream.
            fallback_ok, fallback_message = await _set_router_public_state(False, force=True)
            record_router_debug("Caddy fallback route", fallback_message, ok=fallback_ok)
            result["fallback_configured"] = fallback_ok
            if not fallback_ok:
                route_message += f" Fallback route restore also failed: {fallback_message}"
            if fallback_ok and not before.get("running"):
                cleanup = await stop_router()
                record_router_debug("container cleanup", cleanup.get("message", ""), ok=cleanup.get("ok"))
                if not cleanup.get("ok"):
                    route_message += f" Container cleanup also failed: {cleanup.get('message', '')}"
            result["ok"] = False
            result["message"] = f"9Router is running, but its public route failed: {route_message}"
        else:
            from syte.ssl_status import monitor_endpoint

            result["public_ssl"] = await monitor_endpoint(
                "9Router dashboard",
                NINE_ROUTER_PUBLIC_HOST,
                expect_dedicated=True,
            )
            result["ssl_ready"] = result["public_ssl"].get("state") == "serving"
            result["message"] = (
                f"{result.get('message', '9Router started')} "
                "9router.sycord.site now serves the 9Router dashboard and /v1 API over HTTPS."
            )
            if not result["ssl_ready"]:
                result["message"] += (
                    f" Public HTTPS status: {result['public_ssl'].get('detail', 'still pending')}. "
                    "Refresh diagnostics after DNS/Caddy certificate issuance."
                )
        return result


async def _router_stop() -> dict[str, Any]:
    from syte.nine_router_manager import NINE_ROUTER_ENABLED_SETTING, router_status, stop_router

    async with _ROUTER_START_LOCK:
        enabled = (await get_setting(NINE_ROUTER_ENABLED_SETTING, "0")).strip() == "1"
        if enabled:
            # Move public traffic away before stopping the upstream. If this
            # fails, keep the enabled flag and live route unchanged.
            route_ok, route_message = await _set_router_public_state(False)
            if not route_ok:
                from syte.nine_router_manager import record_router_debug

                record_router_debug("stop Caddy handoff", route_message, ok=False)
                status = await router_status()
                return {
                    **status,
                    "ok": False,
                    "proxy_configured": False,
                    "proxy_message": route_message,
                    "message": f"Could not restore the LiteLLM route; 9Router remains enabled: {route_message}",
                }

        result = await stop_router()
        result["proxy_configured"] = True
        result["proxy_message"] = "LiteLLM/remote 9Router fallback route is active." if enabled else ""
        if not result.get("ok"):
            from syte.nine_router_manager import record_router_debug

            record_router_debug("stop", result.get("message", "Failed to stop 9Router"), ok=False)
            result["message"] = f"{result.get('message', 'Failed to stop 9Router')} Public fallback route is active."
        return result


async def _router_restart() -> dict[str, Any]:
    from syte.host_setup import prepare_router_host
    from syte.nine_router_manager import record_router_debug, start_router, stop_router, router_status

    async with _ROUTER_START_LOCK:
        guard = await _router_gui_guard()
        if guard:
            record_router_debug("configuration", guard.get("message", "GUI domain conflict"), ok=False)
            return guard
        host_setup = await prepare_router_host()
        for step in host_setup.get("steps", []):
            record_router_debug("AlmaLinux host setup", step, ok=host_setup.get("ok"))
        if not host_setup.get("ok"):
            message = host_setup.get("message", "Could not prepare the AlmaLinux host.")
            record_router_debug("AlmaLinux host setup", message, ok=False)
            return {**await router_status(), "ok": False, "host_setup": host_setup, "message": message}
        # Use the same safe handoff as stop/start instead of restarting the
        # container while Caddy still points at an unavailable upstream.
        enabled = (await get_setting("nine_router_public_enabled", "0")).strip() == "1"
        if enabled:
            route_ok, route_message = await _set_router_public_state(False)
            if not route_ok:
                record_router_debug("restart Caddy handoff", route_message, ok=False)
                return {**await router_status(), "ok": False, "message": route_message}
        stopped = await stop_router()
        if not stopped.get("ok"):
            record_router_debug("restart stop", stopped.get("message", "Failed to stop 9Router"), ok=False)
            return stopped
        started = await start_router()
        started["host_setup"] = host_setup
        if not started.get("ok"):
            record_router_debug("restart start", started.get("message", "Failed to start 9Router"), ok=False)
            return started
        route_ok, route_message = await _set_router_public_state(True, force=True)
        record_router_debug("Caddy managed Router route", route_message, ok=route_ok)
        started["proxy_configured"] = route_ok
        started["proxy_message"] = route_message
        if not route_ok:
            cleanup = await stop_router()
            record_router_debug("restart cleanup", cleanup.get("message", ""), ok=cleanup.get("ok"))
            if not cleanup.get("ok"):
                route_message += f" Container cleanup also failed: {cleanup.get('message', '')}"
            started["ok"] = False
            started["message"] = f"9Router restarted, but its public route failed: {route_message}"
        else:
            from syte.ssl_status import monitor_endpoint

            started["public_ssl"] = await monitor_endpoint(
                "9Router dashboard",
                NINE_ROUTER_PUBLIC_HOST,
                expect_dedicated=True,
            )
            started["ssl_ready"] = started["public_ssl"].get("state") == "serving"
            if not started["ssl_ready"]:
                started["message"] = (
                    f"{started.get('message', '9Router restarted')} "
                    f"Public HTTPS status: {started['public_ssl'].get('detail', 'still pending')}."
                )
        return started


@app.get("/api/settings/router/status")
async def api_router_status(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Return status for the managed local 9Router deployment and its public SSL."""
    from syte.nine_router_manager import router_status
    from syte.ssl_status import monitor_endpoint

    result = await router_status()
    result["syte_gui_url"] = await _gui_url()
    result["ssl"] = await monitor_endpoint(
        "9Router dashboard",
        NINE_ROUTER_PUBLIC_HOST,
        expect_dedicated=True,
    )
    if result.get("enabled"):
        result["warning"] = (
            "9router.sycord.site is currently owned by 9Router. "
            "The Syte console is available at the configured separate GUI domain."
        )
    else:
        result["warning"] = ""
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    result["gui_domain_conflict"] = not gui_domain or gui_domain == NINE_ROUTER_PUBLIC_HOST
    if result["gui_domain_conflict"]:
        result["suggested_gui_domain"] = _suggested_gui_domain()
        if not result.get("enabled"):
            result["warning"] = (
                f"Set a separate GUI domain (e.g. {result['suggested_gui_domain']}) in Settings "
                "before starting 9Router — it will otherwise be blocked."
            )
    return result


@app.get("/api/settings/router/password")
async def api_router_password(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Return the persisted initial 9Router WebGUI credential to an authenticated operator."""
    from syte.nine_router_manager import _router_password

    password, is_new = await _router_password()
    return {"password": password, "is_new": is_new}


@app.post("/api/settings/router/start")
async def api_router_start(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Deploy the official 9Router image and publish it at 9router.sycord.site."""
    try:
        return await _router_start()
    except Exception as error:  # noqa: BLE001 - operator receives a useful diagnostic
        logger.exception("9Router start failed")
        from syte.nine_router_manager import record_router_debug

        record_router_debug("start exception", f"{type(error).__name__}: {error}", ok=False)
        return {"ok": False, "running": False, "message": f"9Router start failed — {type(error).__name__}: {error}"}


@app.post("/api/settings/router/stop")
async def api_router_stop(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Stop the managed 9Router container and restore the previous API route."""
    try:
        return await _router_stop()
    except Exception as error:  # noqa: BLE001 - operator receives a useful diagnostic
        logger.exception("9Router stop failed")
        from syte.nine_router_manager import record_router_debug

        record_router_debug("stop exception", f"{type(error).__name__}: {error}", ok=False)
        return {"ok": False, "running": False, "message": f"9Router stop failed — {type(error).__name__}: {error}"}


@app.post("/api/settings/router/restart")
async def api_router_restart(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Restart 9Router and re-apply its public Caddy route."""
    try:
        return await _router_restart()
    except Exception as error:  # noqa: BLE001 - operator receives a useful diagnostic
        logger.exception("9Router restart failed")
        from syte.nine_router_manager import record_router_debug

        record_router_debug("restart exception", f"{type(error).__name__}: {error}", ok=False)
        return {"ok": False, "running": False, "message": f"9Router restart failed — {type(error).__name__}: {error}"}


@app.get("/api/settings/router/logs")
async def api_router_logs(
    lines: int = 100,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Return recent logs from the managed 9Router container."""
    from syte.nine_router_manager import router_logs

    return await router_logs(lines)


@app.get("/api/settings/router/debug")
async def api_router_debug(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Return persistent install diagnostics plus recent container output."""
    from syte.nine_router_manager import router_debug_log, router_logs, router_status

    diagnostic_log = await router_debug_log()
    container = await router_logs(200)
    return {
        "ok": bool(diagnostic_log.get("ok") or container.get("ok")),
        "status": await router_status(),
        "installation_log": diagnostic_log.get("log", ""),
        "container_logs": container.get("logs", ""),
        "container_log_error": container.get("message", "") if not container.get("ok") else "",
    }


# ---------------------------------------------------------------------------
# SSL dashboard (monitor / configure / resolve)
# ---------------------------------------------------------------------------


@app.get("/api/ssl")
async def api_ssl_status(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Aggregate SSL dashboard: Caddy + Cloudflare prerequisites, per-project certs."""
    from syte.ssl_status import build_ssl_overview

    return await build_ssl_overview()


@app.post("/api/ssl/resolve")
async def api_ssl_resolve(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Attempt to repair SSL: ensure Caddy is running and reload the proxy config."""
    from syte.ssl_status import resolve_ssl_issues

    try:
        return await resolve_ssl_issues()
    except Exception as error:  # noqa: BLE001 - surfaced to the operator dashboard
        logger.exception("SSL resolve failed")
        return {
            "ok": False,
            "resolved": False,
            "messages": [f"SSL resolve failed — {type(error).__name__}: {error}"],
        }


class CustomTlsRequest(BaseModel):
    custom_tls_domain: str = ""
    custom_tls_enabled: bool = False


@app.post("/api/ssl/projects/{project_id}/custom-tls")
async def api_project_custom_tls(
    project_id: str,
    body: CustomTlsRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Set an app-specific dedicated TLS domain (its own Let's Encrypt cert).

    ``custom_tls_enabled`` must be true for the block to be served; the domain
    is normalised and validated for safe use in the Caddyfile.
    """
    from syte.database import get_project, update_project
    from syte.certificates import apply_proxy_config

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    domain = normalize_domain(body.custom_tls_domain or "")
    from syte.domain_utils import is_safe_caddy_hostname

    if body.custom_tls_enabled and not is_safe_caddy_hostname(domain):
        raise HTTPException(400, f"Invalid or unsafe custom domain: {body.custom_tls_domain!r}")

    await update_project(project_id, {
        "custom_tls_domain": domain,
        "custom_tls_enabled": 1 if body.custom_tls_enabled else 0,
    })
    ok, msg = await apply_proxy_config()
    if not ok:
        return {"ok": False, "message": f"Custom TLS saved but proxy apply failed: {msg}"}
    return {
        "ok": True,
        "message": f"Custom TLS {'enabled' if body.custom_tls_enabled else 'disabled'} for {project.get('name', project_id)}"
        + (f" on {domain}" if domain and body.custom_tls_enabled else ""),
    }


async def _syra_action(action: str, operation: Any) -> dict[str, Any]:
    """Run a Syra lifecycle action, reporting failures as readable JSON.

    Docker, Caddy, and preview migration all touch the host, so an unexpected
    error must not reach the browser as an opaque 500.
    """
    try:
        return await operation()
    except Exception as error:  # noqa: BLE001 - surfaced to the operator UI
        logger.exception("Syra %s failed", action)
        return {
            "ok": False,
            "running": False,
            "message": f"Syra {action} failed — {type(error).__name__}: {error}",
        }


async def _deploy_litellm_public_proxy_from(lifecycle: Any) -> dict[str, Any]:
    """Run a LiteLLM lifecycle call, then publish its Caddy route."""
    return await _deploy_litellm_public_proxy(await lifecycle())


async def _start_syra_stack() -> dict[str, Any]:
    """Prepare the AlmaLinux host, start LiteLLM, and publish the combined host."""
    async with _SYRA_START_LOCK:
        return await _start_syra_stack_locked()


async def _start_syra_stack_locked() -> dict[str, Any]:
    from syte.host_setup import prepare_syra_host
    from syte.litellm_manager import start_litellm

    host_setup = await prepare_syra_host()
    if not host_setup["ok"]:
        return {
            "ok": False,
            "running": False,
            "message": host_setup["message"],
            "host_setup": host_setup,
        }
    result = await _deploy_litellm_public_proxy_from(start_litellm)
    result["host_setup"] = host_setup
    return result


async def _restart_syra_stack() -> dict[str, Any]:
    """Prepare the host, restart LiteLLM, and publish the combined host."""
    async with _SYRA_START_LOCK:
        return await _restart_syra_stack_locked()


async def _restart_syra_stack_locked() -> dict[str, Any]:
    from syte.host_setup import prepare_syra_host
    from syte.litellm_manager import restart_litellm

    host_setup = await prepare_syra_host()
    if not host_setup["ok"]:
        return {
            "ok": False,
            "running": False,
            "message": host_setup["message"],
            "host_setup": host_setup,
        }
    result = await _deploy_litellm_public_proxy_from(restart_litellm)
    result["host_setup"] = host_setup
    return result


async def _deploy_litellm_public_proxy(result: dict[str, Any]) -> dict[str, Any]:
    """Apply Caddy's combined GUI/API public route after a LiteLLM lifecycle action."""
    if not result.get("ok"):
        return result
    proxy_ok, proxy_message = await apply_proxy_config()
    result["proxy_configured"] = proxy_ok
    result["proxy_message"] = proxy_message
    if not proxy_ok:
        result["ok"] = False
        result["message"] = f"{result.get('message', 'Action completed.')} Caddy route failed: {proxy_message}"
    return result


@app.post("/api/settings/syra/start")
async def api_syra_start(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Prepare the AlmaLinux host, start LiteLLM, and publish https://api.sycord.site/."""
    return await _syra_action("start", _start_syra_stack)


@app.post("/api/settings/syra/stop")
async def api_syra_stop(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Stop the LiteLLM proxy container."""
    from syte.litellm_manager import stop_litellm

    return await _syra_action("stop", stop_litellm)


@app.post("/api/settings/syra/restart")
async def api_syra_restart(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Prepare the AlmaLinux host, restart LiteLLM, and publish https://api.sycord.site/."""
    return await _syra_action("restart", _restart_syra_stack)


@app.get("/api/settings/syra/logs")
async def api_syra_logs(
    lines: int = 100,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Get logs from the LiteLLM proxy container."""
    from syte.litellm_manager import litellm_logs

    result = await litellm_logs(lines=max(1, min(lines, 500)))
    return result


@app.get("/api/settings/syra/models")
async def api_syra_models(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    """Get list of configured models from LiteLLM."""
    from syte.litellm_manager import litellm_models

    result = await litellm_models()
    return result


class NewFeatureAgentRequest(BaseModel):
    message: str = Field(..., description="Message to the system agent")
    model_profile: str = Field(..., min_length=1, description="Enabled model profile from the Models tab")
    request_api_key: str | None = Field(None, description="Provider API key supplied by the requesting user")


@app.post("/api/settings/new-feature/agent")
async def api_new_feature_agent(body: NewFeatureAgentRequest):
    """Run the new-feature system agent with file access.

    After the agent finishes, an auto-update is triggered automatically.
    """
    from syte.cloud_agent import is_catalog_model_profile

    if not await is_catalog_model_profile(body.model_profile):
        raise HTTPException(400, "Choose an enabled model from the Models tab.")
    result = await run_new_feature_agent(
        message=body.message,
        model_profile=body.model_profile,
        request_api_key=body.request_api_key,
    )
    if result.get("ok"):
        ok, update_message = update_syte()
        result["triggered_update"] = True
        result["update_message"] = update_message
    return result


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


async def _github_callback_url(request: Request) -> str:
    configured = settings.public_base_url.strip() or (await get_setting("public_base_url", "")).strip()
    if configured:
        return f"{configured.rstrip('/')}/api/projects/git/github/callback"
    gui_domain = (await get_setting("gui_domain", "")).strip()
    if gui_domain:
        return f"https://{gui_domain}/api/projects/git/github/callback"
    return f"{str(request.base_url).rstrip('/')}/api/projects/git/github/callback"


@app.get("/api/projects/git/github/status")
async def api_github_source_status(
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.github_oauth import connection_summary

    return await connection_summary(str(_operator["id"]))


@app.put("/api/projects/git/github/config")
async def api_configure_github_oauth(
    body: GitHubOAuthConfigRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Persist provider credentials only through the protected operator session.

    The secret and encryption key are never returned by a status or connection
    endpoint. Production may alternatively inject the same settings by env vars.
    """
    from cryptography.fernet import Fernet

    try:
        Fernet(body.encryption_key.strip().encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Use a valid Fernet encryption key for OAuth token storage.") from exc
    await set_setting("github_oauth_client_id", body.client_id.strip())
    await set_setting("github_oauth_client_secret", body.client_secret.strip())
    await set_setting("oauth_encryption_key", body.encryption_key.strip())
    return {"ok": True, "message": "GitHub OAuth provider configured. Connect an account to choose repositories."}


@app.get("/api/projects/git/github/connect")
async def api_connect_github(
    request: Request,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.github_oauth import GitHubOAuthError, start_github_authorization

    try:
        authorization_url = await start_github_authorization(str(_operator["id"]), await _github_callback_url(request))
    except GitHubOAuthError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"authorization_url": authorization_url}


@app.get("/api/projects/git/github/callback", response_class=HTMLResponse)
async def api_github_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    from html import escape
    from syte.github_oauth import GitHubOAuthError, complete_github_authorization

    if error or not code or not state:
        message = "GitHub authorization was cancelled or did not return a code."
        return HTMLResponse(f"<script>window.opener?.postMessage({{type:'syte-github-oauth',ok:false,message:{message!r}}}, window.location.origin);window.close()</script><p>{escape(message)}</p>", status_code=400)
    try:
        connection = await complete_github_authorization(code, state)
    except GitHubOAuthError as exc:
        message = str(exc)
        return HTMLResponse(f"<script>window.opener?.postMessage({{type:'syte-github-oauth',ok:false,message:{message!r}}}, window.location.origin);window.close()</script><p>{escape(message)}</p>", status_code=400)
    message = f"GitHub connected as {connection['login']}. You can close this window."
    return HTMLResponse(f"<script>window.opener?.postMessage({{type:'syte-github-oauth',ok:true,login:{connection['login']!r}}}, window.location.origin);window.close()</script><p>{escape(message)}</p>")


@app.delete("/api/projects/git/github/disconnect")
async def api_disconnect_github(
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.database import delete_github_connection

    await delete_github_connection(str(_operator["id"]))
    return {"ok": True, "message": "GitHub connection removed."}


@app.get("/api/projects/git/github/repositories")
async def api_list_github_repositories(
    q: str = "",
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.github_oauth import GitHubOAuthError, list_repositories

    try:
        return {"repositories": await list_repositories(str(_operator["id"]), q)}
    except GitHubOAuthError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/git/github/repositories/{repository:path}/branches")
async def api_list_github_branches(
    repository: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.github_oauth import GitHubOAuthError, list_branches

    try:
        return {"branches": await list_branches(str(_operator["id"]), repository)}
    except GitHubOAuthError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/import/github")
async def api_import_connected_github_project(
    body: GitHubConnectedImportRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Import an operator-selected GitHub source using an ephemeral OAuth token."""
    from syte.github_oauth import GitHubOAuthError, list_branches, list_repositories, token_for_account
    from syte.project_intake import analysis_metadata, analyze_project_source
    from syte.workspace import clone_or_pull

    account_id = str(_operator["id"])
    try:
        repositories = await list_repositories(account_id)
        selected = next((repo for repo in repositories if repo["full_name"].lower() == body.repository.strip().lower()), None)
        if not selected:
            raise GitHubOAuthError("Choose a repository available to the connected GitHub account.")
        branches = await list_branches(account_id, selected["full_name"])
        if body.branch.strip() not in {str(item["name"]) for item in branches}:
            raise GitHubOAuthError("Choose a branch available in the selected repository.")
        token = await token_for_account(account_id)
    except GitHubOAuthError as exc:
        raise HTTPException(400, str(exc)) from exc

    project, message = await deployment.create_project_record(
        name=body.name,
        git_url=selected["clone_url"],
        branch=body.branch.strip(),
        deploy_now=False,
    )
    if not project:
        raise HTTPException(400, message)
    ok, clone_message = await asyncio.to_thread(
        clone_or_pull, project["id"], selected["clone_url"], body.branch.strip(), http_token=token
    )
    if not ok:
        await update_project(project["id"], {"status": "stopped"})
        raise HTTPException(400, "Could not import the selected GitHub repository. Verify the account can access it.")
    try:
        analysis = await asyncio.to_thread(analyze_project_source, project["id"], source_type="github", base_directory=body.base_directory)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await update_project(project["id"], {"env_vars": analysis_metadata(analysis), "status": "created"})
    refreshed = await get_project(project["id"])
    return {"project": _enrich(refreshed or project), "analysis": analysis, "message": "Connected GitHub repository imported."}


@app.post("/api/projects/import/repository")
async def api_import_project_repository(body: ProjectRepositoryImportRequest):
    """Create a draft project, clone a HTTPS Git repository, and return its safe build analysis."""
    from urllib.parse import urlparse
    from syte.project_intake import analysis_metadata, analyze_project_source
    from syte.workspace import clone_or_pull

    parsed = urlparse(body.git_url.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise HTTPException(400, "Use a public http(s) Git repository URL.")
    project, message = await deployment.create_project_record(
        name=body.name,
        git_url=body.git_url.strip(),
        branch=body.branch.strip(),
        deploy_now=False,
    )
    if not project:
        raise HTTPException(400, message)
    ok, clone_message = await asyncio.to_thread(clone_or_pull, project["id"], body.git_url.strip(), body.branch.strip())
    if not ok:
        await update_project(project["id"], {"status": "stopped"})
        raise HTTPException(400, f"Could not import repository: {clone_message}")
    try:
        analysis = await asyncio.to_thread(
            analyze_project_source, project["id"], source_type="git", base_directory=body.base_directory
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    metadata = analysis_metadata(analysis)
    await update_project(project["id"], {"env_vars": metadata, "status": "created"})
    refreshed = await get_project(project["id"])
    return {
        "project": _enrich(refreshed or project),
        "analysis": analysis,
        "message": f"Repository imported. {clone_message}",
    }


@app.post("/api/projects/import/zip")
async def api_import_project_zip(
    name: str = Form(...),
    base_directory: str = Form("/"),
    archive: UploadFile = File(...),
):
    """Create a draft project and import a ZIP archive after path and size validation."""
    from syte.project_intake import (
        MAX_ARCHIVE_BYTES,
        analysis_metadata,
        analyze_project_source,
        extract_zip_to_project,
    )
    from syte.workspace import ensure_workspace

    filename = (archive.filename or "").lower()
    if not filename.endswith(".zip"):
        raise HTTPException(400, "Upload a .zip project archive.")
    project, message = await deployment.create_project_record(name=name.strip(), deploy_now=False)
    if not project:
        raise HTTPException(400, message)
    archive_path = ensure_workspace(project["id"]) / ".uploaded-source.zip"
    written = 0
    try:
        with archive_path.open("wb") as destination:
            while chunk := await archive.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "ZIP archive exceeds the 75 MB upload limit.")
                destination.write(chunk)
        import_result = await asyncio.to_thread(extract_zip_to_project, project["id"], archive_path)
        analysis = await asyncio.to_thread(
            analyze_project_source, project["id"], source_type="zip", base_directory=base_directory
        )
        await update_project(project["id"], {"env_vars": analysis_metadata(analysis), "status": "created"})
    except HTTPException:
        raise
    except (ValueError, OSError) as exc:
        await update_project(project["id"], {"status": "stopped"})
        raise HTTPException(400, f"Could not import ZIP archive: {exc}") from exc
    finally:
        archive_path.unlink(missing_ok=True)
    refreshed = await get_project(project["id"])
    return {
        "project": _enrich(refreshed or project),
        "analysis": analysis,
        "message": f"ZIP imported: {import_result['files']} files prepared for deployment.",
    }


@app.post("/api/projects/{project_id}/analyze")
async def api_analyze_project_source(project_id: str, body: ProjectSourceAnalysisRequest):
    from syte.project_intake import analyze_project_source

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    source_type = (project.get("env_vars") or "").find("SYTE_SOURCE_TYPE") >= 0 and "imported" or "workspace"
    try:
        analysis = await asyncio.to_thread(
            analyze_project_source, project_id, source_type=source_type, base_directory=body.base_directory
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"project_id": project_id, "analysis": analysis}


@app.post("/api/projects/{project_id}/deploy-detected")
async def api_deploy_detected_project(project_id: str, body: DetectedDeploymentRequest):
    """Persist operator-approved configuration from the detector and queue production deployment."""
    from syte.project_intake import analysis_metadata, analyze_project_source, apply_detected_build_plan
    from syte.workspace import write_env_file

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    source_type = "git" if project.get("git_url") else "zip"
    try:
        analysis = await asyncio.to_thread(
            analyze_project_source, project_id, source_type=source_type, base_directory=body.base_directory
        )
        applied = await asyncio.to_thread(apply_detected_build_plan, project_id, analysis)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    env_vars = {**analysis_metadata(analysis), **body.env_vars}
    start_command = body.start_command or str(analysis.get("start_command") or "")
    updates: dict[str, Any] = {
        "env_vars": env_vars,
        "start_command": start_command,
        "deploy_type": "docker",
        "dockerfile_path": applied["dockerfile_path"],
        "status": "created",
    }
    if body.domain:
        updates["domain"] = normalize_domain(body.domain)
    await update_project(project_id, updates)
    write_env_file(project_id, env_vars)
    updated, deploy_message = await deployment.issue_deploy(project_id, trigger="project-import")
    if not updated:
        raise HTTPException(400, deploy_message)
    refreshed = await get_project(project_id)
    return {
        "project": _enrich(refreshed or updated),
        "analysis": analysis,
        "message": deploy_message,
        "stream_url": f"/api/projects/{project_id}/logs/stream",
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
    from syte.agent_jobs import cancel_agent_job
    from syte.cloud_agent import get_agent_status, stop_agent

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    # Cancel the durable job first: stop_agent() alone leaves the background
    # task in agent_jobs._running, which keeps reporting the agent as busy.
    await cancel_agent_job(project_id)
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
    """Cancel the active Syte cloud turn without discarding conversation history.

    Routes through ``cancel_agent_job`` so the durable background task is
    cancelled too. Calling ``interrupt_agent`` on its own only stopped the
    in-process turn, so the job kept running and the agent stayed "busy" —
    the Stop button appeared to do nothing.
    """
    from syte.agent_jobs import cancel_agent_job
    from syte.cloud_agent import get_agent_status

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    _ok, message = await cancel_agent_job(project_id)
    # "Nothing was running" is a successful stop from the caller's point of
    # view: the turn is not active any more, which is all Stop has to
    # guarantee. Raising 400 here surfaced a bogus "Could not stop response"
    # error and left the composer locked.
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


@app.get("/api/projects/{project_id}/agent/turso_debug")
async def api_agent_turso_debug_public(project_id: str):
    """Diagnose why the 'brain' indicator is red — connectivity + schema check.

    Returns whether Turso is configured, whether the configured
    database/token pair is actually reachable right now (a live round-trip,
    not just "is a URL set"), whether schema initialization succeeded for
    every statement, and the specific error text for anything that failed.
    Meant to be called from the browser console
    (``fetch('/api/projects/<id>/agent/turso_debug').then(r=>r.json()).then(console.log)``)
    or surfaced by the GUI when the brain icon is red, since ``all_saved:
    false`` alone does not say *why* a message failed to sync.
    """
    from syte.turso_store import turso_debug_status

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "project_id": project_id, **(await turso_debug_status())}


@app.get("/api/projects/{project_id}/agent/failures")
async def api_agent_failures_public(
    project_id: str,
    session: str = "last",
    limit: int = 200,
    kind: str = "",
):
    """Per-session failure log — every failed task, tool, request and subagent.

    Surfaced in the GUI by double-clicking the brain icon. Activity events are
    pruned and replay-window limited, so this failure-only table is what answers
    "what actually went wrong in this session?".

    ``session``: ``last`` (default), a session number, or ``all``.
    """
    from syte.agent_failures import failure_summary, list_failures

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    scope = session or "last"
    return {
        "ok": True,
        "project_id": project_id,
        "session": scope,
        "failures": await list_failures(
            project_id, session=scope, limit=max(1, min(limit, 1000)), kind=kind or ""
        ),
        "summary": await failure_summary(project_id, session=scope),
    }


@app.delete("/api/projects/{project_id}/agent/failures")
async def api_agent_failures_clear_public(project_id: str, session: str = "last"):
    """Clear the failure log for a session (or ``all``)."""
    from syte.agent_failures import clear_failures

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    removed = await clear_failures(project_id, session=session or "last")
    return {"ok": True, "project_id": project_id, "removed": removed}


@app.get("/api/projects/{project_id}/agent/subagents")
async def api_agent_subagents_public(
    project_id: str, session: str = "last", limit: int = 50,
):
    """Durable list of delegated subagent tasks for this project/session.

    The GUI subagent tab used to be revealed only by replayed activity events,
    so a subagent whose events aged out of the replay window became invisible.
    This endpoint is the durable source of truth the tab now checks on load.
    """
    from syte.subagent_store import list_tasks

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    tasks = await list_tasks(project_id, session=session or "last", limit=limit)
    running = [t for t in tasks if t.get("status") == "running"]
    return {
        "ok": True,
        "project_id": project_id,
        "session": session or "last",
        "subagents": tasks,
        "count": len(tasks),
        "running": len(running),
        "failed": len([t for t in tasks if t.get("status") in {"failed", "timeout"}]),
    }


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
async def api_agent_sessions_public(
    project_id: str, limit: int = 50, resume: int = 0,
):
    """List durable Turso agent-session UUIDs plus layered memory for resume."""
    from syte.agent_memory import project_memory_snapshot
    from syte.turso_store import list_sessions_for_project, turso_configured

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    memory = await project_memory_snapshot(project_id)
    base = {
        "ok": True,
        "project_id": project_id,
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
    sessions = await list_sessions_for_project(project_id, limit=limit)
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


@app.get("/api/projects/{project_id}/agent/activity/stream")
async def api_agent_activity_stream_public(
    project_id: str, request: Request, since_id: int = 0, session: str | None = None,
):
    """SSE stream for live agent activity (complements Turso session polling).

    Frames for ``token_delta`` / ``thinking_delta`` are minimal-delta (raw text +
    tiny header). The response body is gzip/brotli compressed when the client
    sends ``Accept-Encoding``.
    """
    from syte.agent_activity import activity_sse_generator, sse_stream_response

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    async def _gen():
        async for frame in activity_sse_generator(
            project_id, since_id=since_id, session=session,
        ):
            yield frame

    return sse_stream_response(request, _gen())


@app.get("/api/agent_session/{session_id}")
async def api_get_agent_session(
    session_id: str,
    since_id: int = 0,
    uuid: str | None = None,
    project_id: str | None = None,
):
    """Fetch a durable agent activity session by UUID from Turso.

    This is the Turso access route that replaces the old activity SSE stream.
    Asking the agent something still happens over the normal request/response
    API (``agent_communicate`` / ``agent_change`` / the GUI chat endpoint,
    which return this session's ``id``); to observe what happened, poll this
    route by that ``id`` instead of opening a streaming connection. Pass
    ``since_id`` to fetch only events recorded after a previously-seen event
    id (useful for polling a session that is still ``open``).
    Tokens/cookies are host-global in this single-tenant service; pass ``uuid``
    or ``project_id`` to additionally verify project ownership.
    """
    from syte.turso_store import get_session

    session = await get_session(session_id, since_id=since_id)
    if not session:
        raise HTTPException(404, "Agent session not found")
    expected_project_id = project_id or uuid
    if expected_project_id and str(session.get("project_id") or "") != expected_project_id:
        raise HTTPException(403, "Agent session does not belong to the requested project")
    return {"ok": True, **session}


class AgentChatRequest(BaseModel):
    message: str
    model_profile: str | None = None
    thinking_level: int | None = Field(None, ge=1, le=6, description="1 minimal … 6 xhigh")
    improve_from_screenshot: bool = False
    visual_analysis_id: str | None = None


class DesignProfileRequest(BaseModel):
    theme_key: str | None = None
    style_key: str | None = None


class AgentTestRequest(BaseModel):
    model_profile: str | None = None


class AgentAccessRequest(BaseModel):
    action: str
    url: str | None = None
    lines: int | None = None
    include_screenshot: bool | None = None


class AgentAccessConfigRequest(BaseModel):
    custom_urls: list[str] = []


class AgentServiceRequest(BaseModel):
    action: str
    command: str | None = None
    cwd: str = "app"
    lines: int | None = None
    timeout: int | None = None


class AgentQuestionAnswerRequest(BaseModel):
    answer: str | int | float | list[str] | dict


class AgentMcpRegisterRequest(BaseModel):
    name: str
    command: str
    description: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    transport: str = "stdio"


class AgentMcpUpdateRequest(BaseModel):
    name: str | None = None
    command: str | None = None
    description: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    transport: str | None = None


class AgentMcpConnectRequest(BaseModel):
    addon: str


class AgentMcpCallRequest(BaseModel):
    addon: str
    tool: str
    arguments: dict = Field(default_factory=dict)


class AgentSkillEnableRequest(BaseModel):
    parameters: dict[str, str] = Field(default_factory=dict)


class AgentSkillAddRequest(BaseModel):
    name: str
    content: str
    description: str = ""
    parameters: dict[str, str] = Field(default_factory=dict)
    enable: bool = True
    skill_id: str | None = None


class AgentSkillUpdateRequest(BaseModel):
    name: str | None = None
    content: str | None = None
    description: str | None = None
    parameters: dict[str, str] | None = None


class AgentProfileRequest(BaseModel):
    name: str = ""
    icon: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMcpCredentialRequest(BaseModel):
    service_name: str
    display_name: str = ""
    description: str = ""
    api_key: str = ""
    api_url: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMcpCredentialBatchRequest(BaseModel):
    """Accepted JSON for an external service to bulk-save credentials + profile.

    This is the exact accepted format documented in docs/turso-persistence.md.
    """
    name: str = ""
    icon: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    credentials: list[dict[str, Any]] = Field(default_factory=list)


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
        include_screenshot=bool(body.include_screenshot),
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


@app.get("/api/projects/{project_id}/agent/screenshots")
async def api_agent_screenshots_list(project_id: str, limit: int = 50):
    from syte.agent_artifacts import list_screenshots

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "project_id": project_id, "screenshots": await list_screenshots(project_id, limit=limit)}


@app.get("/api/projects/{project_id}/agent/screenshots/{screenshot_id}")
async def api_agent_screenshot_get(project_id: str, screenshot_id: str, variant: str = "full"):
    from fastapi.responses import Response

    from syte.agent_artifacts import get_screenshot, read_screenshot_bytes

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    record = await get_screenshot(project_id, screenshot_id)
    if not record:
        raise HTTPException(404, "Screenshot not found")
    data = read_screenshot_bytes(record, variant="thumb" if variant == "thumb" else "full")
    if not data:
        raise HTTPException(404, "Screenshot file missing")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/projects/{project_id}/agent/plans")
async def api_agent_plans_list(project_id: str, limit: int = 50):
    from syte.agent_artifacts import list_plans

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "project_id": project_id, "plans": await list_plans(project_id, limit=limit)}


@app.get("/api/projects/{project_id}/agent/questions")
async def api_agent_questions_list(project_id: str, status: str | None = None, limit: int = 50):
    from syte.agent_artifacts import list_questions

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "ok": True,
        "project_id": project_id,
        "questions": await list_questions(project_id, status=status, limit=limit),
    }


@app.post("/api/projects/{project_id}/agent/questions/{question_id}/answer")
async def api_agent_question_answer(
    project_id: str, question_id: str, body: AgentQuestionAnswerRequest
):
    from syte.agent_activity import record_agent_event
    from syte.agent_artifacts import answer_question
    from syte.cloud_agent_store import current_turso_session_id

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await answer_question(project_id, question_id, body.answer)
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "not_found" else 400, result.get("message") or "Failed")
    if not result.get("already_answered"):
        turso_session_id = await current_turso_session_id(project_id)
        await record_agent_event(
            project_id,
            "question_answered",
            role="user",
            title="Answer",
            detail=str(result.get("answer") or "")[:4000],
            payload={"question_id": question_id, "answer": result.get("answer")},
            source="gui",
            turso_session_id=turso_session_id,
        )
    return result


@app.get("/api/projects/{project_id}/agent/stops")
async def api_agent_stops_list(project_id: str, limit: int = 50):
    from syte.agent_artifacts import list_session_stops

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "project_id": project_id, "stops": await list_session_stops(project_id, limit=limit)}


@app.get("/api/projects/{project_id}/agent/mcp")
async def api_agent_mcp_list(project_id: str):
    from syte.agent_artifacts import list_mcp_addons
    from syte.agent_skills import mcp_server_config
    from syte.cloud_agent import agent_root

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "ok": True,
        "project_id": project_id,
        "addons": await list_mcp_addons(project_id),
        "mcp_server": mcp_server_config(project_id, agent_root(project_id)),
        "documentation": "/api/#agent-mcp",
        "project_routes": {
            "skills": f"/api/projects/{project_id}/agent/skills",
            "service": f"/api/projects/{project_id}/agent/service",
            "access": f"/api/projects/{project_id}/agent/access",
            "connect": f"/api/projects/{project_id}/agent/mcp/connect",
            "call": f"/api/projects/{project_id}/agent/mcp/call",
        },
    }


@app.post("/api/projects/{project_id}/agent/mcp")
async def api_agent_mcp_register(project_id: str, body: AgentMcpRegisterRequest):
    from syte.agent_artifacts import register_mcp_addon

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    addon = await register_mcp_addon(
        project_id,
        name=body.name,
        description=body.description,
        command=body.command,
        args=body.args,
        env=body.env,
        transport=body.transport,
    )
    return {"ok": True, **addon}


@app.post("/api/projects/{project_id}/agent/mcp/connect")
async def api_agent_mcp_connect(project_id: str, body: AgentMcpConnectRequest):
    from syte.agent_artifacts import connect_mcp_addon

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await connect_mcp_addon(project_id, body.addon)
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "not_found" else 400, result.get("message") or "Failed")
    return result


@app.post("/api/projects/{project_id}/agent/mcp/call")
async def api_agent_mcp_call(project_id: str, body: AgentMcpCallRequest):
    from syte.agent_artifacts import call_mcp_addon

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return await call_mcp_addon(project_id, body.addon, body.tool, body.arguments)


@app.put("/api/projects/{project_id}/agent/mcp/{addon_id}")
async def api_agent_mcp_update(project_id: str, addon_id: str, body: AgentMcpUpdateRequest):
    from syte.agent_artifacts import update_mcp_addon

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await update_mcp_addon(
        project_id,
        addon_id,
        name=body.name,
        description=body.description,
        command=body.command,
        args=body.args,
        env=body.env,
        transport=body.transport,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "not_found" else 400
        raise HTTPException(status, result.get("message") or "Failed")
    return result


@app.delete("/api/projects/{project_id}/agent/mcp/{addon_id}")
async def api_agent_mcp_disconnect(project_id: str, addon_id: str):
    from syte.agent_artifacts import disconnect_mcp_addon

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await disconnect_mcp_addon(project_id, addon_id)
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "not_found" else 400, result.get("message") or "Failed")
    return result


# ---------------------------------------------------------------------------
# Project profile (Turso) — user-facing name + icon
# ---------------------------------------------------------------------------


@app.get("/api/projects/{project_id}/agent/profile")
async def api_agent_profile_get(project_id: str):
    from syte.turso_store import get_project_profile

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    profile = await get_project_profile(project_id)
    if not profile:
        return {"ok": True, "project_id": project_id, "profile": None, "note": "No Turso profile saved yet"}
    return {"ok": True, "project_id": project_id, "profile": profile}


@app.put("/api/projects/{project_id}/agent/profile")
async def api_agent_profile_upsert(project_id: str, body: AgentProfileRequest):
    from syte.turso_store import upsert_project_profile

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    profile = await upsert_project_profile(
        project_id,
        name=body.name,
        icon=body.icon,
        metadata=body.metadata,
    )
    if not profile:
        return {
            "ok": False,
            "error": "turso_unavailable",
            "message": "Turso is not configured — cannot save profile.",
        }
    return {"ok": True, "project_id": project_id, "profile": profile}


# ---------------------------------------------------------------------------
# MCP credentials (Turso) — external service API keys the agent can use
# ---------------------------------------------------------------------------


@app.get("/api/projects/{project_id}/agent/credentials")
async def api_agent_credentials_list(project_id: str):
    from syte.turso_store import list_mcp_credentials

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    creds = await list_mcp_credentials(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        "credentials": creds,
        "documentation": "docs/turso-persistence.md#user-mcp-credentials",
    }


@app.post("/api/projects/{project_id}/agent/credentials")
async def api_agent_credential_save(project_id: str, body: AgentMcpCredentialRequest):
    from syte.turso_store import save_mcp_credential

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await save_mcp_credential(
        project_id,
        service_name=body.service_name,
        display_name=body.display_name,
        description=body.description,
        api_key=body.api_key,
        api_url=body.api_url,
        metadata=body.metadata,
    )
    if not result:
        return {
            "ok": False,
            "error": "turso_unavailable",
            "message": "Turso is not configured — cannot save credential.",
        }
    return {"ok": True, **result}


@app.post("/api/projects/{project_id}/agent/credentials/batch")
async def api_agent_credential_batch(project_id: str, body: AgentMcpCredentialBatchRequest):
    """Accepted JSON for an external service to bulk-save credentials and profile.

    Exact accepted JSON schema — see docs/turso-persistence.md.
    """
    from syte.turso_store import save_mcp_credential, upsert_project_profile

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    results: dict[str, Any] = {"ok": True, "project_id": project_id, "profile": None, "credentials": []}

    if body.name or body.icon or body.metadata:
        profile = await upsert_project_profile(
            project_id,
            name=body.name,
            icon=body.icon,
            metadata=body.metadata,
        )
        results["profile"] = profile

    for cred in body.credentials:
        svc = str(cred.get("service_name") or "").strip()
        if not svc:
            continue
        saved = await save_mcp_credential(
            project_id,
            service_name=svc,
            display_name=str(cred.get("display_name") or svc),
            description=str(cred.get("description") or ""),
            api_key=str(cred.get("api_key") or ""),
            api_url=str(cred.get("api_url") or ""),
            metadata=cred.get("metadata") if isinstance(cred.get("metadata"), dict) else {},
        )
        if saved:
            results["credentials"].append(saved)

    return results


@app.get("/api/projects/{project_id}/agent/credentials/{service_name}")
async def api_agent_credential_get(project_id: str, service_name: str):
    from syte.turso_store import get_mcp_credential

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    cred = await get_mcp_credential(project_id, service_name)
    if not cred:
        raise HTTPException(404, f"Credential not found for service '{service_name}'")
    return {
        "ok": True,
        "project_id": project_id,
        "service_name": service_name,
        "credential": cred,
    }


@app.delete("/api/projects/{project_id}/agent/credentials/{service_name}")
async def api_agent_credential_delete(project_id: str, service_name: str):
    from syte.turso_store import delete_mcp_credential

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    ok = await delete_mcp_credential(project_id, service_name)
    return {"ok": ok, "project_id": project_id, "service_name": service_name}


@app.get("/api/projects/{project_id}/agent/skills")
async def api_agent_skills_list(project_id: str):
    from syte.agent_skills import get_project_skills

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "project_id": project_id, "skills": await get_project_skills(project_id)}


@app.post("/api/projects/{project_id}/agent/skills")
async def api_agent_skill_add(project_id: str, body: AgentSkillAddRequest):
    from syte.agent_skills import add_custom_skill

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await add_custom_skill(
        project_id,
        name=body.name,
        description=body.description,
        content=body.content,
        parameters=body.parameters,
        enable=body.enable,
        skill_id=body.skill_id,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("message") or "Failed to add skill")
    return result


@app.put("/api/projects/{project_id}/agent/skills/{skill_id}")
async def api_agent_skill_update(project_id: str, skill_id: str, body: AgentSkillUpdateRequest):
    from syte.agent_skills import update_custom_skill

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await update_custom_skill(
        project_id,
        skill_id,
        name=body.name,
        description=body.description,
        content=body.content,
        parameters=body.parameters,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "not_found" else 400
        raise HTTPException(status, result.get("message") or "Failed")
    return result


@app.post("/api/projects/{project_id}/agent/skills/{skill_id}/enable")
async def api_agent_skill_enable(project_id: str, skill_id: str, body: AgentSkillEnableRequest):
    from syte.agent_skills import enable_skill

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    result = await enable_skill(project_id, skill_id, body.parameters)
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "not_found" else 400, result.get("message") or "Failed")
    return result


@app.delete("/api/projects/{project_id}/agent/skills/{skill_id}")
async def api_agent_skill_disable(project_id: str, skill_id: str, purge: bool = False):
    from syte.agent_skills import delete_custom_skill, disable_skill

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if purge:
        result = await delete_custom_skill(project_id, skill_id)
    else:
        result = await disable_skill(project_id, skill_id)
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "not_found" else 400, result.get("message") or "Failed")
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
            thinking_level=body.thinking_level,
            source="gui",
            background=not wait,
            improve_from_screenshot=bool(body.improve_from_screenshot),
            visual_analysis_id=body.visual_analysis_id,
        )
    except Exception as exc:
        return {"ok": False, "error": "agent_communicate_failed", "message": str(exc)}
    if not result.get("ok"):
        return result
    return result


@app.get("/api/projects/{project_id}/agent/visual_analyses")
async def api_list_visual_analyses_gui(project_id: str, limit: int = 20):
    from syte.agent_memory import list_visual_analyses

    if not await get_project(project_id):
        raise HTTPException(404, "Project not found")
    return {
        "ok": True,
        "project_id": project_id,
        "analyses": await list_visual_analyses(project_id, limit=limit),
    }


@app.post("/api/projects/{project_id}/agent/visual_analyze")
async def api_visual_analyze_gui(project_id: str, route: str = "/", capture: bool = True):
    from syte.cloud_agent import _tool_screenshot_preview, selected_model_metadata

    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    model = await selected_model_metadata(project)
    result = await _tool_screenshot_preview(
        project_id,
        {"route": route, "viewports": ["desktop", "phone"]},
        {"session_number": 0, "model": model},
    )
    from syte.agent_memory import get_visual_analysis

    analyses = []
    for shot in result.get("screenshots") or []:
        aid = shot.get("visual_analysis_id")
        if aid:
            row = await get_visual_analysis(str(aid))
            if row:
                analyses.append(row)
    if not analyses and not result.get("ok"):
        raise HTTPException(400, result.get("message") or "Screenshot capture failed")
    return {"ok": True, "project_id": project_id, "analyses": analyses, "capture": result}


@app.get("/api/projects/{project_id}/agent/design_profile")
async def api_get_design_profile_gui(project_id: str):
    from syte.agent_memory import get_design_profile
    from syte.design_profile import list_style_profiles

    if not await get_project(project_id):
        raise HTTPException(404, "Project not found")
    return {
        "ok": True,
        "project_id": project_id,
        "profile": await get_design_profile(project_id),
        "style_profiles": list_style_profiles(),
    }


@app.post("/api/projects/{project_id}/agent/design_profile")
async def api_set_design_profile_gui(project_id: str, body: DesignProfileRequest):
    from syte.design_profile import apply_theme_profile

    if not await get_project(project_id):
        raise HTTPException(404, "Project not found")
    profile = await apply_theme_profile(
        project_id,
        theme_key=body.theme_key,
        style_key=body.style_key,
        source="gui",
    )
    return {"ok": True, "project_id": project_id, "profile": profile}


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


@app.get("/api/projects/{project_id}/deployments")
async def api_deployment_history(project_id: str, limit: int = 20):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {"project_id": project_id, "deployments": await list_deployment_runs(project_id, limit)}


@app.get("/api/projects/{project_id}/health")
async def api_project_health(project_id: str):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    url = _project_url(_enrich(project))
    path = project.get("healthcheck_path") or "/"
    url = url.rstrip("/") + (path if path.startswith("/") else f"/{path}")
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Syte-Health/1.0"})
        result = {"healthy": response.status_code < 500, "status_code": response.status_code, "detail": response.reason_phrase}
    except Exception as exc:
        result = {"healthy": False, "status_code": None, "detail": str(exc)}
    return {"project_id": project_id, "url": url, **result}


@app.put("/api/projects/{project_id}/deployment-config")
async def api_deployment_config(project_id: str, body: DeploymentConfigRequest):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    updates = body.model_dump(exclude_none=True)
    if "auto_deploy" in updates:
        updates["auto_deploy"] = int(updates["auto_deploy"])
    if updates.get("deploy_type") not in {None, "shell", "docker", "compose", "image"}:
        raise HTTPException(400, "deploy_type must be shell, docker, compose, or image")
    updated = await update_project(project_id, updates)
    return {"ok": True, "project": _enrich(updated or project)}


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


def _index_response() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text()
    html = html.replace("__VERSION__", __version__)
    return HTMLResponse(html, headers={"Cache-Control": NO_CACHE, "Pragma": "no-cache"})


@app.get("/")
async def index():
    return _index_response()


_GUI_PATHS = [
    "/home", "/projects", "/settings", "/profile", "/session", "/servers", "/users",
    "/audit", "/ssh-keys", "/ai", "/tags", "/git", "/registry", "/secrets",
    "/dns", "/s3", "/certificates", "/notifications", "/billing", "/license",
    "/sso", "/docs", "/support",
]


app.mount("/static", VersionedStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
async def gui_path(path: str):
    normalized = "/" + path.strip("/")
    if normalized in _GUI_PATHS or (normalized.startswith("/projects/") and normalized.count("/") == 3):
        return _index_response()
    raise HTTPException(404, "Not found")

