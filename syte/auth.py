"""API token authentication."""

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from syte.database import (
    create_api_token,
    delete_api_token,
    get_api_token_by_hash,
    get_setting,
    list_api_tokens,
    touch_api_token,
)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
BEARER_PREFIX = "Bearer "


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


async def _verify_bridge_secret(request: Request) -> bool:
    """True when X-Sycord-Bridge-Secret matches configured shared secret."""
    secret = (await get_setting("sycord_bridge_secret", "")).strip()
    if not secret:
        return False
    header = request.headers.get("x-sycord-bridge-secret", "").strip()
    if header and hmac.compare_digest(header, secret):
        return True
    auth = request.headers.get("authorization", "")
    if auth.startswith(BEARER_PREFIX):
        token = auth[len(BEARER_PREFIX) :].strip()
        if token and hmac.compare_digest(token, secret):
            return True
    return False


async def verify_bridge_or_api_token(
    request: Request,
    x_api_key: str | None = Security(API_KEY_HEADER),
) -> dict[str, Any]:
    """Accept Syte API token OR matching Sycord bridge secret (for sycord.com → Syte)."""
    if await _verify_bridge_secret(request):
        return {"id": "bridge", "name": "sycord-bridge", "prefix": "bridge"}
    return await verify_api_token(request, x_api_key)


async def verify_bridge_or_api_token_from_request(request: Request) -> dict[str, Any]:
    if await _verify_bridge_secret(request):
        return {"id": "bridge", "name": "sycord-bridge", "prefix": "bridge"}
    return await verify_api_token_from_request(request)


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
