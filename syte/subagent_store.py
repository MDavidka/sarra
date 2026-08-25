"""Local, always-available subagent task records.

Subagent tasks used to be persisted only to Turso. When Turso is unconfigured
or unreachable (a normal state — the brain indicator has an explicit
"unconfigured" mode) nothing durable was written, which caused two visible
problems:

* ``await_subagent`` returned ``subagent_not_found`` once the bounded in-memory
  result cache rotated or the process restarted.
* The GUI subagent tab is only revealed by *replayed activity events*, so a
  subagent whose events aged out of the replay window became invisible even
  though it ran.

This module mirrors every delegated task into local SQLite so both the tool and
the GUI have a cheap, reliable source of truth. Turso mirroring is unchanged
and remains the cross-host durable copy.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from syte.config import settings

logger = logging.getLogger(__name__)

SUBAGENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_subagent_local (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session INTEGER NOT NULL DEFAULT 0,
    turso_session_id TEXT NOT NULL DEFAULT '',
    parent_request_id TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'research',
    profile TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    background INTEGER NOT NULL DEFAULT 0,
    files TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'running',
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    usage TEXT NOT NULL DEFAULT '{}',
    cost TEXT NOT NULL DEFAULT '{}',
    activity_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_subagent_local_project
    ON agent_subagent_local(project_id, started_at);
CREATE INDEX IF NOT EXISTS idx_subagent_local_session
    ON agent_subagent_local(project_id, session, started_at);
"""

SUBAGENT_MAX_PER_PROJECT = 200
SUBAGENT_MAX_AGE_DAYS = 30
TERMINAL_STATUSES = frozenset({"completed", "failed", "timeout", "cancelled", "partial"})

_table_ready = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_subagent_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        await db.executescript(SUBAGENT_SCHEMA)
        await db.commit()
    _table_ready = True


def _dumps(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return fallback


def _loads(raw: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(raw or ""))
    except (json.JSONDecodeError, ValueError, TypeError):
        return fallback
    return parsed


async def record_task(
    task_id: str,
    project_id: str,
    task: str,
    *,
    session: int = 0,
    turso_session_id: str | None = None,
    parent_request_id: str = "",
    mode: str = "research",
    profile: str = "",
    model: str = "",
    background: bool = False,
    files: list[str] | None = None,
) -> None:
    """Insert (or reset) a running subagent task row. Never raises."""
    if not task_id or not project_id:
        return
    try:
        await ensure_subagent_table()
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            await db.execute(
                "INSERT INTO agent_subagent_local (task_id, project_id, session, "
                "turso_session_id, parent_request_id, task, mode, profile, model, "
                "background, files, status, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?) "
                "ON CONFLICT(task_id) DO UPDATE SET status='running', "
                "task=excluded.task, mode=excluded.mode, files=excluded.files, "
                "started_at=excluded.started_at, finished_at=''",
                (
                    task_id,
                    project_id,
                    int(session or 0),
                    str(turso_session_id or ""),
                    str(parent_request_id or "")[:120],
                    str(task or "")[:8000],
                    str(mode or "research")[:32],
                    str(profile or "")[:64],
                    str(model or "")[:120],
                    1 if background else 0,
                    _dumps(list(files or []), "[]"),
                    _now(),
                ),
            )
            await db.commit()
        await _prune(project_id)
    except Exception:
        logger.debug("local subagent task record failed for %s", task_id, exc_info=True)


async def finalize_task(
    task_id: str,
    project_id: str,
    result: dict[str, Any],
) -> None:
    """Close a task row with its outcome. Never raises."""
    if not task_id or not project_id:
        return
    try:
        await ensure_subagent_table()
        status = str(
            result.get("status") or ("completed" if result.get("ok") else "failed")
        )[:32]
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            await db.execute(
                "UPDATE agent_subagent_local SET status = ?, result = ?, error = ?, "
                "usage = ?, cost = ?, activity_count = ?, finished_at = ? "
                "WHERE task_id = ? AND project_id = ?",
                (
                    status,
                    str(result.get("result") or "")[:20000],
                    str(result.get("error") or "")[:200],
                    _dumps(result.get("usage") if isinstance(result.get("usage"), dict) else {}, "{}"),
                    _dumps(result.get("cost") if isinstance(result.get("cost"), dict) else {}, "{}"),
                    int(result.get("activity_count") or 0),
                    _now(),
                    task_id,
                    project_id,
                ),
            )
            await db.commit()
    except Exception:
        logger.debug("local subagent finalize failed for %s", task_id, exc_info=True)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "task_id": row[0],
        "project_id": row[1],
        "session": int(row[2] or 0),
        "turso_session_id": row[3] or "",
        "parent_request_id": row[4] or "",
        "task": row[5] or "",
        "mode": row[6] or "research",
        "profile": row[7] or "",
        "model": row[8] or "",
        "background": bool(row[9]),
        "files": _loads(row[10], []),
        "status": row[11] or "running",
        "result": row[12] or "",
        "error": row[13] or "",
        "usage": _loads(row[14], {}),
        "cost": _loads(row[15], {}),
        "activity_count": int(row[16] or 0),
        "started_at": row[17],
        "finished_at": row[18] or "",
    }


_SELECT = (
    "SELECT task_id, project_id, session, turso_session_id, parent_request_id, task, "
    "mode, profile, model, background, files, status, result, error, usage, cost, "
    "activity_count, started_at, finished_at FROM agent_subagent_local"
)


async def get_task(task_id: str, project_id: str) -> dict[str, Any] | None:
    """Fetch one task row (used as the ``await_subagent`` recovery path)."""
    if not task_id:
        return None
    try:
        await ensure_subagent_table()
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            async with db.execute(
                f"{_SELECT} WHERE task_id = ? AND project_id = ?", (task_id, project_id)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_dict(row) if row else None
    except Exception:
        logger.debug("local subagent lookup failed for %s", task_id, exc_info=True)
        return None


async def list_tasks(
    project_id: str,
    *,
    session: int | str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Newest-first subagent tasks, optionally scoped to one session."""
    try:
        await ensure_subagent_table()
        limit = max(1, min(int(limit or 50), 500))
        where = ["project_id = ?"]
        params: list[Any] = [project_id]
        session_filter = await _resolve_session(project_id, session)
        if session_filter is not None:
            where.append("session = ?")
            params.append(session_filter)
        params.append(limit)
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            async with db.execute(
                f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY started_at DESC, rowid DESC LIMIT ?",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_dict(row) for row in rows]
    except Exception:
        logger.debug("local subagent list failed", exc_info=True)
        return []


async def _resolve_session(project_id: str, session: int | str | None) -> int | None:
    if session is None or str(session).strip() == "" or str(session).strip().lower() == "all":
        return None
    raw = str(session).strip().lower()
    if raw == "last":
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            async with db.execute(
                "SELECT MAX(session) FROM agent_subagent_local WHERE project_id = ?",
                (project_id,),
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def mark_orphans_cancelled(project_id: str) -> int:
    """Mark still-``running`` rows as cancelled (called on process/turn reset).

    Without this, a task interrupted by a restart stays ``running`` forever and
    ``await_subagent`` would wait on something that no longer exists.
    """
    try:
        await ensure_subagent_table()
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            cursor = await db.execute(
                "UPDATE agent_subagent_local SET status = 'cancelled', "
                "error = COALESCE(NULLIF(error, ''), 'subagent_cancelled'), finished_at = ? "
                "WHERE project_id = ? AND status = 'running'",
                (_now(), project_id),
            )
            await db.commit()
            return int(cursor.rowcount or 0)
    except Exception:
        logger.debug("local subagent orphan sweep failed", exc_info=True)
        return 0


async def _prune(project_id: str) -> None:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=SUBAGENT_MAX_AGE_DAYS)
    ).isoformat()
    try:
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            from syte.sqlite_utils import configure_sqlite

            await configure_sqlite(db, db_path=str(settings.resolved_db_path))
            await db.execute(
                "DELETE FROM agent_subagent_local WHERE project_id = ? AND started_at < ?",
                (project_id, cutoff),
            )
            await db.execute(
                "DELETE FROM agent_subagent_local WHERE project_id = ? AND task_id NOT IN ("
                "SELECT task_id FROM agent_subagent_local WHERE project_id = ? "
                "ORDER BY started_at DESC LIMIT ?)",
                (project_id, project_id, SUBAGENT_MAX_PER_PROJECT),
            )
            await db.commit()
    except Exception:
        logger.debug("local subagent prune failed", exc_info=True)


def reset_subagent_table_cache() -> None:
    """Test hook — forget that the table was already created."""
    global _table_ready
    _table_ready = False
