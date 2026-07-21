"""Durable session and request state for the VM-native cloud agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from syte.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    project_id TEXT PRIMARY KEY,
    model_profile TEXT NOT NULL,
    session_counter INTEGER NOT NULL DEFAULT 0,
    turso_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    session_number INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_call_id TEXT,
    tool_calls TEXT,
    reasoning_content TEXT,
    turso_synced INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_project
ON agent_messages(project_id, id);
CREATE TABLE IF NOT EXISTS cloud_agent_requests (
    request_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    message TEXT NOT NULL,
    model_profile TEXT,
    source TEXT NOT NULL,
    auto_start INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cloud_agent_requests_pending
ON cloud_agent_requests(status, created_at);
"""

# Bump when additive column migrations change so long-lived processes re-run
# ensure after an upgrade (the path cache alone would skip ALTER TABLE).
_SCHEMA_EPOCH = 4
_ensured_paths: dict[str, int] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_cloud_agent_tables() -> None:
    """Create cloud-agent tables and apply additive column migrations.

    Existing databases predate ``session_number`` / ``session_counter``. The
    session index is created *after* those columns are added — creating it in
    the initial ``CREATE TABLE`` script would fail with
    ``no such column: session_number`` on upgrade.
    """
    path = str(settings.resolved_db_path)
    if _ensured_paths.get(path) == _SCHEMA_EPOCH:
        return
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=path)
        # Base tables/indexes that do not depend on migrated columns.
        await db.executescript(SCHEMA)

        async with db.execute("PRAGMA table_info(agent_messages)") as cur:
            message_cols = {row[1] for row in await cur.fetchall()}
        if "reasoning_content" not in message_cols:
            await db.execute("ALTER TABLE agent_messages ADD COLUMN reasoning_content TEXT")
        if "session_number" not in message_cols:
            await db.execute(
                "ALTER TABLE agent_messages ADD COLUMN session_number INTEGER NOT NULL DEFAULT 0"
            )
        if "turso_synced" not in message_cols:
            await db.execute(
                "ALTER TABLE agent_messages ADD COLUMN turso_synced INTEGER NOT NULL DEFAULT 0"
            )

        async with db.execute("PRAGMA table_info(agent_sessions)") as cur:
            session_cols = {row[1] for row in await cur.fetchall()}
        if "session_counter" not in session_cols:
            await db.execute(
                "ALTER TABLE agent_sessions ADD COLUMN session_counter INTEGER NOT NULL DEFAULT 0"
            )
        if "turso_session_id" not in session_cols:
            await db.execute("ALTER TABLE agent_sessions ADD COLUMN turso_session_id TEXT")

        # Indexes that require migrated columns — must run after ALTER TABLE.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_messages_session "
            "ON agent_messages(project_id, session_number, id)"
        )

        # A VM restart may interrupt an active turn. Re-admit it to the queue.
        await db.execute(
            "UPDATE cloud_agent_requests SET status = 'pending', started_at = NULL "
            "WHERE status = 'running'"
        )
        await db.commit()
    _ensured_paths[path] = _SCHEMA_EPOCH


async def ensure_session(project_id: str, model_profile: str) -> None:
    await ensure_cloud_agent_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_sessions"
            "(project_id, model_profile, session_counter, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?) ON CONFLICT(project_id) DO UPDATE SET "
            "model_profile = excluded.model_profile, updated_at = excluded.updated_at",
            (project_id, model_profile, now, now),
        )
        await db.commit()


async def begin_turn_session(project_id: str, model_profile: str | None = None) -> int:
    """Open a new chat session for one user message + the agent turn that follows.

    Each user-submitted request increments ``session_counter``. Receivers see
    ``[sessionN]`` on the marked activity stream; the agent loads provider
    history only from this latest session.
    """
    await ensure_cloud_agent_tables()
    now = _now()
    profile = (model_profile or "syra-base").strip() or "syra-base"
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_sessions"
            "(project_id, model_profile, session_counter, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?) ON CONFLICT(project_id) DO UPDATE SET "
            "session_counter = agent_sessions.session_counter + 1, "
            "model_profile = COALESCE(?, agent_sessions.model_profile), "
            "updated_at = excluded.updated_at",
            (project_id, profile, now, now, model_profile),
        )
        async with db.execute(
            "SELECT session_counter FROM agent_sessions WHERE project_id = ?",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return int(row[0]) if row else 1


async def set_turso_session_id(project_id: str, turso_session_id: str | None) -> None:
    """Record the durable Turso session UUID backing the project's current turn."""
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE agent_sessions SET turso_session_id = ?, updated_at = ? WHERE project_id = ?",
            (turso_session_id, _now(), project_id),
        )
        await db.commit()


async def current_turso_session_id(project_id: str) -> str | None:
    """Return the Turso session UUID for the project's most recent turn, if any."""
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT turso_session_id FROM agent_sessions WHERE project_id = ?",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
    return str(row[0]) if row and row[0] else None


async def current_session_number(project_id: str) -> int:
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT session_counter FROM agent_sessions WHERE project_id = ?",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def append_message(
    project_id: str,
    request_id: str,
    role: str,
    content: str,
    *,
    session_number: int = 0,
    tool_call_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
) -> int:
    """Persist one chat message locally and return its local ``id``.

    The returned id is the join key used to mirror this exact message into
    the durable Turso ``agent_message`` table (see
    :func:`syte.turso_store.record_message` and :func:`mark_message_synced`)
    and to compute the aggregate "all messages saved" status shown by the
    GUI's brain indicator.
    """
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute(
            "INSERT INTO agent_messages "
            "(project_id, request_id, session_number, role, content, tool_call_id, "
            "tool_calls, reasoning_content, turso_synced, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                project_id,
                request_id,
                max(0, int(session_number or 0)),
                role,
                content,
                tool_call_id,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                reasoning_content,
                _now(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def mark_message_synced(message_id: int, *, synced: bool = True) -> None:
    """Flag one locally-stored message as durably mirrored to Turso (or not)."""
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE agent_messages SET turso_synced = ? WHERE id = ?",
            (1 if synced else 0, message_id),
        )
        await db.commit()


async def session_sync_status(project_id: str, session_number: int) -> dict[str, Any]:
    """Aggregate local Turso-sync status for one chat session.

    Returns ``{"total": N, "synced": M, "all_saved": bool}`` for every
    message locally recorded under ``session_number`` for ``project_id``.
    ``all_saved`` is ``True`` only when there is at least one message and
    every one of them has been mirrored to Turso (``turso_synced = 1``).
    Used to drive the green ("all_saved") / red (not all saved) brain
    indicator in the GUI.
    """
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(turso_synced), 0) FROM agent_messages "
            "WHERE project_id = ? AND session_number = ?",
            (project_id, int(session_number or 0)),
        ) as cur:
            row = await cur.fetchone()
    total = int(row[0] or 0) if row else 0
    synced = int(row[1] or 0) if row else 0
    # Vacuously "all saved" when there is nothing to save yet (no messages
    # recorded for this session so far) — the brain indicator should read
    # green before the very first message, not red.
    return {"total": total, "synced": synced, "all_saved": synced == total}


def sanitize_provider_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair tool_calls / tool-response pairs for OpenAI-compatible providers.

    DeepSeek returns HTTP 400 when an assistant tool_calls message is not
    followed by a tool result for every call id (common after an interrupted
    turn or a tool that raised before its result was stored). Also drops
    leading orphaned tool messages left by history-window truncation.
    """
    while messages and messages[0].get("role") == "tool":
        messages = messages[1:]

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = dict(messages[i])
        out.append(msg)
        tool_calls = msg.get("tool_calls") if msg.get("role") == "assistant" else None
        if not tool_calls:
            i += 1
            continue
        expected: list[str] = []
        for call in tool_calls:
            if isinstance(call, dict):
                call_id = str(call.get("id") or "")
                if call_id:
                    expected.append(call_id)
        found: set[str] = set()
        i += 1
        while i < len(messages) and messages[i].get("role") == "tool":
            tool_msg = dict(messages[i])
            call_id = str(tool_msg.get("tool_call_id") or "")
            if call_id in expected and call_id not in found:
                out.append(tool_msg)
                found.add(call_id)
            i += 1
        for call_id in expected:
            if call_id not in found:
                out.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({
                        "ok": False,
                        "error": "tool_result_missing",
                        "message": "Previous tool call was interrupted and has no result.",
                    }, ensure_ascii=False),
                })
    return out


async def latest_message_session(project_id: str) -> int:
    """Return the highest ``session_number`` stored for the project (0 if none)."""
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT MAX(session_number) FROM agent_messages WHERE project_id = ?",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def conversation_messages(
    project_id: str,
    *,
    limit: int = 80,
    last_session_only: bool = True,
    session_number: int | None = None,
) -> list[dict[str, Any]]:
    """Load provider chat history.

    By default only the latest chat session is returned so each user turn does
    not re-hydrate every prior tool/plan message. Pass
    ``last_session_only=False`` for a sliding window across all sessions, or
    set ``session_number`` to pin a specific session.
    """
    await ensure_cloud_agent_tables()
    target_session = session_number
    if target_session is None and last_session_only:
        target_session = await latest_message_session(project_id)

    async with aiosqlite.connect(settings.resolved_db_path) as db:
        if target_session is not None and target_session > 0:
            async with db.execute(
                "SELECT role, content, tool_call_id, tool_calls, reasoning_content FROM "
                "(SELECT id, role, content, tool_call_id, tool_calls, reasoning_content "
                "FROM agent_messages WHERE project_id = ? AND session_number = ? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (project_id, int(target_session), max(1, limit)),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT role, content, tool_call_id, tool_calls, reasoning_content FROM "
                "(SELECT id, role, content, tool_call_id, tool_calls, reasoning_content "
                "FROM agent_messages WHERE project_id = ? ORDER BY id DESC LIMIT ?) "
                "ORDER BY id ASC",
                (project_id, max(1, limit)),
            ) as cur:
                rows = await cur.fetchall()
    messages: list[dict[str, Any]] = []
    for role, content, tool_call_id, tool_calls_raw, reasoning_content in rows:
        message: dict[str, Any] = {"role": role, "content": content}
        if tool_call_id:
            message["tool_call_id"] = tool_call_id
        if tool_calls_raw:
            try:
                message["tool_calls"] = json.loads(tool_calls_raw)
            except json.JSONDecodeError:
                pass
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
        messages.append(message)
    # Drop leading orphans and synthesize any missing tool results so the
    # window is always a valid OpenAI-compatible tool_calls/tool pair sequence.
    # DeepSeek rejects incomplete pairs with HTTP 400.
    return sanitize_provider_messages(messages)


async def get_request(request_id: str) -> dict[str, Any] | None:
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM cloud_agent_requests WHERE request_id = ?",
            (request_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def enqueue_request(
    request_id: str,
    project_id: str,
    message: str,
    *,
    model_profile: str | None,
    source: str,
    auto_start: bool,
) -> None:
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO cloud_agent_requests "
            "(request_id, project_id, message, model_profile, source, auto_start, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (request_id, project_id, message, model_profile, source, int(auto_start), _now()),
        )
        await db.commit()


async def mark_request(request_id: str, status: str, *, error: str = "") -> None:
    await ensure_cloud_agent_tables()
    fields = "status = ?, error = ?"
    values: list[Any] = [status, error[:4000]]
    if status == "running":
        fields += ", started_at = ?, attempts = attempts + 1"
        values.append(_now())
    if status in {"completed", "failed", "cancelled"}:
        fields += ", finished_at = ?"
        values.append(_now())
    values.append(request_id)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(f"UPDATE cloud_agent_requests SET {fields} WHERE request_id = ?", values)
        await db.commit()


async def pending_requests() -> list[dict[str, Any]]:
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM cloud_agent_requests WHERE status = 'pending' ORDER BY created_at ASC"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def clear_conversation(project_id: str) -> None:
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute("DELETE FROM agent_messages WHERE project_id = ?", (project_id,))
        await db.execute("DELETE FROM agent_sessions WHERE project_id = ?", (project_id,))
        await db.commit()
