"""GitHub OAuth helpers for the connected Project source workflow.

Tokens are encrypted before storage, used only server-side, and never included in
project records or API responses. This module uses GitHub's OAuth App endpoints
for interactive authorization and the REST API for repository discovery.
"""
from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from syte.config import settings
from syte.database import (
    consume_github_oauth_state,
    create_github_oauth_state,
    get_github_connection,
    get_setting,
    save_github_connection,
)

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"
OAUTH_SCOPE = "read:user repo"
STATE_TTL_SECONDS = 10 * 60


class GitHubOAuthError(ValueError):
    """A safe, user-facing provider authorization error."""


def _setting_value(env_value: str, setting_value: str) -> str:
    return env_value.strip() or setting_value.strip()


async def github_oauth_configured() -> bool:
    client_id = _setting_value(settings.github_oauth_client_id, await get_setting("github_oauth_client_id", ""))
    client_secret = _setting_value(settings.github_oauth_client_secret, await get_setting("github_oauth_client_secret", ""))
    key = _setting_value(settings.oauth_encryption_key, await get_setting("oauth_encryption_key", ""))
    return bool(client_id and client_secret and key)


async def _credentials() -> tuple[str, str, Fernet]:
    client_id = _setting_value(settings.github_oauth_client_id, await get_setting("github_oauth_client_id", ""))
    client_secret = _setting_value(settings.github_oauth_client_secret, await get_setting("github_oauth_client_secret", ""))
    raw_key = _setting_value(settings.oauth_encryption_key, await get_setting("oauth_encryption_key", ""))
    if not client_id or not client_secret:
        raise GitHubOAuthError("GitHub OAuth is not configured. Add the client ID and client secret in Git provider settings.")
    if not raw_key:
        raise GitHubOAuthError("GitHub OAuth token encryption is not configured. Set a Fernet encryption key first.")
    try:
        cipher = Fernet(raw_key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise GitHubOAuthError("The GitHub OAuth encryption key is invalid.") from exc
    return client_id, client_secret, cipher


async def start_github_authorization(account_id: str, redirect_uri: str) -> str:
    client_id, _, _ = await _credentials()
    state = secrets.token_urlsafe(32)
    await create_github_oauth_state(state, account_id, redirect_uri, int(time.time()) + STATE_TTL_SECONDS)
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'state': state, 'scope': OAUTH_SCOPE, 'allow_signup': 'false'})}"


async def complete_github_authorization(code: str, state: str) -> dict[str, str]:
    record = await consume_github_oauth_state(state)
    if not record:
        raise GitHubOAuthError("This GitHub connection request is invalid or expired. Start it again from Projects.")
    client_id, client_secret, cipher = await _credentials()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(
                GITHUB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": str(record["redirect_uri"]),
                },
            )
    except httpx.HTTPError as exc:
        raise GitHubOAuthError("Could not reach GitHub to complete authorization.") from exc
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    token = str(payload.get("access_token", ""))
    if response.status_code >= 400 or not token:
        raise GitHubOAuthError("GitHub did not accept this authorization code. Start the connection again.")
    profile = await _github_api(token, "/user")
    login = str(profile.get("login", ""))
    if not login:
        raise GitHubOAuthError("GitHub authorization succeeded but did not return an account profile.")
    await save_github_connection(
        str(record["account_id"]),
        login=login,
        avatar_url=str(profile.get("avatar_url", "")),
        token_ciphertext=cipher.encrypt(token.encode("utf-8")).decode("utf-8"),
        scopes=str(payload.get("scope", "")),
    )
    return {"account_id": str(record["account_id"]), "login": login}


async def connection_summary(account_id: str) -> dict[str, Any]:
    configured = await github_oauth_configured()
    connection = await get_github_connection(account_id)
    return {
        "provider": "github",
        "configured": configured,
        "connected": bool(connection),
        "login": str(connection.get("login", "")) if connection else "",
        "avatar_url": str(connection.get("avatar_url", "")) if connection else "",
        "scopes": str(connection.get("scopes", "")) if connection else "",
        "connected_at": str(connection.get("connected_at", "")) if connection else "",
    }


async def token_for_account(account_id: str) -> str:
    _, _, cipher = await _credentials()
    connection = await get_github_connection(account_id, include_token=True)
    if not connection:
        raise GitHubOAuthError("Connect a GitHub account before browsing private repositories.")
    try:
        return cipher.decrypt(str(connection["token_ciphertext"]).encode("utf-8")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, KeyError) as exc:
        raise GitHubOAuthError("The saved GitHub connection cannot be read. Reconnect your GitHub account.") from exc


async def _github_api(token: str, path: str, *, params: dict[str, Any] | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(
                f"{GITHUB_API_URL}{path}",
                params=params,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as exc:
        raise GitHubOAuthError("Could not reach GitHub. Check your connection and try again.") from exc
    if response.status_code == 401:
        raise GitHubOAuthError("Your GitHub connection has expired or was revoked. Reconnect it to continue.")
    if response.status_code == 403:
        raise GitHubOAuthError("GitHub denied this action. Check the OAuth App permissions and organization policy.")
    if response.status_code == 404:
        raise GitHubOAuthError("GitHub could not find that repository or branch for the connected account.")
    if response.status_code >= 400:
        raise GitHubOAuthError("GitHub could not complete this request.")
    return response.json()


async def list_repositories(account_id: str, query: str = "") -> list[dict[str, Any]]:
    token = await token_for_account(account_id)
    records: list[dict[str, Any]] = []
    # GitHub returns at most 100 repositories per request. Continue until the
    # final short page so a connected account can browse its full access set.
    for page in range(1, 101):
        batch = await _github_api(token, "/user/repos", params={
            "visibility": "all", "affiliation": "owner,collaborator,organization_member",
            "sort": "updated", "per_page": 100, "page": page,
        })
        if not isinstance(batch, list):
            break
        records.extend(batch)
        if len(batch) < 100:
            break
    needle = query.strip().lower()
    result: list[dict[str, Any]] = []
    for item in records:
        full_name = str(item.get("full_name", ""))
        if needle and needle not in full_name.lower() and needle not in str(item.get("description", "")).lower():
            continue
        result.append({
            "id": int(item.get("id", 0)),
            "full_name": full_name,
            "name": str(item.get("name", "")),
            "clone_url": str(item.get("clone_url", "")),
            "default_branch": str(item.get("default_branch", "main")),
            "private": bool(item.get("private", False)),
            "description": str(item.get("description") or ""),
            "language": str(item.get("language") or ""),
            "topics": [str(topic) for topic in (item.get("topics") or []) if str(topic)],
            "updated_at": str(item.get("updated_at", "")),
            "owner": str((item.get("owner") or {}).get("login", "")),
        })
    return result


async def branch_head(account_id: str, full_name: str, branch: str) -> str:
    """Return the current SHA for one tracked GitHub branch."""
    owner, separator, repo = full_name.strip().partition("/")
    if not separator or not owner or not repo or "/" in repo or not branch.strip():
        raise GitHubOAuthError("Choose a valid GitHub repository and branch.")
    token = await token_for_account(account_id)
    item = await _github_api(token, f"/repos/{owner}/{repo}/commits/{branch.strip()}")
    sha = str((item or {}).get("sha") or "")
    if not sha:
        raise GitHubOAuthError("GitHub did not return a commit for the configured branch.")
    return sha


async def list_branches(account_id: str, full_name: str) -> list[dict[str, str]]:
    owner, separator, repo = full_name.strip().partition("/")
    if not separator or not owner or not repo or "/" in repo:
        raise GitHubOAuthError("Choose a valid GitHub repository.")
    token = await token_for_account(account_id)
    records: list[dict[str, Any]] = []
    for page in range(1, 101):
        batch = await _github_api(token, f"/repos/{owner}/{repo}/branches", params={"per_page": 100, "page": page})
        if not isinstance(batch, list):
            break
        records.extend(batch)
        if len(batch) < 100:
            break
    return [{"name": str(item.get("name", "")), "sha": str((item.get("commit") or {}).get("sha", ""))} for item in records if item.get("name")]


__all__ = [
    "GitHubOAuthError", "complete_github_authorization", "connection_summary",
    "branch_head", "github_oauth_configured", "list_branches", "list_repositories",
    "start_github_authorization", "token_for_account",
]
