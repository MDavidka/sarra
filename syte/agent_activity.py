"""Real-time agent activity feed for Cursor-like chat UIs (sycord.com)."""

from __future__ import annotations

import asyncio
import json
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
    "file_changed",
    "command_output",
    "agent_started",
    "agent_stopped",
    "agent_restarted",
    "processing",
    "status",
    "service_action",
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
) -> dict[str, Any]:
    """Persist an activity event and push to live subscribers."""
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
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        async with db.execute(
            "SELECT id, project_id, event_type, role, title, detail, payload, source, created_at "
            "FROM agent_events WHERE project_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (project_id, since_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_event_row_to_dict(row) for row in rows]


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
    query = str(args.get("query") or args.get("pattern") or args.get("search") or "")
    file_operation = str(args.get("operation") or args.get("action") or command).lower()

    if any(k in name for k in ("syte_service", "syte-service", "service")):
        action = str(args.get("action") or args.get("cmd") or "")
        cmd = str(args.get("command") or "")
        detail = cmd or action or name
        return "service_action", f"Service: {action or name}", detail[:500], args
    if any(k in name for k in ("syte_access", "syte-access")) and "preview" in name:
        action = str(args.get("action") or "access")
        return "service_action", f"Preview: {action}", _text_from_content(arguments)[:500], args
    if name in {"file_editor", "fileeditor"}:
        if any(token in file_operation for token in ("view", "read", "show")):
            return "file_read", "Read file", path, args
        if any(token in file_operation for token in ("create", "new")):
            return "file_created", "Create file", path, args
        if any(token in file_operation for token in ("delete", "remove", "unlink")):
            return "file_deleted", "Delete file", path, args
        if any(token in file_operation for token in ("search", "find", "grep")):
            return "file_search", "Search", path or query, args
        return "file_modified", "Rewrite file", path, args
    if any(k in name for k in ("grep", "ripgrep", "rg", "search", "find", "glob", "list_dir", "ls")):
        detail = path or query or command[:200] or name
        return "file_search", "Search", detail, args
    if any(k in name for k in ("write", "create_file", "create")):
        return "file_created", "Create file", path or command[:200], args
    if any(k in name for k in ("edit", "patch", "replace", "modify", "rewrite")):
        return "file_modified", "Rewrite file", path or command[:200], args
    if any(k in name for k in ("delete", "remove", "unlink")):
        return "file_deleted", "Delete file", path or command[:200], args
    if any(k in name for k in ("read", "view", "cat")):
        return "file_read", "Read file", path or command[:200], args
    if any(k in name for k in ("terminal", "bash", "shell", "run", "command", "exec")):
        return "command_run", "Ran command", command[:500] or name, args
    if "think" in name:
        return "thinking", "Thinking", _text_from_content(args.get("thought") or args.get("content"))[:500], args
    return "tool_call", tool_name or "tool", _text_from_content(arguments)[:500], args


def _openhands_text(value: Any) -> str:
    """Extract text from OpenHands' transport-safe event payloads."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        return _openhands_text(
            value.get("content") or value.get("text") or value.get("message")
        )
    return ""


def _openhands_action_arguments(event: dict[str, Any]) -> dict[str, Any]:
    action = event.get("action")
    if isinstance(action, dict):
        return {
            key: value
            for key, value in action.items()
            if key not in {"kind", "summary"}
        }

    tool_call = event.get("tool_call")
    if not isinstance(tool_call, dict):
        return {}
    function = tool_call.get("function")
    if isinstance(function, dict):
        raw = function.get("arguments")
    else:
        raw = tool_call.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"raw": raw}
    return {}


def extract_events_from_openhands_event(
    event: dict[str, Any],
    *,
    source: str = "openhands",
    request_id: str | None = None,
    token_snapshot: str = "",
) -> list[dict[str, Any]]:
    """Map one native OpenHands WebSocket event to Syte's stable activity feed.

    OpenHands uses a discriminated ``kind`` field on event payloads.  This
    mapper intentionally accepts a few older spellings as well so a server
    upgrade cannot make the UI silently lose activity events.
    """
    kind = str(event.get("kind") or event.get("type") or event.get("event_type") or "")
    kind_lower = kind.lower()
    common = {
        "request_id": request_id or "",
        "openhands_event_id": str(event.get("id") or ""),
        "runtime": "openhands",
    }

    if kind_lower in {"streamingdeltaevent", "tokenevent"}:
        delta = str(event.get("content") or event.get("delta") or "")
        reasoning = str(event.get("reasoning_content") or "")
        raw: list[dict[str, Any]] = []
        if reasoning:
            raw.append({
                "event_type": "thinking",
                "role": "assistant",
                "title": "Thinking",
                "detail": reasoning[:4000],
                "payload": {**common, "content": reasoning},
                "source": source,
            })
        if delta:
            raw.append({
                "event_type": "token_delta",
                "role": "assistant",
                "title": "Assistant",
                "detail": delta[:4000],
                "payload": {
                    **common,
                    "delta": delta,
                    "snapshot": token_snapshot,
                },
                "source": source,
            })
        return raw

    if kind_lower == "messageevent":
        message = event.get("llm_message") or event.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        role = str(message.get("role") or event.get("source") or "assistant")
        content = _openhands_text(message.get("content"))
        reasoning = str(
            message.get("reasoning_content") or event.get("reasoning_content") or ""
        )
        raw = []
        if reasoning:
            raw.append({
                "event_type": "thinking",
                "role": "assistant",
                "title": "Thinking",
                "detail": reasoning[:4000],
                "payload": {**common, "content": reasoning},
                "source": source,
            })
        if content:
            is_user = role == "user" or event.get("source") == "user"
            raw.append({
                "event_type": "user_message" if is_user else "assistant_message",
                "role": "user" if is_user else "assistant",
                "title": "User" if is_user else "Assistant",
                "detail": content[:4000],
                "payload": {**common, "content": content},
                "source": source,
            })
        return raw

    if kind_lower == "actionevent":
        tool_call = event.get("tool_call") or {}
        tool_name = str(
            event.get("tool_name")
            or (tool_call.get("name") if isinstance(tool_call, dict) else "")
            or "tool"
        )
        args = _openhands_action_arguments(event)
        event_type, title, detail, payload = _map_tool_event(tool_name, args)
        summary = str(event.get("summary") or "")
        thought = _openhands_text(event.get("thought"))
        reasoning = str(event.get("reasoning_content") or "")
        raw = []
        if reasoning or thought:
            raw.append({
                "event_type": "thinking",
                "role": "assistant",
                "title": "Thinking",
                "detail": (reasoning or thought)[:4000],
                "payload": {**common, "content": reasoning or thought},
                "source": source,
            })
        raw.append({
            "event_type": event_type,
            "role": "assistant",
            "title": title,
            "detail": (summary or detail)[:4000],
            "payload": {
                **common,
                **payload,
                "tool": tool_name,
                "tool_call_id": event.get("tool_call_id") or "",
                "phase": "started",
            },
            "source": source,
        })
        return raw

    if kind_lower in {"observationevent", "agenterrorevent", "userrejectobservation"}:
        tool_name = str(event.get("tool_name") or "tool")
        observation = event.get("observation")
        content = _openhands_text(
            observation.get("content") if isinstance(observation, dict) else observation
        )
        if not content:
            content = str(event.get("error") or event.get("rejection_reason") or "")
        is_terminal = any(
            token in tool_name.lower() for token in ("terminal", "bash", "shell", "command")
        )
        return [{
            "event_type": "command_output" if is_terminal else "tool_call_finished",
            "role": "tool",
            "title": "Command output" if is_terminal else f"{tool_name} finished",
            "detail": content[:4000],
            "payload": {
                **common,
                "tool": tool_name,
                "tool_call_id": event.get("tool_call_id") or "",
                "phase": "finished",
                "is_error": bool(
                    event.get("error")
                    or event.get("rejection_reason")
                    or (
                        observation.get("is_error")
                        if isinstance(observation, dict)
                        else False
                    )
                ),
            },
            "source": source,
        }]

    if kind_lower == "conversationerrorevent":
        detail = str(event.get("detail") or event.get("code") or "OpenHands conversation failed")
        return [{
            "event_type": "request_failed",
            "role": "system",
            "title": "OpenHands error",
            "detail": detail[:4000],
            "payload": {**common, "error": detail, "code": event.get("code") or ""},
            "source": source,
        }]

    if kind_lower == "conversationstateupdateevent":
        key = str(event.get("key") or "")
        value = event.get("value")
        status = ""
        if key == "execution_status":
            status = str(value or "")
        elif key == "full_state" and isinstance(value, dict):
            status = str(value.get("execution_status") or "")
        if status == "running":
            return [{
                "event_type": "processing",
                "role": "system",
                "title": "OpenHands working",
                "detail": "Agent is processing…",
                "payload": {**common, "execution_status": status},
                "source": source,
            }]
    return []


async def ingest_openhands_event(
    project_id: str,
    event: dict[str, Any],
    *,
    source: str = "openhands",
    request_id: str | None = None,
    token_snapshot: str = "",
) -> list[dict[str, Any]]:
    """Persist one native OpenHands event and fan it out to SSE consumers."""
    raw_events = extract_events_from_openhands_event(
        event,
        source=source,
        request_id=request_id,
        token_snapshot=token_snapshot,
    )
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
