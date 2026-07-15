"""Turso (libSQL) durable store for agent activity sessions.

Every agent turn now has a durable "agent session" identified by a UUID and
persisted in a Turso database (configured from the Syte GUI's AI tab via
``turso_database_url`` / ``turso_auth_token``). All activity produced while the
cloud agent works — the request, its plan, tool calls, and the final reply —
is written to this session as it happens.

This replaces the previous Server-Sent Events (SSE) activity stream. Clients
no longer open a long-lived streaming connection; instead they fetch the
durable session document by its UUID from the Turso access routes
(``GET /api/agent_session/{session_id}`` and its ``/api/internal`` and
``/sycord/api`` mirrors). Asking the agent something is unchanged — it still
happens over the regular request/response API (``agent_communicate`` /
``agent_change`` / the GUI chat endpoint) — only the *activity access* pattern
moved from a stream to a stored, poll-by-uuid session.

If Turso is not configured, every function here is a no-op (returns ``None``
or an empty result) so the rest of the agent pipeline keeps working
unaffected — activity simply is not mirrored anywhere durable beyond the
existing local SQLite ``agent_events`` table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from syte.database import get_setting

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS agent_session (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        session_number INTEGER NOT NULL DEFAULT 0,
        model_profile TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_session_event (
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_session_event_session "
    "ON agent_session_event(session_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_session_project "
    "ON agent_session(project_id, created_at)",
)

# One cached client + schema-ready flag per (url, token) pair so settings
# changes (saved from the AI tab) transparently pick up a fresh connection.
_client_cache: dict[tuple[str, str], Any] = {}
_schema_ready: set[tuple[str, str]] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def turso_settings() -> tuple[str, str]:
    """Return the configured ``(database_url, auth_token)`` pair, or ("", "")."""
    url = (await get_setting("turso_database_url", "")).strip()
    token = (await get_setting("turso_auth_token", "")).strip()
    return url, token


async def turso_configured() -> bool:
    url, _ = await turso_settings()
    return bool(url)


def reset_client_cache() -> None:
    """Drop cached clients — call after Turso settings are saved.

    Closing is best-effort: ``Client.close()`` is a coroutine, but this helper
    is called from sync contexts (e.g. right after a settings save) where
    scheduling it reliably isn't worth the complexity — the underlying
    connection is lightweight and simply dropping the reference is safe.
    """
    _client_cache.clear()
    _schema_ready.clear()


def _build_client(url: str, token: str):
    import libsql_client

    kwargs: dict[str, Any] = {}
    if token:
        kwargs["auth_token"] = token
    return libsql_client.create_client(url, **kwargs)


async def get_turso_client() -> Any | None:
    """Return a ready-to-use Turso client, or ``None`` if not configured."""
    url, token = await turso_settings()
    if not url:
        return None
    key = (url, token)
    client = _client_cache.get(key)
    if client is None:
        try:
            client = _build_client(url, token)
        except Exception:
            logger.exception("Failed to create Turso client")
            return None
        _client_cache[key] = client
    if key not in _schema_ready:
        try:
            for stmt in SCHEMA_STATEMENTS:
                await client.execute(stmt)
            _schema_ready.add(key)
        except Exception:
            logger.exception("Failed to initialize Turso agent_session schema")
            _client_cache.pop(key, None)
            return None
    return client


def _row_value(row: Any, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


async def open_session(
    project_id: str,
    *,
    session_number: int = 0,
    model_profile: str | None = None,
) -> str | None:
    """Create a new durable agent session in Turso and return its UUID."""
    client = await get_turso_client()
    if client is None:
        return None
    session_id = uuid.uuid4().hex
    now = _now()
    try:
        await client.execute(
            "INSERT INTO agent_session "
            "(id, project_id, session_number, model_profile, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?)",
            [session_id, project_id, int(session_number or 0), model_profile, now, now],
        )
    except Exception:
        logger.exception("Failed to open Turso agent session for %s", project_id)
        return None
    return session_id


async def close_session(session_id: str | None, *, status: str = "completed") -> None:
    if not session_id:
        return
    client = await get_turso_client()
    if client is None:
        return
    try:
        await client.execute(
            "UPDATE agent_session SET status = ?, updated_at = ? WHERE id = ?",
            [status, _now(), session_id],
        )
    except Exception:
        logger.exception("Failed to close Turso agent session %s", session_id)


async def record_event(
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
    """Append one activity event to a durable Turso session."""
    if not session_id:
        return None
    client = await get_turso_client()
    if client is None:
        return None
    now = _now()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    try:
        result = await client.execute(
            "INSERT INTO agent_session_event "
            "(session_id, project_id, event_type, role, title, detail, payload, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                session_id,
                project_id,
                event_type,
                role,
                (title or "")[:500],
                (detail or "")[:4000],
                payload_json,
                source,
                now,
            ],
        )
        await client.execute(
            "UPDATE agent_session SET updated_at = ? WHERE id = ?", [now, session_id]
        )
    except Exception:
        logger.exception("Failed to record Turso agent session event for %s", session_id)
        return None
    return {
        "id": result.last_insert_rowid,
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


async def list_events(
    session_id: str, *, since_id: int = 0, limit: int = 2000
) -> list[dict[str, Any]]:
    client = await get_turso_client()
    if client is None:
        return []
    try:
        rs = await client.execute(
            "SELECT id, session_id, project_id, event_type, role, title, detail, "
            "payload, source, created_at FROM agent_session_event "
            "WHERE session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            [session_id, since_id, max(1, min(limit, 5000))],
        )
    except Exception:
        logger.exception("Failed to list Turso agent session events for %s", session_id)
        return []
    events: list[dict[str, Any]] = []
    for row in rs.rows:
        payload_raw = _row_value(row, "payload") or "{}"
        try:
            payload = json.loads(payload_raw)
        except (json.JSONDecodeError, TypeError):
            payload = {}
        events.append({
            "id": _row_value(row, "id"),
            "session_id": _row_value(row, "session_id"),
            "project_id": _row_value(row, "project_id"),
            "event_type": _row_value(row, "event_type"),
            "role": _row_value(row, "role"),
            "title": _row_value(row, "title"),
            "detail": _row_value(row, "detail"),
            "payload": payload,
            "source": _row_value(row, "source"),
            "created_at": _row_value(row, "created_at"),
        })
    return events


async def get_session(session_id: str, *, since_id: int = 0) -> dict[str, Any] | None:
    """Fetch one durable session (metadata + events) by UUID."""
    client = await get_turso_client()
    if client is None:
        return None
    try:
        rs = await client.execute(
            "SELECT id, project_id, session_number, model_profile, status, "
            "created_at, updated_at FROM agent_session WHERE id = ?",
            [session_id],
        )
    except Exception:
        logger.exception("Failed to fetch Turso agent session %s", session_id)
        return None
    if not rs.rows:
        return None
    row = rs.rows[0]
    session = {
        "id": _row_value(row, "id"),
        "project_id": _row_value(row, "project_id"),
        "session_number": _row_value(row, "session_number"),
        "model_profile": _row_value(row, "model_profile"),
        "status": _row_value(row, "status"),
        "created_at": _row_value(row, "created_at"),
        "updated_at": _row_value(row, "updated_at"),
    }
    session["events"] = await list_events(session_id, since_id=since_id)
    return session


async def list_sessions_for_project(
    project_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    client = await get_turso_client()
    if client is None:
        return []
    try:
        rs = await client.execute(
            "SELECT id, session_number, model_profile, status, created_at, updated_at "
            "FROM agent_session WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            [project_id, max(1, min(limit, 500))],
        )
    except Exception:
        logger.exception("Failed to list Turso agent sessions for %s", project_id)
        return []
    return [
        {
            "id": _row_value(row, "id"),
            "session_number": _row_value(row, "session_number"),
            "model_profile": _row_value(row, "model_profile"),
            "status": _row_value(row, "status"),
            "created_at": _row_value(row, "created_at"),
            "updated_at": _row_value(row, "updated_at"),
        }
        for row in rs.rows
    ]


async def latest_session_id_for_project(project_id: str) -> str | None:
    sessions = await list_sessions_for_project(project_id, limit=1)
    return sessions[0]["id"] if sessions else None
