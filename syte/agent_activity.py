"""Real-time agent activity feed for Cursor-like chat UIs (sycord.com)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from syte.config import settings

EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'system',
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'agent',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_project_id ON agent_events(project_id, id);
"""

# Cursor-like event kinds exposed to clients.
ACTIVITY_EVENT_TYPES = frozenset({
    "user_message",
    "assistant_message",
    "thinking",
    "tool_call",
    "command_run",
    "file_created",
    "file_modified",
    "file_deleted",
    "file_read",
    "file_search",
    "request_started",
    "request_completed",
    "request_failed",
    "token_delta",
    "message_snapshot",
    "tool_call_started",
    "tool_call_finished",
    "tool_error",
    "file_changed",
    "command_output",
    "agent_started",
    "agent_stopped",
    "agent_restarted",
    "processing",
    "status",
    "service_action",
    "screenshot",
    "question",
    "question_answered",
    "session_stopped",
    "plan",
})

_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_table_ensured_paths: set[str] = set()


async def ensure_agent_events_table() -> None:
    db_path = str(settings.resolved_db_path)
    if db_path in _table_ensured_paths:
        return
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=db_path)
        await db.executescript(EVENTS_SCHEMA)
        await db.commit()
    _table_ensured_paths.add(db_path)


def _event_row_to_dict(row: tuple) -> dict[str, Any]:
    payload_raw = row[6] or "{}"
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        payload = {"raw": payload_raw}
    return {
        "id": row[0],
        "project_id": row[1],
        "event_type": row[2],
        "role": row[3],
        "title": row[4],
        "detail": row[5],
        "payload": payload,
        "source": row[7],
        "created_at": row[8],
    }


async def record_agent_event(
    project_id: str,
    event_type: str,
    *,
    role: str = "system",
    title: str = "",
    detail: str = "",
    payload: dict[str, Any] | None = None,
    source: str = "agent",
    turso_session_id: str | None = None,
) -> dict[str, Any]:
    """Persist an activity event locally and mirror it to the durable Turso session.

    Local persistence (the ``agent_events`` SQLite table below) remains the
    fast, always-available store used by internal status/debug endpoints. When
    ``turso_session_id`` is supplied (the caller's current durable agent
    session UUID — see :mod:`syte.turso_store`), the same event is additionally
    written to Turso so clients can fetch the whole session by UUID instead of
    streaming it live. Turso writes are best-effort: any failure (including
    Turso not being configured) never blocks or fails the local write.
    """
    await ensure_agent_events_table()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        cursor = await db.execute(
            "INSERT INTO agent_events "
            "(project_id, event_type, role, title, detail, payload, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                event_type,
                role,
                title[:500],
                detail[:4000],
                payload_json,
                source,
                now,
            ),
        )
        await db.commit()
        event_id = int(cursor.lastrowid)

    event = {
        "id": event_id,
        "project_id": project_id,
        "event_type": event_type,
        "role": role,
        "title": title,
        "detail": detail,
        "payload": payload or {},
        "source": source,
        "created_at": now,
    }

    for queue in list(_subscribers.get(project_id, [])):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass

    if turso_session_id:
        from syte.turso_store import record_event as record_turso_event

        try:
            await record_turso_event(
                turso_session_id,
                project_id,
                event_type,
                role=role,
                title=title,
                detail=detail,
                payload=payload,
                source=source,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to mirror agent event to Turso session %s", turso_session_id
            )
    return event


async def list_agent_events(
    project_id: str,
    *,
    since_id: int = 0,
    limit: int = 200,
    session: int | str | None = None,
) -> list[dict[str, Any]]:
    """List persisted activity events.

    ``session`` may be an integer session number, or ``"last"`` to return only
    events from the latest chat session (receivers that already rendered older
    ``[sessionN]`` blocks can skip reloading them).
    """
    await ensure_agent_events_table()
    limit = max(1, min(limit, 2000))
    session_filter: int | None = None
    if session is not None and str(session).strip() != "":
        raw = str(session).strip().lower()
        if raw == "last":
            async with aiosqlite.connect(settings.resolved_db_path) as db:
                from syte.sqlite_utils import configure_sqlite

                await configure_sqlite(db, db_path=str(settings.resolved_db_path))
                async with db.execute(
                    "SELECT payload FROM agent_events WHERE project_id = ? "
                    "ORDER BY id DESC LIMIT 200",
                    (project_id,),
                ) as cur:
                    rows = await cur.fetchall()
            for (payload_raw,) in rows:
                try:
                    payload = json.loads(payload_raw or "{}")
                except json.JSONDecodeError:
                    continue
                value = payload.get("session")
                if value is not None:
                    try:
                        session_filter = int(value)
                        break
                    except (TypeError, ValueError):
                        continue
        else:
            try:
                session_filter = int(raw)
            except (TypeError, ValueError):
                session_filter = None

    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        async with db.execute(
            "SELECT id, project_id, event_type, role, title, detail, payload, source, created_at "
            "FROM agent_events WHERE project_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (project_id, since_id, limit if session_filter is None else min(limit * 5, 2000)),
        ) as cur:
            rows = await cur.fetchall()
    events = [_event_row_to_dict(row) for row in rows]
    if session_filter is not None:
        events = [
            event
            for event in events
            if int((event.get("payload") or {}).get("session") or 0) == session_filter
        ][:limit]
    return events


def subscribe_agent_activity(project_id: str) -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
    _subscribers[project_id].append(queue)
    return queue


def unsubscribe_agent_activity(project_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    subs = _subscribers.get(project_id, [])
    if queue in subs:
        subs.remove(queue)
    if not subs and project_id in _subscribers:
        del _subscribers[project_id]


async def activity_sse_generator(
    project_id: str,
    *,
    since_id: int = 0,
    session: str | None = None,
    heartbeat_seconds: float = 15.0,
):
    """Yield SSE frames for live agent activity (token deltas, tools, etc.).

    Clients may still poll Turso session documents; this stream is an optional
    low-latency channel for Cursor-style token streaming in the GUI / sycord.com.
    """
    import json as _json

    # Replay recent backlog first so reconnects don't miss early tokens.
    backlog = await list_agent_events(
        project_id, since_id=since_id, limit=500, session=session or None,
    )
    last_id = since_id
    for event in backlog:
        last_id = max(last_id, int(event.get("id") or 0))
        event_name = str(event.get("event_type") or "message")
        yield (
            f"id: {event['id']}\n"
            f"event: {event_name}\n"
            f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        )

    queue = subscribe_agent_activity(project_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if int(event.get("id") or 0) <= last_id:
                continue
            if session:
                raw = str(session).strip().lower()
                payload_session = (event.get("payload") or {}).get("session")
                if raw == "last":
                    # Accept all live events for the current turn.
                    pass
                else:
                    try:
                        if int(payload_session or 0) != int(raw):
                            continue
                    except (TypeError, ValueError):
                        continue
            last_id = int(event.get("id") or 0)
            event_name = str(event.get("event_type") or "message")
            yield (
                f"id: {event['id']}\n"
                f"event: {event_name}\n"
                f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
            )
    finally:
        unsubscribe_agent_activity(project_id, queue)


async def record_workspace_activity(
    project_id: str,
    action: str,
    *,
    path: str = "",
    command: str = "",
    source: str = "api",
    detail: str = "",
) -> dict[str, Any]:
    """Record Syte workspace API actions (write/delete/command) for sycord.com."""
    mapping = {
        "write_file": ("file_modified", "Modified file"),
        "create_file": ("file_created", "Created file"),
        "delete_file": ("file_deleted", "Deleted file"),
        "read_file": ("file_read", "Read file"),
        "execute_command": ("command_run", "Ran command"),
        "upload_file": ("file_created", "Uploaded file"),
    }
    event_type, title = mapping.get(action, ("tool_call", action.replace("_", " ").title()))
    body = detail or path or command
    return await record_agent_event(
        project_id,
        event_type,
        role="assistant",
        title=title,
        detail=body[:4000],
        payload={"action": action, "path": path, "command": command},
        source=source,
    )
