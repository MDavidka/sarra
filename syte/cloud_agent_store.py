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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_call_id TEXT,
    tool_calls TEXT,
    reasoning_content TEXT,
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

_ensured_paths: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_cloud_agent_tables() -> None:
    path = str(settings.resolved_db_path)
    if path in _ensured_paths:
        return
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=path)
        await db.executescript(SCHEMA)
        async with db.execute("PRAGMA table_info(agent_messages)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "reasoning_content" not in cols:
            await db.execute("ALTER TABLE agent_messages ADD COLUMN reasoning_content TEXT")
        # A VM restart may interrupt an active turn. Re-admit it to the queue.
        await db.execute(
            "UPDATE cloud_agent_requests SET status = 'pending', started_at = NULL "
            "WHERE status = 'running'"
        )
        await db.commit()
    _ensured_paths.add(path)


async def ensure_session(project_id: str, model_profile: str) -> None:
    await ensure_cloud_agent_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_sessions(project_id, model_profile, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET "
            "model_profile = excluded.model_profile, updated_at = excluded.updated_at",
            (project_id, model_profile, now, now),
        )
        await db.commit()


async def append_message(
    project_id: str,
    request_id: str,
    role: str,
    content: str,
    *,
    tool_call_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
) -> None:
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_messages "
            "(project_id, request_id, role, content, tool_call_id, tool_calls, "
            "reasoning_content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                request_id,
                role,
                content,
                tool_call_id,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                reasoning_content,
                _now(),
            ),
        )
        await db.commit()


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


async def conversation_messages(project_id: str, *, limit: int = 80) -> list[dict[str, Any]]:
    await ensure_cloud_agent_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
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
