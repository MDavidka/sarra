"""Regression coverage for governed API-token storage and request enforcement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


def api_request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"syte.test")],
            "server": ("syte.test", 443),
        }
    )


@pytest.mark.asyncio
async def test_api_token_persists_safe_expiry_scope_and_rate_policy(tmp_data_dir: Path) -> None:
    from syte import auth, database

    auth._token_request_windows.clear()
    await database.init_db()
    created = await auth.create_token(
        "release-bot",
        expires_at="2030-01-01T00:00:00+00:00",
        scopes=["read", "deploy"],
        rate_limit_per_minute=25,
    )

    listed = await auth.list_tokens()

    assert created["token"].startswith("syte_")
    assert listed == [{
        "id": created["id"],
        "name": "release-bot",
        "prefix": created["prefix"],
        "created_at": created["created_at"],
        "last_used_at": None,
        "expires_at": "2030-01-01T00:00:00+00:00",
        "scopes": ["read", "deploy"],
        "rate_limit_per_minute": 25,
    }]
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]


@pytest.mark.asyncio
async def test_api_token_scope_expiry_and_rate_are_enforced(tmp_data_dir: Path) -> None:
    from syte import auth, database

    auth._token_request_windows.clear()
    await database.init_db()
    limited = await auth.create_token("read-only", scopes=["read"], rate_limit_per_minute=1)

    accepted = await auth.verify_api_token(api_request("GET", "/api/projects"), limited["token"])
    assert accepted["id"] == limited["id"]

    with pytest.raises(HTTPException) as rate_error:
        await auth.verify_api_token(api_request("GET", "/api/projects"), limited["token"])
    assert rate_error.value.status_code == 429
    assert rate_error.value.detail["error"] == "rate_limited"

    with pytest.raises(HTTPException) as scope_error:
        await auth.verify_api_token(api_request("POST", "/api/projects"), limited["token"])
    assert scope_error.value.status_code == 403
    assert scope_error.value.detail["error"] == "insufficient_api_key_scope"

    expired = await auth.create_token(
        "expired",
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        scopes=["read"],
    )
    with pytest.raises(HTTPException) as expired_error:
        await auth.verify_api_token(api_request("GET", "/api/projects"), expired["token"])
    assert expired_error.value.status_code == 401
    assert expired_error.value.detail["error"] == "expired_api_key"


@pytest.mark.asyncio
async def test_database_migrates_legacy_api_token_policy_columns(tmp_data_dir: Path) -> None:
    from syte import database

    tmp_data_dir.mkdir(parents=True)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                git_url TEXT,
                branch TEXT DEFAULT 'main',
                port INTEGER NOT NULL,
                domain TEXT,
                start_command TEXT NOT NULL DEFAULT '',
                env_vars TEXT DEFAULT '{}',
                status TEXT DEFAULT 'stopped',
                in_app_notifications INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE api_tokens (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                prefix TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_used_at TEXT
            );
            """
        )
        await db.commit()

    await database.init_db()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute("PRAGMA table_info(api_tokens)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        async with db.execute("PRAGMA table_info(projects)") as cur:
            project_columns = {row[1] for row in await cur.fetchall()}

    assert {"expires_at", "scopes", "rate_limit_per_minute"} <= columns
    assert {"github_account_id", "last_seen_git_commit", "last_deployed_commit"} <= project_columns
