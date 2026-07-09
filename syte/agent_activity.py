"""Real-time agent activity feed for Cursor-like chat UIs (sycord.com)."""

from __future__ import annotations

import asyncio
import json
import re
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
    "request_started",
    "request_completed",
    "request_failed",
    "agent_started",
    "agent_stopped",
    "agent_restarted",
    "processing",
    "status",
})

_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
_history_trackers: dict[str, int] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_agent_events_table() -> None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.executescript(EVENTS_SCHEMA)
        await db.commit()


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
) -> dict[str, Any]:
    """Persist an activity event and push to live subscribers."""
    await ensure_agent_events_table()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
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
                _now(),
            ),
        )
        await db.commit()
        event_id = int(cursor.lastrowid)

    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id, project_id, event_type, role, title, detail, payload, source, created_at "
            "FROM agent_events WHERE id = ?",
            (event_id,),
        ) as cur:
            row = await cur.fetchone()

    event = _event_row_to_dict(row) if row else {
        "id": event_id,
        "project_id": project_id,
        "event_type": event_type,
        "role": role,
        "title": title,
        "detail": detail,
        "payload": payload or {},
        "source": source,
        "created_at": _now(),
    }

    for queue in list(_subscribers.get(project_id, [])):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event


async def list_agent_events(
    project_id: str,
    *,
    since_id: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    await ensure_agent_events_table()
    limit = max(1, min(limit, 2000))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id, project_id, event_type, role, title, detail, payload, source, created_at "
            "FROM agent_events WHERE project_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (project_id, since_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_event_row_to_dict(row) for row in rows]


def subscribe_agent_activity(project_id: str) -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
    _subscribers[project_id].append(queue)
    return queue


def unsubscribe_agent_activity(project_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    subs = _subscribers.get(project_id, [])
    if queue in subs:
        subs.remove(queue)
    if not subs and project_id in _subscribers:
        del _subscribers[project_id]


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif part.get("text"):
                    parts.append(str(part["text"]))
        return "\n".join(p for p in parts if p).strip()
    if content is None:
        return ""
    return str(content).strip()


def _map_tool_event(tool_name: str, arguments: Any) -> tuple[str, str, str, dict[str, Any]]:
    name = (tool_name or "").lower().replace("-", "_")
    args: dict[str, Any] = {}
    if isinstance(arguments, dict):
        args = arguments
    elif isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                args = parsed
        except json.JSONDecodeError:
            args = {"raw": arguments}

    path = str(args.get("path") or args.get("file_path") or args.get("filepath") or "")
    command = str(args.get("command") or args.get("cmd") or "")

    if any(k in name for k in ("write", "create_file", "create")):
        return "file_created", "Created file", path or command[:200], args
    if any(k in name for k in ("edit", "patch", "replace", "modify")):
        return "file_modified", "Modified file", path or command[:200], args
    if any(k in name for k in ("delete", "remove", "unlink")):
        return "file_deleted", "Deleted file", path or command[:200], args
    if any(k in name for k in ("read", "view", "cat")):
        return "file_read", "Read file", path or command[:200], args
    if any(k in name for k in ("terminal", "bash", "shell", "run", "command", "exec")):
        return "command_run", "Ran command", command[:500] or name, args
    if "think" in name:
        return "thinking", "Thinking", _text_from_content(args.get("thought") or args.get("content"))[:500], args
    return "tool_call", tool_name or "tool", _text_from_content(arguments)[:500], args


def _events_from_message_item(item: dict[str, Any], *, source: str) -> list[dict[str, Any]]:
    message = item.get("message") or item
    role = str(message.get("role") or "assistant")
    content = message.get("content")
    events: list[dict[str, Any]] = []

    if role == "user":
        text = _text_from_content(content)
        if text:
            events.append({
                "event_type": "user_message",
                "role": "user",
                "title": "User",
                "detail": text[:4000],
                "payload": {"content": text},
                "source": source,
            })
        return events

    if role == "tool":
        text = _text_from_content(content)
        events.append({
            "event_type": "tool_call",
            "role": "tool",
            "title": "Tool result",
            "detail": text[:4000],
            "payload": {"content": text},
            "source": source,
        })
        return events

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type == "text":
                text = _text_from_content(part.get("text"))
                if text:
                    events.append({
                        "event_type": "assistant_message",
                        "role": "assistant",
                        "title": "Assistant",
                        "detail": text[:4000],
                        "payload": {"content": text},
                        "source": source,
                    })
            elif part_type in {"tool_use", "tool_call", "function_call"}:
                tool_name = str(
                    part.get("name")
                    or part.get("tool")
                    or part.get("function", {}).get("name")
                    or "tool"
                )
                arguments = part.get("input") or part.get("arguments") or part.get("function", {}).get("arguments")
                event_type, title, detail, payload = _map_tool_event(tool_name, arguments)
                payload = {**payload, "tool": tool_name}
                events.append({
                    "event_type": event_type,
                    "role": "assistant",
                    "title": title,
                    "detail": detail[:4000],
                    "payload": payload,
                    "source": source,
                })
            elif part_type == "thinking" or part.get("thinking"):
                text = _text_from_content(part.get("thinking") or part.get("text"))
                if text:
                    events.append({
                        "event_type": "thinking",
                        "role": "assistant",
                        "title": "Thinking",
                        "detail": text[:4000],
                        "payload": {"content": text},
                        "source": source,
                    })
        return events

    text = _text_from_content(content)
    if text:
        lowered = text.lower()
        if re.search(r"", text, re.I) or lowered.startswith("thinking:"):
            events.append({
                "event_type": "thinking",
                "role": "assistant",
                "title": "Thinking",
                "detail": re.sub(r"</?think>", "", text, flags=re.I)[:4000],
                "payload": {"content": text},
                "source": source,
            })
        else:
            events.append({
                "event_type": "assistant_message",
                "role": "assistant",
                "title": "Assistant",
                "detail": text[:4000],
                "payload": {"content": text},
                "source": source,
            })
    return events


def extract_events_from_state(
    state: dict[str, Any],
    *,
    source: str = "agent",
    since_index: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Parse Continue /state history into structured activity events."""
    history = (state.get("session") or {}).get("history") or []
    events: list[dict[str, Any]] = []
    start = max(0, since_index)
    for item in history[start:]:
        if isinstance(item, dict):
            events.extend(_events_from_message_item(item, source=source))
    return events, len(history)


async def ingest_agent_state(
    project_id: str,
    state: dict[str, Any],
    *,
    source: str = "agent",
) -> list[dict[str, Any]]:
    """Diff Continue state history and persist new activity events."""
    key = f"{project_id}:{source}"
    since = _history_trackers.get(key, 0)
    raw_events, new_index = extract_events_from_state(state, source=source, since_index=since)
    _history_trackers[key] = new_index

    recorded: list[dict[str, Any]] = []
    for raw in raw_events:
        recorded.append(
            await record_agent_event(
                project_id,
                raw["event_type"],
                role=raw.get("role", "assistant"),
                title=raw.get("title", ""),
                detail=raw.get("detail", ""),
                payload=raw.get("payload"),
                source=raw.get("source", source),
            )
        )
    return recorded


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


def reset_history_tracker(project_id: str, *, source: str = "agent") -> None:
    _history_trackers.pop(f"{project_id}:{source}", None)
