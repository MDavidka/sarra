"""Local SQLite fallback for agent activity sessions.

sycord-pages (and other clients) require ``turso_session_id`` from
``agent_change`` so they can poll ``GET /agent_session/{id}``. When remote
Turso is unset or unreachable, the agent used to accept the turn with
``turso_session_id: null``, which surfaces as:

  "Syte agent accepted the request but did not return turso_session_id…"

This module keeps a pollable session document in the deployer's local
``syte.db`` so a session id is always available. Remote Turso remains the
preferred durable backend when configured; local storage is the always-on
fallback (and a mirror while Turso is healthy).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from syte.config import settings

logger = logging.getLogger(__name__)

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_agent_session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_number INTEGER NOT NULL DEFAULT 0,
    model_profile TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS local_agent_session_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'system',
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'agent',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_agent_session_event_session
ON local_agent_session_event(session_id, id);
CREATE INDEX IF NOT EXISTS idx_local_agent_session_project
ON local_agent_session(project_id, created_at);
"""

_ensured_paths: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_local_session_tables() -> None:
    path = str(settings.resolved_db_path)
    if path in _ensured_paths:
        return
    settings.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=path)
        await db.executescript(LOCAL_SCHEMA)
        async with db.execute("PRAGMA table_info(local_agent_session)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "ended_at" not in cols:
            await db.execute("ALTER TABLE local_agent_session ADD COLUMN ended_at TEXT")
        await db.commit()
    _ensured_paths.add(path)


def reset_local_session_cache() -> None:
    """Drop the ensure-cache (tests / db path switches)."""
    _ensured_paths.clear()


async def open_local_session(
    session_id: str,
    project_id: str,
    *,
    session_number: int = 0,
    model_profile: str | None = None,
) -> str:
    await ensure_local_session_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO local_agent_session "
            "(id, project_id, session_number, model_profile, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (
                session_id,
                project_id,
                int(session_number or 0),
                model_profile,
                now,
                now,
            ),
        )
        await db.commit()
    return session_id


async def close_local_session(session_id: str | None, *, status: str = "completed") -> None:
    if not session_id:
        return
    await ensure_local_session_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE local_agent_session SET status = ?, updated_at = ?, ended_at = ? WHERE id = ?",
            (status, now, now, session_id),
        )
        await db.commit()


async def record_local_event(
    session_id: str | None,
    project_id: str,
    event_type: str,
    *,
    role: str = "system",
    title: str = "",
    detail: str = "",
    payload: dict[str, Any] | None = None,
    source: str = "agent",
) -> dict[str, Any] | None:
    if not session_id:
        return None
    await ensure_local_session_tables()
    now = _now()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute(
            "INSERT INTO local_agent_session_event "
            "(session_id, project_id, event_type, role, title, detail, payload, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                project_id,
                event_type,
                role,
                (title or "")[:500],
                (detail or "")[:4000],
                payload_json,
                source,
                now,
            ),
        )
        await db.execute(
            "UPDATE local_agent_session SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        await db.commit()
        event_id = int(cursor.lastrowid)
    return {
        "id": event_id,
        "session_id": session_id,
        "project_id": project_id,
        "event_type": event_type,
        "role": role,
        "title": title,
        "detail": detail,
        "payload": payload or {},
        "source": source,
        "created_at": now,
    }


async def list_local_events(
    session_id: str, *, since_id: int = 0, limit: int = 2000
) -> list[dict[str, Any]]:
    await ensure_local_session_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, session_id, project_id, event_type, role, title, detail, "
            "payload, source, created_at FROM local_agent_session_event "
            "WHERE session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (session_id, since_id, max(1, min(limit, 5000))),
        ) as cur:
            rows = await cur.fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        payload_raw = row["payload"] or "{}"
        try:
            payload = json.loads(payload_raw)
        except (json.JSONDecodeError, TypeError):
            payload = {}
        events.append({
            "id": row["id"],
            "session_id": row["session_id"],
            "project_id": row["project_id"],
            "event_type": row["event_type"],
            "role": row["role"],
            "title": row["title"],
            "detail": row["detail"],
            "payload": payload,
            "source": row["source"],
            "created_at": row["created_at"],
        })
    return events


async def get_local_session(session_id: str, *, since_id: int = 0) -> dict[str, Any] | None:
    await ensure_local_session_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, project_id, session_number, model_profile, status, "
            "created_at, updated_at, ended_at FROM local_agent_session WHERE id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "session_number": row["session_number"],
        "model_profile": row["model_profile"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "ended_at": row["ended_at"],
        "storage": "local",
        "events": await list_local_events(session_id, since_id=since_id),
    }


async def list_local_sessions_for_project(
    project_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    await ensure_local_session_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, session_number, model_profile, status, created_at, updated_at, "
            "ended_at FROM local_agent_session WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (project_id, max(1, min(limit, 500))),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": row["id"],
            "session_number": row["session_number"],
            "model_profile": row["model_profile"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "ended_at": row["ended_at"],
            "storage": "local",
        }
        for row in rows
    ]
