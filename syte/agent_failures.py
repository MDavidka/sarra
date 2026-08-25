"""Per-session failure log — every failed task, tool, request and subagent.

Activity events are ephemeral (pruned, replay-window limited) and mix success
with failure, so "what actually went wrong in this session?" was impossible to
answer from the chat feed. This module keeps a small, durable, *failure-only*
table next to the activity log and is surfaced in the GUI by double-clicking
the brain icon.

Recording is best-effort and must never break a turn: every public helper
swallows its own errors.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from syte.config import settings

logger = logging.getLogger(__name__)

FAILURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_failure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session INTEGER NOT NULL DEFAULT 0,
    request_id TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT 'main',
    subagent_task_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'tool',
    tool TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL DEFAULT '',
    retryable INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    event_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_failure_project ON agent_failure(project_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_failure_session ON agent_failure(project_id, session, id);
"""

# Keep the log small: it is a debugging aid, not an audit trail.
FAILURES_MAX_PER_PROJECT = 600
FAILURES_MAX_AGE_DAYS = 14
_PRUNE_EVERY = 25

# Failure kinds, ordered roughly by blast radius.
KIND_REQUEST = "request"
KIND_SUBAGENT = "subagent"
KIND_TOOL = "tool"
KIND_PROVIDER = "provider"
KIND_SESSION = "session"
KIND_PREVIEW = "preview"
KIND_DESIGN = "design"

# Errors that are normal control flow, not real failures. Recording these would
# bury genuine problems (the planner gate fires on almost every website turn).
_IGNORED_ERRORS = frozenset({
    "plan_required",
    "question_required",
    "research_readonly",
    "outside_file_scope",
    "file_reserved_by_subagent",
    "file_scope_conflict",
    "missing_file_scope",
    "subagent_queue_full",
    "await_timeout",
})

_table_ready = False
_write_counts: dict[str, int] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_failure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        await db.executescript(FAILURES_SCHEMA)
        await db.commit()
    _table_ready = True


async def _prune(project_id: str) -> None:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=FAILURES_MAX_AGE_DAYS)
    ).isoformat()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        await db.execute(
            "DELETE FROM agent_failure WHERE project_id = ? AND created_at < ?",
            (project_id, cutoff),
        )
        await db.execute(
            "DELETE FROM agent_failure WHERE project_id = ? AND id NOT IN ("
            "SELECT id FROM agent_failure WHERE project_id = ? ORDER BY id DESC LIMIT ?)",
            (project_id, project_id, FAILURES_MAX_PER_PROJECT),
        )
        await db.commit()


async def record_failure(
    project_id: str,
    kind: str,
    *,
    error: str = "",
    message: str = "",
    tool: str = "",
    target: str = "",
    session: int = 0,
    request_id: str = "",
    agent: str = "main",
    subagent_task_id: str = "",
    retryable: bool = False,
    detail: Any = None,
    event_id: int = 0,
) -> dict[str, Any] | None:
    """Append one failure row. Never raises."""
    if not project_id:
        return None
    try:
        await ensure_failure_table()
        detail_text = ""
        if detail is not None:
            detail_text = (
                detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
            )[:4000]
        row = {
            "project_id": project_id,
            "session": int(session or 0),
            "request_id": str(request_id or "")[:120],
            "agent": str(agent or "main")[:32],
            "subagent_task_id": str(subagent_task_id or "")[:64],
            "kind": str(kind or KIND_TOOL)[:32],
            "tool": str(tool or "")[:120],
            "error": str(error or "")[:160],
            "message": str(message or "")[:2000],
            "target": str(target or "")[:500],
            "retryable": 1 if retryable else 0,
            "detail": detail_text,
            "event_id": int(event_id or 0),
            "created_at": _now(),
        }
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            cursor = await db.execute(
                "INSERT INTO agent_failure (project_id, session, request_id, agent, "
                "subagent_task_id, kind, tool, error, message, target, retryable, detail, "
                "event_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["project_id"], row["session"], row["request_id"], row["agent"],
                    row["subagent_task_id"], row["kind"], row["tool"], row["error"],
                    row["message"], row["target"], row["retryable"], row["detail"],
                    row["event_id"], row["created_at"],
                ),
            )
            await db.commit()
            row["id"] = int(cursor.lastrowid)

        _write_counts[project_id] = _write_counts.get(project_id, 0) + 1
        if _write_counts[project_id] % _PRUNE_EVERY == 0:
            try:
                await _prune(project_id)
            except Exception:
                logger.debug("failure log prune failed", exc_info=True)
        return row
    except Exception:
        logger.debug("failed to record agent failure", exc_info=True)
        return None


def _coerce_json(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    raw = str(text or "").strip()
    if not raw.startswith("{"):
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def classify_event_failure(event_type: str, payload: Any, detail: Any) -> dict[str, Any] | None:
    """Return failure fields for a failing activity event, else ``None``.

    Central classifier so both the main agent and subagents are captured by the
    single ``record_agent_event`` hook — no per-call-site instrumentation.
    """
    kind = ""
    data = payload if isinstance(payload, dict) else {}
    body = _coerce_json(detail)

    if event_type == "request_failed":
        kind = KIND_REQUEST
    elif event_type == "subagent_failed":
        kind = KIND_SUBAGENT
    elif event_type == "tool_error":
        kind = KIND_TOOL
    elif event_type == "tool_call_finished":
        ok = data.get("ok")
        if ok is False or (ok is None and body.get("ok") is False):
            kind = KIND_TOOL
        else:
            return None
    elif event_type == "session_stopped":
        reason = str(data.get("reason") or "").strip().lower()
        if reason and reason not in {"completed", "done"}:
            kind = KIND_SESSION
        else:
            return None
    else:
        return None

    error = str(data.get("error") or body.get("error") or "").strip()
    if error in _IGNORED_ERRORS:
        return None
    message = str(
        data.get("message") or body.get("message") or data.get("detail") or ""
    ).strip()
    if not message and isinstance(detail, str):
        message = detail.strip()[:2000]

    tool = str(data.get("tool") or "").strip()
    target = str(
        data.get("path") or data.get("command") or body.get("path") or body.get("url") or ""
    ).strip()
    retryable = bool(data.get("retryable") or body.get("retryable"))
    if kind == KIND_TOOL and tool in {"screenshot_preview", "inspect_preview", "service"}:
        kind = KIND_PREVIEW
    if error in {"provider_error", "circuit_open", "quota_exhausted", "rate_limited"}:
        kind = KIND_PROVIDER

    fallback_error = {
        KIND_REQUEST: "request_failed",
        KIND_SUBAGENT: "subagent_failed",
        KIND_SESSION: "session_stopped",
    }.get(kind, "tool_failed")

    return {
        "kind": kind,
        "error": error or fallback_error,
        "message": message,
        "tool": tool,
        "target": target,
        "retryable": retryable,
        "agent": str(data.get("agent") or "main"),
        "subagent_task_id": str(data.get("subagent_task_id") or data.get("task_id") or ""),
        "request_id": str(data.get("request_id") or ""),
        "session": int(data.get("session") or 0) if str(data.get("session") or "").lstrip("-").isdigit() else 0,
    }


async def maybe_record_event_failure(
    project_id: str, event: dict[str, Any]
) -> dict[str, Any] | None:
    """Mirror a failing activity event into the failure log. Never raises."""
    try:
        fields = classify_event_failure(
            str(event.get("event_type") or ""), event.get("payload"), event.get("detail")
        )
        if not fields:
            return None
        return await record_failure(
            project_id,
            fields.pop("kind"),
            event_id=int(event.get("id") or 0),
            **fields,
        )
    except Exception:
        logger.debug("failure mirror failed", exc_info=True)
        return None


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "project_id": row[1],
        "session": int(row[2] or 0),
        "request_id": row[3] or "",
        "agent": row[4] or "main",
        "subagent_task_id": row[5] or "",
        "kind": row[6] or KIND_TOOL,
        "tool": row[7] or "",
        "error": row[8] or "",
        "message": row[9] or "",
        "target": row[10] or "",
        "retryable": bool(row[11]),
        "detail": row[12] or "",
        "event_id": int(row[13] or 0),
        "created_at": row[14],
    }


_SELECT = (
    "SELECT id, project_id, session, request_id, agent, subagent_task_id, kind, tool, "
    "error, message, target, retryable, detail, event_id, created_at FROM agent_failure"
)


async def list_failures(
    project_id: str,
    *,
    session: int | str | None = None,
    limit: int = 200,
    kind: str = "",
) -> list[dict[str, Any]]:
    """Newest-first failures for a project, optionally scoped to one session."""
    await ensure_failure_table()
    limit = max(1, min(int(limit or 200), 1000))
    where = ["project_id = ?"]
    params: list[Any] = [project_id]

    session_filter = await resolve_session_filter(project_id, session)
    if session_filter is not None:
        where.append("session = ?")
        params.append(session_filter)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    params.append(limit)

    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        async with db.execute(
            f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", params
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_dict(row) for row in rows]


async def resolve_session_filter(
    project_id: str, session: int | str | None
) -> int | None:
    """Map ``None``/``"last"``/a number onto a concrete session number."""
    if session is None or str(session).strip() == "" or str(session).strip().lower() == "all":
        return None
    raw = str(session).strip().lower()
    if raw == "last":
        await ensure_failure_table()
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            async with db.execute(
                "SELECT MAX(session) FROM agent_failure WHERE project_id = ?",
                (project_id,),
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def failure_summary(
    project_id: str, *, session: int | str | None = None
) -> dict[str, Any]:
    """Counts by kind/tool for the badge + panel header."""
    failures = await list_failures(project_id, session=session, limit=1000)
    by_kind: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    for row in failures:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        if row["tool"]:
            by_tool[row["tool"]] = by_tool.get(row["tool"], 0) + 1
    return {
        "total": len(failures),
        "by_kind": by_kind,
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])[:10]),
        "latest_at": failures[0]["created_at"] if failures else None,
        "sessions": sorted({row["session"] for row in failures}, reverse=True),
    }


async def clear_failures(project_id: str, *, session: int | str | None = None) -> int:
    """Delete failures for a project (optionally one session). Returns rows removed."""
    await ensure_failure_table()
    where = ["project_id = ?"]
    params: list[Any] = [project_id]
    session_filter = await resolve_session_filter(project_id, session)
    if session_filter is not None:
        where.append("session = ?")
        params.append(session_filter)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        cursor = await db.execute(
            f"DELETE FROM agent_failure WHERE {' AND '.join(where)}", params
        )
        await db.commit()
        return int(cursor.rowcount or 0)


def reset_failure_table_cache() -> None:
    """Test hook — forget that the table was already created."""
    global _table_ready
    _table_ready = False
    _write_counts.clear()
