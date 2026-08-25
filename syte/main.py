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
from syte.domain_utils import build_direct_url, build_https_url, is_valid_ip, normalize_domain
from syte.self_update import update_syte
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
from syte import workspace_api
from syte import platform_api
from syte.platform.backup_scheduler import backup_scheduler_loop
from syte.platform.store import ensure_bootstrap, init_platform_db
from syte.log_stream import stream_preview_logs, stream_project_logs
from syte.rate_limit import RateLimitMiddleware
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
    in_app_notifications: bool = False


class ProjectRepositoryImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    git_url: str = Field(min_length=8, max_length=2048)
    branch: str = Field(default="main", min_length=1, max_length=255)
    base_directory: str = Field(default="/", max_length=255)
    in_app_notifications: bool = False


class GitHubConnectedImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    repository: str = Field(min_length=3, max_length=255)
    branch: str = Field(default="main", min_length=1, max_length=255)
    base_directory: str = Field(default="/", max_length=255)
    in_app_notifications: bool = False


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
    in_app_notifications: bool | None = None


class NotificationEmailSettingsRequest(BaseModel):
    enabled: bool = False
    recipients: str = Field(default="", max_length=2000)
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default="", max_length=255)
    smtp_password: str | None = Field(default=None, max_length=1024)
    sender: str = Field(default="", max_length=320)
    use_tls: bool = True


class NotificationWebhookSettingsRequest(BaseModel):
    enabled: bool = False
    urls: str = Field(default="", max_length=5000)


class NotificationSettingsRequest(BaseModel):
    email: NotificationEmailSettingsRequest = Field(default_factory=NotificationEmailSettingsRequest)
    webhook: NotificationWebhookSettingsRequest = Field(default_factory=NotificationWebhookSettingsRequest)


class PwaPushSubscriptionRequest(BaseModel):
    subscription: dict[str, Any]


class NotificationReadRequest(BaseModel):
    event_ids: list[str] = Field(default_factory=list, max_length=250)


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

class GitHubSettingsRequest(BaseModel):
    repo: str | None = None
    token: str | None = None


class GitHubMergeRequest(BaseModel):
    method: str = "squash"
    force: bool = False


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


@app.get("/api", include_in_schema=False)
@app.get("/api/", include_in_schema=False)
async def api_documentation():
    """API reference documentation page."""
    html = (STATIC_DIR / "api-docs.html").read_text()
    html = html.replace("__VERSION__", __version__)
    return HTMLResponse(html, headers={"Cache-Control": NO_CACHE})


@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    return HTMLResponse(
        (STATIC_DIR / "manifest.webmanifest").read_text(),
        media_type="application/manifest+json",
        headers={"Cache-Control": NO_CACHE},
    )


@app.get("/service-worker.js", include_in_schema=False)
async def pwa_service_worker():
    return HTMLResponse(
        (STATIC_DIR / "service-worker.js").read_text(),
        media_type="application/javascript",
        headers={"Cache-Control": NO_CACHE},
    )


@app.get("/api/notifications/settings")
async def api_notification_settings(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    from syte.notifications import notification_settings_payload

    return await notification_settings_payload()


@app.put("/api/notifications/settings")
async def api_save_notification_settings(
    body: NotificationSettingsRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.notifications import save_notification_settings

    try:
        settings_payload = await save_notification_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "settings": settings_payload, "message": "Notification settings saved."}


@app.get("/api/notifications")
async def api_list_notifications(
    limit: int = 100,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.database import list_notification_events

    events = await list_notification_events(limit)
    return {"notifications": events, "unread_count": sum(1 for item in events if not item["is_read"])}


@app.post("/api/notifications/read")
async def api_mark_notifications_read(
    body: NotificationReadRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from syte.database import mark_notification_events_read

    return {"ok": True, "updated": await mark_notification_events_read(body.event_ids or None)}


@app.get("/api/notifications/push/vapid-public-key")
async def api_pwa_vapid_public_key(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    from syte.notifications import vapid_public_key

    return {"public_key": await vapid_public_key()}


@app.post("/api/notifications/push-subscriptions")
async def api_save_pwa_push_subscription(
    body: PwaPushSubscriptionRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    from urllib.parse import urlparse
    from syte.database import upsert_pwa_push_subscription

    subscription = body.subscription
    endpoint = str(subscription.get("endpoint") or "")
    keys = subscription.get("keys") or {}
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(422, "Use a valid HTTPS browser push subscription.")
    await upsert_pwa_push_subscription(str(_operator["id"]), subscription)
    return {"ok": True, "message": "This device is registered for Sycord PWA notifications."}


@app.post("/api/notifications/test")
async def api_test_notification(_operator: dict[str, Any] = Depends(verify_operator_session_or_token)):
    from syte.notifications import publish_project_event

    await publish_project_event(
        "notification.test",
        project_id=None,
        message="Sycord notifications are connected on this device.",
        force_in_app=True,
    )
    return {"ok": True, "message": "Test notification queued for configured channels."}


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
        "disk_used_gb": stats["disk_used_gb"],
        "disk_total_gb": stats["disk_total_gb"],
        "disk_percent": stats["disk_percent"],
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
    from syte.certificates import cloudflare_tls_status
    from syte.preview_domains import resolve_preview_zone

    ip = _resolved_ip()
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    preview_base_domain = normalize_domain(await get_setting("preview_base_domain", ""))
    preview_zone = await resolve_preview_zone()
    cf_status = await cloudflare_tls_status()
    return {
        "public_ip": ip,
        "admin_email": await get_setting("admin_email", settings.admin_email),
        "gui_domain": gui_domain,
        "preview_base_domain": preview_base_domain,
        "preview_zone": preview_zone,
        "preview_host_pattern": f"preview{{letter}}-appname.{preview_zone}" if preview_zone else "",
        "preview_wildcard_tls": await get_setting("preview_wildcard_tls", "auto"),
        "custom_tls_host": await get_setting("custom_tls_host", ""),
        "custom_tls_port": await get_setting("custom_tls_port", ""),
        "cloudflare_api_token_set": cf_status["token_configured"],
        "cloudflare_tls": cf_status,
        "preview_dns_hint": (
            f"Point wildcard *.{preview_zone} A record to this server (DNS only)."
            if preview_zone else "Set a preview base domain or GUI domain for HTTPS previews."
        ),
        "direct_url": build_direct_url(ip, settings.port),
        "domain_url": build_https_url(gui_domain) if gui_domain else "",
        "github_repo": (await get_setting("github_repo", "")).strip(),
        "github_token_set": bool((await get_setting("github_token", "")).strip()),
        "github_token_source": await _github_token_source(),
        "version": __version__,
    }

async def _github_token_source() -> str:
    """Return the configured GitHub token source without exposing its value."""
    from syte.github_prs import resolve_token

    _token, source = await resolve_token()
    return source


@app.put("/api/settings")
async def save_settings(body: SettingsRequest):
    from syte.certificates import cloudflare_tls_status

    messages: list[str] = []
    proxy_updated = False
    if body.public_ip is not None:
        ip = body.public_ip.strip()
        if ip and not is_valid_ip(ip):
            raise HTTPException(400, "Public IP must be an IPv4 address.")
        await set_setting("public_ip", ip)
        settings.public_ip = ip
        messages.append(f"Public IP set to {ip}" if ip else "Public IP cleared (auto-detect).")
        proxy_updated = True
    if body.admin_email is not None:
        await set_setting("admin_email", body.admin_email)
        settings.admin_email = body.admin_email
        messages.append(f"Admin email set to {body.admin_email}.")
    if body.gui_domain is not None:
        domain = normalize_domain(body.gui_domain)
        if domain:
            email = settings.admin_email
            if not email or "@" not in email or email.endswith("@localhost"):
                raise HTTPException(400, "A valid admin email is required before setting a GUI domain.")
            await set_setting("gui_domain", domain)
            ok, message = await set_gui_domain(domain, email)
            if not ok:
                await set_setting("gui_domain", "")
                raise HTTPException(500, message)
            messages.append(message)
        else:
            await set_setting("gui_domain", "")
            ok, message = await apply_proxy_config()
            messages.append("GUI domain removed." if ok else message)
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
        messages.append(f"Preview base domain set to {zone}." if zone else "Preview base domain cleared.")
    if body.cloudflare_api_token is not None:
        await set_setting("cloudflare_api_token", body.cloudflare_api_token.strip())
        proxy_updated = True
        messages.append("Cloudflare API token saved." if body.cloudflare_api_token.strip() else "Cloudflare API token cleared.")
    if body.preview_wildcard_tls is not None:
        mode = body.preview_wildcard_tls.strip().lower() or "auto"
        await set_setting("preview_wildcard_tls", mode)
        proxy_updated = True
        messages.append(f"Preview wildcard TLS mode: {mode}.")
    if body.custom_tls_host is not None:
        await set_setting("custom_tls_host", normalize_domain(body.custom_tls_host))
        proxy_updated = True
        messages.append("Global custom TLS host updated.")
    if body.custom_tls_port is not None:
        await set_setting("custom_tls_port", body.custom_tls_port.strip())
        proxy_updated = True
        messages.append("Global custom TLS port updated.")
    if proxy_updated or not messages:
        ok, message = await apply_proxy_config()
        messages.append(message)
    else:
        ok = True
    return {"ok": ok, "messages": messages, "cloudflare_tls": await cloudflare_tls_status()}

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
        in_app_notifications=body.in_app_notifications,
    )
    if not project:
        raise HTTPException(500, message)
    from syte.notifications import publish_project_event
    await publish_project_event(
        "project.created",
        project_id=project["id"],
        message=f"Project {project.get('name', project['id'])} was created.",
        payload={"source": "project-create"},
    )
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
        in_app_notifications=body.in_app_notifications,
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
    from syte.notifications import publish_project_event
    await publish_project_event(
        "project.imported",
        project_id=project["id"],
        message=f"GitHub repository {selected['full_name']} was imported for {project.get('name', project['id'])}.",
        payload={"repository": selected["full_name"], "branch": body.branch.strip()},
    )
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
        in_app_notifications=body.in_app_notifications,
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
    from syte.notifications import publish_project_event
    await publish_project_event(
        "project.imported",
        project_id=project["id"],
        message=f"Repository was imported for {project.get('name', project['id'])}.",
        payload={"repository": body.git_url.strip(), "branch": body.branch.strip()},
    )
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
    in_app_notifications: bool = Form(False),
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
    project, message = await deployment.create_project_record(
        name=name.strip(), deploy_now=False, in_app_notifications=in_app_notifications
    )
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
    from syte.notifications import publish_project_event
    await publish_project_event(
        "project.imported",
        project_id=project["id"],
        message=f"ZIP source was imported for {project.get('name', project['id'])}.",
        payload={"files": import_result["files"]},
    )
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
    if body.in_app_notifications is not None:
        updates["in_app_notifications"] = int(body.in_app_notifications)
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
    from syte.notifications import publish_project_event
    await publish_project_event(
        "project.updated",
        project_id=project_id,
        message=f"Project {project.get('name', project_id)} was updated.",
        payload={"changed_fields": sorted(updates)},
    )
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
    from syte.notifications import publish_project_event
    await publish_project_event("project.started", project_id=project_id, message=f"Project {project.get('name', project_id)} was started.")
    return {"project": _enrich(project), "message": message}


@app.post("/api/projects/{project_id}/stop")
async def api_stop(project_id: str):
    project, message = await deployment.stop_service(project_id)
    if not project:
        raise HTTPException(404, message)
    from syte.notifications import publish_project_event
    await publish_project_event("project.stopped", project_id=project_id, message=f"Project {project.get('name', project_id)} was stopped.")
    return {"project": _enrich(project), "message": message}


@app.post("/api/projects/{project_id}/update")
async def api_git_update(project_id: str):
    """Pull newest git version and restart app. Data is preserved on VM."""
    project, message = await deployment.update_service(project_id)
    if not project:
        raise HTTPException(404, message)
    from syte.notifications import publish_project_event
    await publish_project_event("project.repository_updated", project_id=project_id, message=f"Repository for {project.get('name', project_id)} was updated.")
    return {"project": _enrich(project), "message": message}


@app.post("/api/projects/{project_id}/domain")
async def api_set_domain(project_id: str, body: DomainRequest):
    project, message = await deployment.set_custom_domain(
        project_id, body.domain, body.email
    )
    if not project:
        raise HTTPException(404, message)
    from syte.notifications import publish_project_event
    await publish_project_event("project.domain_updated", project_id=project_id, message=f"Domain for {project.get('name', project_id)} was updated to {project.get('domain') or body.domain}.")
    return {"project": _enrich(project), "message": message}


@app.delete("/api/projects/{project_id}")
async def api_delete(project_id: str):
    project = await get_project(project_id)
    ok, message = await deployment.remove_service(project_id)
    if not ok:
        raise HTTPException(404, message)
    from syte.notifications import publish_project_event
    await publish_project_event(
        "project.deleted",
        project_id=None,
        message=f"Project {(project or {}).get('name', project_id)} was removed.",
        payload={"project_id": project_id},
        force_in_app=bool(project and project.get("in_app_notifications")),
    )
    return {"ok": True, "message": message}


@app.post("/api/projects/{project_id}/preview/start")
async def api_preview_start(project_id: str):
    from syte.preview_manager import start_preview
    ok, message, meta = await start_preview(project_id)
    if not ok:
        raise HTTPException(400, message)
    from syte.notifications import publish_project_event
    project = await get_project(project_id)
    await publish_project_event("project.preview_started", project_id=project_id, message=f"Preview for {(project or {}).get('name', project_id)} was started.", payload=meta)
    return {"ok": True, "message": message, **meta}


@app.post("/api/projects/{project_id}/preview/stop")
async def api_preview_stop(project_id: str):
    from syte.preview_manager import get_preview_status, stop_preview_async

    await stop_preview_async(project_id)
    meta, _ = await get_preview_status(project_id)
    from syte.notifications import publish_project_event
    project = await get_project(project_id)
    await publish_project_event("project.preview_stopped", project_id=project_id, message=f"Preview for {(project or {}).get('name', project_id)} was stopped.", payload=meta or {})
    return {"ok": True, "message": "Preview stopped", **(meta or {})}


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
    if not result["healthy"]:
        from syte.notifications import publish_project_event
        await publish_project_event(
            "project.health_failed",
            project_id=project_id,
            message=f"Health check for {project.get('name', project_id)} failed: {result['detail']}",
            payload={"url": url, **result},
        )
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
    from syte.notifications import publish_project_event
    await publish_project_event(
        "project.deployment_config_updated",
        project_id=project_id,
        message=f"Deployment configuration for {project.get('name', project_id)} was updated.",
        payload={"changed_fields": sorted(updates)},
    )
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


def _running(project: dict) -> bool:
    """Return the current project process state through the canonical manager."""
    from syte.process_manager import is_running
    return is_running(str(project.get("id") or ""), str(project.get("deploy_type") or "shell"))


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

