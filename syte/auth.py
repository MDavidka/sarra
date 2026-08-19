"""API token authentication.

Syte API tokens are **host-global admin credentials** for a single Syte hoster
instance (multi-project, single-tenant). Any valid token may operate on any
project UUID on that host. Tenant isolation is expected at the host boundary
(one Syte install per operator), not via per-token project ACLs.
"""

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from syte.config import settings
from syte.database import (
    create_api_token,
    delete_api_token,
    get_api_token_by_hash,
    list_api_tokens,
    touch_api_token,
    get_setting,
    get_operator_account,
    get_operator_account_by_email,
    update_operator_account,
)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
BEARER_PREFIX = "Bearer "
OPERATOR_SESSION_COOKIE = "__Host-syte-operator"
OPERATOR_CSRF_HEADER = "X-Syte-CSRF"
OPERATOR_SESSION_TTL_SECONDS = 8 * 60 * 60
_operator_sessions: dict[str, tuple[float, str, dict[str, Any] | None]] = {}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Return (full_token, prefix, token_hash)."""
    full = "syte_" + secrets.token_urlsafe(32)
    prefix = full[:16]
    return full, prefix, hash_token(full)


async def create_token(name: str) -> dict[str, Any]:
    full, prefix, token_hash = generate_token()
    row = await create_api_token(name=name, prefix=prefix, token_hash=token_hash)
    row["token"] = full
    return row


async def revoke_token(token_id: str) -> bool:
    return await delete_api_token(token_id)


async def list_tokens() -> list[dict[str, Any]]:
    return await list_api_tokens()


def _extract_token(
    x_api_key: str | None,
    authorization: str | None = None,
    query_key: str | None = None,
) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if query_key:
        return query_key.strip()
    if authorization and authorization.startswith(BEARER_PREFIX):
        return authorization[len(BEARER_PREFIX) :].strip()
    return None


async def verify_api_token(
    request: Request,
    x_api_key: str | None = Security(API_KEY_HEADER),
) -> dict[str, Any]:
    """FastAPI dependency — require valid API token."""
    auth = request.headers.get("authorization")
    token = _extract_token(x_api_key, auth)
    if not token:
        raise HTTPException(
            401,
            detail={
                "error": "missing_api_key",
                "message": "Provide X-API-Key header or Authorization: Bearer <token>",
            },
        )
    token_hash = hash_token(token)
    row = await get_api_token_by_hash(token_hash)
    if not row:
        raise HTTPException(
            401,
            detail={"error": "invalid_api_key", "message": "API key is invalid or revoked"},
        )
    if not hmac.compare_digest(row["token_hash"], token_hash):
        raise HTTPException(401, detail={"error": "invalid_api_key", "message": "API key is invalid"})
    await touch_api_token(row["id"])
    return row


def require_same_origin_if_present(request: Request) -> None:
    """Reject cross-origin browser requests that could carry a GUI cookie.

    Secure host-only cookies already prevent sibling hosts from receiving the
    session. This check additionally prevents permissive global CORS settings
    from exposing the session's CSRF value to a different allowed origin.
    """
    origin = request.headers.get("origin", "").strip()
    if not origin:
        return
    origin_host = urlsplit(origin).netloc.lower()
    request_host = request.headers.get("host", "").lower()
    if not origin_host or not request_host or not hmac.compare_digest(origin_host, request_host):
        raise HTTPException(
            403,
            detail={"error": "cross_origin_operator_request", "message": "Operator sessions are same-origin only."},
        )


def _prune_operator_sessions(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    for session_id, (expires_at, _csrf_token, _account) in tuple(_operator_sessions.items()):
        if expires_at <= now:
            _operator_sessions.pop(session_id, None)


def create_bootstrap_operator_session(bootstrap_token: str) -> dict[str, str | int]:
    """Create a short-lived GUI operator session from the server credential.

    The credential remains server configuration: only a random HttpOnly
    session id goes into the cookie, while the CSRF value is returned to the
    same page for unsafe same-origin requests.
    """
    expected = settings.bootstrap_api_token.strip()
    if not expected:
        raise HTTPException(
            503,
            detail={
                "error": "bootstrap_session_unavailable",
                "message": "SYTE_BOOTSTRAP_API_TOKEN is not configured.",
            },
        )
    if not hmac.compare_digest((bootstrap_token or "").strip(), expected):
        raise HTTPException(401, detail={"error": "invalid_operator_key", "message": "Invalid operator key."})

    _prune_operator_sessions()
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    _operator_sessions[session_id] = (time.time() + OPERATOR_SESSION_TTL_SECONDS, csrf_token, None)
    return {
        "session_id": session_id,
        "csrf_token": csrf_token,
        "max_age": OPERATOR_SESSION_TTL_SECONDS,
    }


def operator_session_status(request: Request) -> dict[str, str | int | bool]:
    """Read the current operator session without accepting browser-supplied keys."""
    require_same_origin_if_present(request)
    _prune_operator_sessions()
    session_id = request.cookies.get(OPERATOR_SESSION_COOKIE, "")
    entry = _operator_sessions.get(session_id)
    if not entry:
        return {"authenticated": False}
    expires_at, csrf_token, account = entry
    if expires_at <= time.time():
        _operator_sessions.pop(session_id, None)
        return {"authenticated": False}
    result: dict[str, str | int | bool | dict[str, Any]] = {
        "authenticated": True,
        "csrf_token": csrf_token,
        "expires_in": max(0, int(expires_at - time.time())),
    }
    if account:
        result["account"] = account
    return result


def revoke_operator_session(request: Request) -> None:
    """Revoke this browser's operator session, if one is present."""
    session_id = request.cookies.get(OPERATOR_SESSION_COOKIE, "")
    if session_id:
        _operator_sessions.pop(session_id, None)


async def verify_operator_session_or_token(
    request: Request,
    x_api_key: str | None = Security(API_KEY_HEADER),
) -> dict[str, Any]:
    """Allow explicit operator tokens or a secure same-origin GUI session."""
    token = _extract_token(x_api_key, request.headers.get("authorization"))
    if token:
        return await verify_operator_token(request, x_api_key)

    session = operator_session_status(request)
    if not session.get("authenticated"):
        raise HTTPException(
            401,
            detail={"error": "operator_session_required", "message": "Operator authentication is required for this protected action."},
        )

    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        csrf_token = str(session["csrf_token"])
        supplied = request.headers.get(OPERATOR_CSRF_HEADER, "")
        if not supplied or not hmac.compare_digest(supplied, csrf_token):
            raise HTTPException(
                403,
                detail={"error": "invalid_csrf_token", "message": "Refresh the Syra web UI and retry."},
            )
    account = session.get("account") if isinstance(session, dict) else None
    if isinstance(account, dict):
        return {"id": str(account.get("id", "gui-session")), "name": str(account.get("email", "operator")), "auth": "account", "account": account}
    return {"id": "gui-session", "name": "gui-session", "auth": "session"}


async def verify_operator_token(
    request: Request,
    x_api_key: str | None = Security(API_KEY_HEADER),
) -> dict[str, Any]:
    """Require an existing API token or the server's bootstrap operator token.

    ``SYTE_BOOTSTRAP_API_TOKEN`` is intentionally environment-only. It lets an
    administrator create the first stored API token without exposing a public,
    unauthenticated token-minting route.
    """
    token = _extract_token(x_api_key, request.headers.get("authorization"))
    bootstrap_token = settings.bootstrap_api_token.strip()
    if bootstrap_token and token and hmac.compare_digest(token, bootstrap_token):
        return {"id": "bootstrap", "name": "bootstrap", "auth": "bootstrap"}
    return await verify_api_token(request, x_api_key)


async def verify_api_token_from_request(request: Request) -> dict[str, Any]:
    auth_header = request.headers.get("authorization")
    key = request.headers.get("x-api-key")
    query_key = request.query_params.get("api_key")
    token = _extract_token(key, auth_header, query_key)
    if not token:
        raise HTTPException(
            401,
            detail={"error": "missing_api_key", "message": "Provide X-API-Key or Authorization: Bearer"},
        )
    token_hash = hash_token(token)
    row = await get_api_token_by_hash(token_hash)
    if not row:
        raise HTTPException(401, detail={"error": "invalid_api_key", "message": "Invalid API key"})
    await touch_api_token(row["id"])
    return row


async def verify_internal_service_request(request: Request) -> dict[str, Any]:
    """Shared-secret auth for sycord.com -> Syte internal runtime calls."""
    expected = (await get_setting("syra_internal_secret", "")).strip()
    if not expected:
        raise HTTPException(
            503,
            detail={
                "error": "internal_secret_not_configured",
                "message": "Set syra_internal_secret in Syte settings before using internal agent routes.",
            },
        )

    token = _extract_token(
        request.headers.get("x-syra-internal-secret"),
        request.headers.get("authorization"),
        request.query_params.get("internal_secret"),
    )
    if not token:
        raise HTTPException(
            401,
            detail={
                "error": "missing_internal_secret",
                "message": "Provide X-Syra-Internal-Secret or Authorization: Bearer <secret>",
            },
        )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            401,
            detail={
                "error": "invalid_internal_secret",
                "message": "Internal secret is invalid.",
            },
        )
    return {"ok": True, "auth": "internal-secret"}


PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """Hash an operator password with salted scrypt using only the standard library."""
    if len(password) < 12:
        raise HTTPException(422, detail={"error": "weak_password", "message": "Password must contain at least 12 characters."})
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=PASSWORD_SCRYPT_N, r=PASSWORD_SCRYPT_R, p=PASSWORD_SCRYPT_P)
    return "$".join(("scrypt", str(PASSWORD_SCRYPT_N), str(PASSWORD_SCRYPT_R), str(PASSWORD_SCRYPT_P), base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode()))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.urlsafe_b64decode(salt.encode()), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest, base64.urlsafe_b64decode(expected.encode()))
    except (ValueError, TypeError):
        return False


def _public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account["id"], "email": account["email"], "display_name": account.get("display_name") or account["email"].split("@", 1)[0],
        "avatar_icon": account.get("avatar_icon") or "user", "role": account.get("role") or "operator",
    }


def create_account_operator_session(account: dict[str, Any]) -> dict[str, str | int | dict[str, Any]]:
    _prune_operator_sessions()
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    _operator_sessions[session_id] = (time.time() + OPERATOR_SESSION_TTL_SECONDS, csrf_token, _public_account(account))
    return {"session_id": session_id, "csrf_token": csrf_token, "max_age": OPERATOR_SESSION_TTL_SECONDS, "account": _public_account(account)}


async def authenticate_operator_account(email: str, password: str) -> dict[str, Any] | None:
    account = await get_operator_account_by_email(email)
    if not account or not verify_password(password, str(account.get("password_hash") or "")):
        return None
    await update_operator_account(str(account["id"]), {"last_login_at": datetime.now(timezone.utc).isoformat()})
    return await get_operator_account(str(account["id"])) or account
