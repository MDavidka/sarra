"""Durable agent artifacts: screenshots, plans, interactive questions, MCP addons.

Local SQLite is the source of truth. Activity events mirror summaries into the
chat/session feed (and Turso when configured). Large screenshot blobs stay on
disk under ``data/cloud-agent/screenshots/`` and are served by API routes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from syte.config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    session_number INTEGER NOT NULL DEFAULT 0,
    turso_session_id TEXT,
    steps TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_plans_project
ON agent_plans(project_id, id);

CREATE TABLE IF NOT EXISTS agent_screenshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    session_number INTEGER NOT NULL DEFAULT 0,
    turso_session_id TEXT,
    route TEXT NOT NULL DEFAULT '/',
    url TEXT NOT NULL DEFAULT '',
    viewport TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    format TEXT NOT NULL DEFAULT 'png',
    bytes INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    thumb_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_screenshots_project
ON agent_screenshots(project_id, created_at);

CREATE TABLE IF NOT EXISTS agent_questions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    session_number INTEGER NOT NULL DEFAULT 0,
    turso_session_id TEXT,
    prompt TEXT NOT NULL,
    question_type TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '[]',
    min_value REAL,
    max_value REAL,
    step_value REAL,
    default_value TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    answer TEXT,
    answered_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_questions_project
ON agent_questions(project_id, status, created_at);

CREATE TABLE IF NOT EXISTS agent_mcp_addons (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    transport TEXT NOT NULL DEFAULT 'stdio',
    command TEXT NOT NULL DEFAULT '',
    args TEXT NOT NULL DEFAULT '[]',
    env TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'available',
    tools_json TEXT NOT NULL DEFAULT '[]',
    connected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_mcp_addons_project
ON agent_mcp_addons(project_id, name);

CREATE TABLE IF NOT EXISTS agent_session_stops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session_number INTEGER NOT NULL DEFAULT 0,
    turso_session_id TEXT,
    reason TEXT NOT NULL DEFAULT 'stopped',
    source TEXT NOT NULL DEFAULT 'api',
    stopped_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_session_stops_project
ON agent_session_stops(project_id, stopped_at);

CREATE TABLE IF NOT EXISTS agent_project_skills (
    project_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    parameters TEXT NOT NULL DEFAULT '{}',
    enabled_at TEXT NOT NULL,
    PRIMARY KEY (project_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_project_skills_project
ON agent_project_skills(project_id, enabled_at);

CREATE TABLE IF NOT EXISTS agent_custom_skills (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_custom_skills_project
ON agent_custom_skills(project_id, skill_id);
"""

_SCHEMA_EPOCH = 3
_ensured_paths: dict[str, int] = {}

# In-process waiters for interactive questions (question_id -> Future[answer]).
_pending_answers: dict[str, asyncio.Future[Any]] = {}
# Connected MCP addon runtime handles keyed by (project_id, addon_id).
_connected_mcp: dict[tuple[str, str], dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}" if prefix else uuid.uuid4().hex


async def ensure_artifact_tables() -> None:
    path = str(settings.resolved_db_path)
    if _ensured_paths.get(path) == _SCHEMA_EPOCH:
        return
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=path)
        await db.executescript(SCHEMA)
        await db.commit()
    _ensured_paths[path] = _SCHEMA_EPOCH


def screenshots_dir(project_id: str) -> Path:
    from syte.workspace import ensure_workspace

    path = ensure_workspace(project_id) / "data" / "cloud-agent" / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


async def save_plan(
    project_id: str,
    steps: list[str],
    *,
    note: str = "",
    request_id: str = "",
    session_number: int = 0,
    turso_session_id: str | None = None,
) -> dict[str, Any]:
    await ensure_artifact_tables()
    now = _now()
    steps_clean = [str(s).strip() for s in steps if str(s).strip()]
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cur = await db.execute(
            "INSERT INTO agent_plans "
            "(project_id, request_id, session_number, turso_session_id, steps, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                request_id,
                int(session_number or 0),
                turso_session_id,
                json.dumps(steps_clean, ensure_ascii=False),
                (note or "")[:2000],
                now,
            ),
        )
        await db.commit()
        plan_id = int(cur.lastrowid)
    return {
        "id": plan_id,
        "project_id": project_id,
        "request_id": request_id,
        "session_number": session_number,
        "turso_session_id": turso_session_id,
        "steps": steps_clean,
        "note": note or "",
        "created_at": now,
    }


async def list_plans(project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    await ensure_artifact_tables()
    limit = max(1, min(int(limit or 50), 200))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id, project_id, request_id, session_number, turso_session_id, "
            "steps, note, created_at FROM agent_plans WHERE project_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            steps = json.loads(row[5] or "[]")
        except json.JSONDecodeError:
            steps = []
        out.append({
            "id": row[0],
            "project_id": row[1],
            "request_id": row[2],
            "session_number": row[3],
            "turso_session_id": row[4],
            "steps": steps,
            "note": row[6] or "",
            "created_at": row[7],
        })
    return out


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------


async def save_screenshot_record(
    project_id: str,
    *,
    viewport: str,
    width: int,
    height: int,
    png_bytes: bytes,
    route: str = "/",
    url: str = "",
    request_id: str = "",
    session_number: int = 0,
    turso_session_id: str | None = None,
    thumb_bytes: bytes | None = None,
) -> dict[str, Any]:
    await ensure_artifact_tables()
    shot_id = _new_id("shot_")
    root = screenshots_dir(project_id)
    path = root / f"{shot_id}.png"
    path.write_bytes(png_bytes)
    thumb_path = ""
    if thumb_bytes:
        tpath = root / f"{shot_id}.thumb.png"
        tpath.write_bytes(thumb_bytes)
        thumb_path = str(tpath)
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_screenshots "
            "(id, project_id, request_id, session_number, turso_session_id, route, url, "
            "viewport, width, height, format, bytes, path, thumb_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'png', ?, ?, ?, ?)",
            (
                shot_id,
                project_id,
                request_id,
                int(session_number or 0),
                turso_session_id,
                route or "/",
                url or "",
                viewport,
                int(width),
                int(height),
                len(png_bytes),
                str(path),
                thumb_path,
                now,
            ),
        )
        await db.commit()
    return {
        "id": shot_id,
        "project_id": project_id,
        "viewport": viewport,
        "width": width,
        "height": height,
        "route": route or "/",
        "url": url or "",
        "bytes": len(png_bytes),
        "path": str(path),
        "thumb_path": thumb_path,
        "created_at": now,
        "image_base64": base64.b64encode(png_bytes).decode("ascii"),
        "thumb_base64": (
            base64.b64encode(thumb_bytes).decode("ascii") if thumb_bytes else ""
        ),
    }


async def get_screenshot(project_id: str, screenshot_id: str) -> dict[str, Any] | None:
    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id, project_id, request_id, session_number, turso_session_id, route, url, "
            "viewport, width, height, format, bytes, path, thumb_path, created_at "
            "FROM agent_screenshots WHERE project_id = ? AND id = ?",
            (project_id, screenshot_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "project_id": row[1],
        "request_id": row[2],
        "session_number": row[3],
        "turso_session_id": row[4],
        "route": row[5],
        "url": row[6],
        "viewport": row[7],
        "width": row[8],
        "height": row[9],
        "format": row[10],
        "bytes": row[11],
        "path": row[12],
        "thumb_path": row[13],
        "created_at": row[14],
    }


async def list_screenshots(project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    await ensure_artifact_tables()
    limit = max(1, min(int(limit or 50), 200))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id, project_id, request_id, session_number, route, url, viewport, "
            "width, height, format, bytes, created_at FROM agent_screenshots "
            "WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "project_id": r[1],
            "request_id": r[2],
            "session_number": r[3],
            "route": r[4],
            "url": r[5],
            "viewport": r[6],
            "width": r[7],
            "height": r[8],
            "format": r[9],
            "bytes": r[10],
            "created_at": r[11],
            "image_url": f"/api/projects/{project_id}/agent/screenshots/{r[0]}",
            "thumb_url": f"/api/projects/{project_id}/agent/screenshots/{r[0]}?variant=thumb",
        }
        for r in rows
    ]


def read_screenshot_bytes(record: dict[str, Any], *, variant: str = "full") -> bytes | None:
    path = record.get("thumb_path") if variant == "thumb" else record.get("path")
    if variant == "thumb" and not path:
        path = record.get("path")
    if not path:
        return None
    file_path = Path(str(path))
    if not file_path.is_file():
        return None
    return file_path.read_bytes()


def optimize_png_for_chat(png_bytes: bytes, *, max_bytes: int = 90_000) -> str:
    """Return a base64 PNG suitable for chat payloads (may truncate to empty if huge).

    Without an image codec we keep the original when small enough; otherwise the
    chat UI should load via ``image_url`` / ``thumb_url`` instead of inline data.
    """
    if not png_bytes:
        return ""
    if len(png_bytes) <= max_bytes:
        return base64.b64encode(png_bytes).decode("ascii")
    return ""


# ---------------------------------------------------------------------------
# Interactive questions
# ---------------------------------------------------------------------------

QUESTION_TYPES = frozenset({"answer", "input", "slider", "choice", "multi_choice"})


async def create_question(
    project_id: str,
    prompt: str,
    question_type: str,
    *,
    options: list[str] | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    step_value: float | None = None,
    default_value: str | None = None,
    request_id: str = "",
    session_number: int = 0,
    turso_session_id: str | None = None,
) -> dict[str, Any]:
    await ensure_artifact_tables()
    qtype = (question_type or "answer").strip().lower()
    if qtype not in QUESTION_TYPES:
        qtype = "answer"
    qid = _new_id("q_")
    now = _now()
    opts = [str(o) for o in (options or []) if str(o).strip()]
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_questions "
            "(id, project_id, request_id, session_number, turso_session_id, prompt, "
            "question_type, options, min_value, max_value, step_value, default_value, "
            "status, answer, answered_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?)",
            (
                qid,
                project_id,
                request_id,
                int(session_number or 0),
                turso_session_id,
                (prompt or "").strip()[:2000],
                qtype,
                json.dumps(opts, ensure_ascii=False),
                min_value,
                max_value,
                step_value,
                default_value,
                now,
            ),
        )
        await db.commit()
    loop = asyncio.get_running_loop()
    _pending_answers[qid] = loop.create_future()
    return {
        "id": qid,
        "project_id": project_id,
        "prompt": (prompt or "").strip(),
        "question_type": qtype,
        "options": opts,
        "min_value": min_value,
        "max_value": max_value,
        "step_value": step_value,
        "default_value": default_value,
        "status": "pending",
        "created_at": now,
    }


async def answer_question(
    project_id: str,
    question_id: str,
    answer: Any,
) -> dict[str, Any]:
    await ensure_artifact_tables()
    encoded = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cur = await db.execute(
            "UPDATE agent_questions SET status = 'answered', answer = ?, answered_at = ? "
            "WHERE project_id = ? AND id = ? AND status = 'pending'",
            (encoded[:8000], now, project_id, question_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            async with db.execute(
                "SELECT status, answer FROM agent_questions WHERE project_id = ? AND id = ?",
                (project_id, question_id),
            ) as sel:
                row = await sel.fetchone()
            if not row:
                return {"ok": False, "error": "not_found", "message": "Question not found"}
            return {
                "ok": True,
                "id": question_id,
                "status": row[0],
                "answer": row[1],
                "already_answered": True,
            }
    fut = _pending_answers.pop(question_id, None)
    if fut and not fut.done():
        fut.set_result(encoded)
    return {
        "ok": True,
        "id": question_id,
        "status": "answered",
        "answer": encoded,
        "answered_at": now,
    }


async def wait_for_answer(
    question_id: str,
    *,
    timeout_s: float = 1800.0,
) -> str | None:
    fut = _pending_answers.get(question_id)
    if fut is None:
        # Process may have restarted; poll DB.
        return await _poll_answer(question_id, timeout_s=timeout_s)
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout_s)
    except asyncio.TimeoutError:
        await _expire_question(question_id)
        return None
    except asyncio.CancelledError:
        await _cancel_question(question_id)
        raise


async def _poll_answer(question_id: str, *, timeout_s: float) -> str | None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        await ensure_artifact_tables()
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            async with db.execute(
                "SELECT status, answer FROM agent_questions WHERE id = ?",
                (question_id,),
            ) as cur:
                row = await cur.fetchone()
        if row and row[0] == "answered":
            return row[1]
        if row and row[0] in {"cancelled", "expired"}:
            return None
        await asyncio.sleep(0.5)
    await _expire_question(question_id)
    return None


async def _expire_question(question_id: str) -> None:
    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE agent_questions SET status = 'expired' "
            "WHERE id = ? AND status = 'pending'",
            (question_id,),
        )
        await db.commit()
    fut = _pending_answers.pop(question_id, None)
    if fut and not fut.done():
        fut.set_result(None)


async def _cancel_question(question_id: str) -> None:
    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE agent_questions SET status = 'cancelled' "
            "WHERE id = ? AND status = 'pending'",
            (question_id,),
        )
        await db.commit()
    fut = _pending_answers.pop(question_id, None)
    if fut and not fut.done():
        fut.cancel()


async def cancel_pending_questions(project_id: str) -> int:
    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id FROM agent_questions WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        ) as cur:
            ids = [row[0] for row in await cur.fetchall()]
        await db.execute(
            "UPDATE agent_questions SET status = 'cancelled' "
            "WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        )
        await db.commit()
    for qid in ids:
        fut = _pending_answers.pop(qid, None)
        if fut and not fut.done():
            fut.cancel()
    return len(ids)


async def list_questions(
    project_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    await ensure_artifact_tables()
    limit = max(1, min(int(limit or 50), 200))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        if status:
            async with db.execute(
                "SELECT id, project_id, request_id, session_number, prompt, question_type, "
                "options, min_value, max_value, step_value, default_value, status, answer, "
                "answered_at, created_at FROM agent_questions WHERE project_id = ? AND status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, status, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT id, project_id, request_id, session_number, prompt, question_type, "
                "options, min_value, max_value, step_value, default_value, status, answer, "
                "answered_at, created_at FROM agent_questions WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ) as cur:
                rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            options = json.loads(r[6] or "[]")
        except json.JSONDecodeError:
            options = []
        out.append({
            "id": r[0],
            "project_id": r[1],
            "request_id": r[2],
            "session_number": r[3],
            "prompt": r[4],
            "question_type": r[5],
            "options": options,
            "min_value": r[7],
            "max_value": r[8],
            "step_value": r[9],
            "default_value": r[10],
            "status": r[11],
            "answer": r[12],
            "answered_at": r[13],
            "created_at": r[14],
        })
    return out


# ---------------------------------------------------------------------------
# Session stop markers
# ---------------------------------------------------------------------------


async def mark_session_stopped(
    project_id: str,
    *,
    reason: str = "stopped",
    source: str = "api",
    session_number: int = 0,
    turso_session_id: str | None = None,
) -> dict[str, Any]:
    await ensure_artifact_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cur = await db.execute(
            "INSERT INTO agent_session_stops "
            "(project_id, session_number, turso_session_id, reason, source, stopped_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                project_id,
                int(session_number or 0),
                turso_session_id,
                (reason or "stopped")[:200],
                source,
                now,
            ),
        )
        await db.commit()
        stop_id = int(cur.lastrowid)
    return {
        "id": stop_id,
        "project_id": project_id,
        "session_number": session_number,
        "turso_session_id": turso_session_id,
        "reason": reason,
        "source": source,
        "stopped_at": now,
    }


async def list_session_stops(project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    await ensure_artifact_tables()
    limit = max(1, min(int(limit or 50), 200))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id, project_id, session_number, turso_session_id, reason, source, stopped_at "
            "FROM agent_session_stops WHERE project_id = ? ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "project_id": r[1],
            "session_number": r[2],
            "turso_session_id": r[3],
            "reason": r[4],
            "source": r[5],
            "stopped_at": r[6],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# MCP addons
# ---------------------------------------------------------------------------

BUILTIN_MCP_ADDONS: list[dict[str, Any]] = [
    {
        "id": "syte",
        "name": "syte",
        "description": "Built-in Syte MCP: preview service control + preview access (fetch/logs/screenshot).",
        "transport": "stdio",
        "command": "syte-mcp",
        "args": [],
        "builtin": True,
    },
    {
        "id": "web_search",
        "name": "web_search",
        "description": "Built-in web search (Tavily/Brave when configured, else DuckDuckGo Instant Answer).",
        "transport": "api",
        "command": "",
        "args": [],
        "builtin": True,
        "tools": [
            {
                "name": "web_search",
                "description": "Search the web for current information, news, docs, or image ideas.",
            }
        ],
    },
]


async def ensure_builtin_mcp_addons(project_id: str) -> None:
    await ensure_artifact_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        for addon in BUILTIN_MCP_ADDONS:
            tools_json = json.dumps(addon.get("tools") or [], ensure_ascii=False)
            await db.execute(
                "INSERT INTO agent_mcp_addons "
                "(id, project_id, name, description, transport, command, args, env, "
                "status, tools_json, connected_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '{}', 'available', ?, NULL, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "description = excluded.description, "
                "tools_json = CASE "
                "  WHEN agent_mcp_addons.status = 'connected' THEN agent_mcp_addons.tools_json "
                "  ELSE excluded.tools_json END, "
                "updated_at = excluded.updated_at",
                (
                    f"{project_id}:{addon['id']}",
                    project_id,
                    addon["name"],
                    addon["description"],
                    addon["transport"],
                    addon["command"],
                    json.dumps(addon.get("args") or []),
                    tools_json,
                    now,
                    now,
                ),
            )
        await db.commit()


async def list_mcp_addons(project_id: str) -> list[dict[str, Any]]:
    await ensure_builtin_mcp_addons(project_id)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT id, project_id, name, description, transport, command, args, env, "
            "status, tools_json, connected_at, created_at, updated_at "
            "FROM agent_mcp_addons WHERE project_id = ? ORDER BY name ASC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            args = json.loads(r[6] or "[]")
            env = json.loads(r[7] or "{}")
            tools = json.loads(r[9] or "[]")
        except json.JSONDecodeError:
            args, env, tools = [], {}, []
        key = (project_id, r[0])
        runtime = _connected_mcp.get(key) or {}
        out.append({
            "id": r[0],
            "project_id": r[1],
            "name": r[2],
            "description": r[3],
            "transport": r[4],
            "command": r[5],
            "args": args,
            "env": env,
            "status": "connected" if runtime.get("connected") else r[8],
            "tools": runtime.get("tools") or tools,
            "connected_at": runtime.get("connected_at") or r[10],
            "created_at": r[11],
            "updated_at": r[12],
            "builtin": str(r[2]) == "syte",
        })
    return out


async def register_mcp_addon(
    project_id: str,
    *,
    name: str,
    description: str = "",
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    transport: str = "stdio",
) -> dict[str, Any]:
    await ensure_artifact_tables()
    now = _now()
    addon_id = f"{project_id}:{_new_id('mcp_')}"
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_mcp_addons "
            "(id, project_id, name, description, transport, command, args, env, "
            "status, tools_json, connected_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'available', '[]', NULL, ?, ?)",
            (
                addon_id,
                project_id,
                (name or "").strip()[:120],
                (description or "")[:1000],
                transport or "stdio",
                command,
                json.dumps(list(args or []), ensure_ascii=False),
                json.dumps(dict(env or {}), ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()
    return {
        "id": addon_id,
        "project_id": project_id,
        "name": name,
        "description": description,
        "transport": transport,
        "command": command,
        "args": list(args or []),
        "env": dict(env or {}),
        "status": "available",
        "created_at": now,
    }


async def update_mcp_addon(
    project_id: str,
    addon_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    transport: str | None = None,
) -> dict[str, Any]:
    """Edit a registered custom MCP addon. Builtin ``syte`` cannot be changed."""
    addons = await list_mcp_addons(project_id)
    addon = next((a for a in addons if a["id"] == addon_id or a["name"] == addon_id), None)
    if not addon:
        return {"ok": False, "error": "not_found", "message": f"MCP addon not found: {addon_id}"}
    if addon.get("builtin") or addon.get("name") == "syte":
        return {
            "ok": False,
            "error": "builtin_readonly",
            "message": "Built-in syte MCP addon cannot be edited",
        }

    next_name = (name if name is not None else addon["name"] or "").strip()[:120]
    next_description = description if description is not None else addon.get("description") or ""
    next_command = command if command is not None else addon.get("command") or ""
    next_args = list(args) if args is not None else list(addon.get("args") or [])
    next_env = dict(env) if env is not None else dict(addon.get("env") or {})
    next_transport = transport if transport is not None else addon.get("transport") or "stdio"
    if not next_name:
        return {"ok": False, "error": "invalid_name", "message": "MCP addon name is required"}
    if not next_command:
        return {"ok": False, "error": "invalid_command", "message": "MCP addon command is required"}

    now = _now()
    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE agent_mcp_addons SET name = ?, description = ?, transport = ?, "
            "command = ?, args = ?, env = ?, updated_at = ? WHERE id = ? AND project_id = ?",
            (
                next_name,
                str(next_description)[:1000],
                next_transport or "stdio",
                next_command,
                json.dumps(next_args, ensure_ascii=False),
                json.dumps(next_env, ensure_ascii=False),
                now,
                addon["id"],
                project_id,
            ),
        )
        await db.commit()

    runtime = _connected_mcp.get((project_id, addon["id"]))
    if runtime and runtime.get("addon"):
        runtime["addon"] = {
            **runtime["addon"],
            "name": next_name,
            "description": next_description,
            "transport": next_transport,
            "command": next_command,
            "args": next_args,
            "env": next_env,
            "updated_at": now,
        }

    return {
        "ok": True,
        "id": addon["id"],
        "project_id": project_id,
        "name": next_name,
        "description": next_description,
        "transport": next_transport,
        "command": next_command,
        "args": next_args,
        "env": next_env,
        "status": addon.get("status") or "available",
        "updated_at": now,
    }


async def discover_mcp_stdio_tools(
    *,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    """Boot an MCP stdio server long enough to run initialize + tools/list (DAV-186)."""
    cmd = (command or "").strip()
    if not cmd:
        return {"ok": False, "error": "invalid_command", "message": "MCP command is empty", "tools": []}

    proc_env = {**dict(__import__("os").environ), **dict(env or {})}
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            *[str(a) for a in (args or [])],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "command_not_found",
            "message": f"MCP command not found: {cmd}",
            "tools": [],
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": "spawn_failed",
            "message": f"Failed to start MCP process: {exc}",
            "tools": [],
        }

    async def _rpc(method: str, params: dict[str, Any] | None = None, *, req_id: int = 1) -> dict[str, Any]:
        assert proc.stdin is not None and proc.stdout is not None
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}},
            ensure_ascii=False,
        ).encode("utf-8")
        # Support both Content-Length framed and newline-delimited MCP servers.
        framed = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        proc.stdin.write(framed)
        await proc.stdin.drain()

        header = await proc.stdout.readline()
        if not header:
            raise RuntimeError("MCP process closed stdout during handshake")
        if header.startswith(b"Content-Length:"):
            length = int(header.decode("ascii").split(":", 1)[1].strip())
            while True:
                sep = await proc.stdout.read(2)
                if sep == b"\r\n":
                    break
                if not sep:
                    raise RuntimeError("MCP framing truncated")
            body = await proc.stdout.readexactly(length)
            return json.loads(body.decode("utf-8"))
        line = header.decode("utf-8", errors="replace").strip()
        while not line:
            nxt = await proc.stdout.readline()
            if not nxt:
                raise RuntimeError("MCP process closed stdout during handshake")
            line = nxt.decode("utf-8", errors="replace").strip()
        return json.loads(line)

    try:
        init = await asyncio.wait_for(
            _rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "syte", "version": "1.0.0"},
                },
                req_id=1,
            ),
            timeout=timeout_s,
        )
        if init.get("error"):
            return {
                "ok": False,
                "error": "initialize_failed",
                "message": str(init["error"]),
                "tools": [],
            }
        # Best-effort initialized notification (ignore response/no-response).
        try:
            assert proc.stdin is not None
            note = json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                ensure_ascii=False,
            ).encode("utf-8")
            proc.stdin.write(f"Content-Length: {len(note)}\r\n\r\n".encode("ascii") + note)
            await proc.stdin.drain()
        except Exception:
            pass
        listed = await asyncio.wait_for(_rpc("tools/list", {}, req_id=2), timeout=timeout_s)
        if listed.get("error"):
            return {
                "ok": False,
                "error": "tools_list_failed",
                "message": str(listed["error"]),
                "tools": [],
            }
        tools_raw = ((listed.get("result") or {}).get("tools") or [])
        tools: list[dict[str, Any]] = []
        for item in tools_raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            tools.append({
                "name": str(item.get("name")),
                "description": str(item.get("description") or "")[:1000],
                "inputSchema": item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {},
            })
        if not tools:
            return {
                "ok": False,
                "error": "no_tools",
                "message": "MCP server returned an empty tools/list",
                "tools": [],
            }
        return {"ok": True, "tools": tools, "serverInfo": (init.get("result") or {}).get("serverInfo")}
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": "boot_timeout",
            "message": f"MCP boot timed out after {timeout_s}s",
            "tools": [],
        }
    except Exception as exc:
        stderr = ""
        try:
            if proc.stderr is not None:
                stderr = (await asyncio.wait_for(proc.stderr.read(2000), timeout=0.5)).decode(
                    "utf-8", errors="replace"
                )
        except Exception:
            pass
        return {
            "ok": False,
            "error": "boot_failed",
            "message": str(exc)[:500] + (f" | stderr: {stderr[:300]}" if stderr else ""),
            "tools": [],
        }
    finally:
        try:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass


async def connect_mcp_addon(project_id: str, addon_id: str) -> dict[str, Any]:
    """Mark an MCP addon connected and expose its tool catalog to the agent.

    Built-in ``syte`` maps to in-process Syte tools (no subprocess required).
    Custom stdio addons are booted briefly for ``initialize`` + ``tools/list``
    validation before being marked connected (DAV-186).
    """
    addons = await list_mcp_addons(project_id)
    addon = next((a for a in addons if a["id"] == addon_id or a["name"] == addon_id), None)
    if not addon:
        return {"ok": False, "error": "not_found", "message": f"MCP addon not found: {addon_id}"}

    tools: list[dict[str, Any]]
    boot: dict[str, Any] | None = None
    if addon["name"] == "syte":
        tools = [
            {"name": "syte_service", "description": "Control preview/service/logs"},
            {"name": "syte_access", "description": "Fetch preview HTML/logs/screenshot"},
        ]
    elif addon["name"] == "web_search":
        tools = [
            {
                "name": "web_search",
                "description": "Search the web for current information, news, docs, or image ideas.",
            }
        ]
    else:
        boot = await discover_mcp_stdio_tools(
            command=str(addon.get("command") or ""),
            args=list(addon.get("args") or []),
            env=dict(addon.get("env") or {}),
        )
        if not boot.get("ok"):
            now = _now()
            await ensure_artifact_tables()
            async with aiosqlite.connect(settings.resolved_db_path) as db:
                await db.execute(
                    "UPDATE agent_mcp_addons SET status = 'error', tools_json = '[]', "
                    "connected_at = NULL, updated_at = ? WHERE id = ?",
                    (now, addon["id"]),
                )
                await db.commit()
            _connected_mcp.pop((project_id, addon["id"]), None)
            return {
                "ok": False,
                "error": boot.get("error") or "boot_failed",
                "message": boot.get("message") or "MCP boot validation failed",
                "id": addon["id"],
                "name": addon["name"],
                "status": "error",
                "tools": [],
            }
        tools = list(boot.get("tools") or [])

    now = _now()
    key = (project_id, addon["id"])
    _connected_mcp[key] = {
        "connected": True,
        "connected_at": now,
        "tools": tools,
        "addon": addon,
    }
    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE agent_mcp_addons SET status = 'connected', tools_json = ?, "
            "connected_at = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tools, ensure_ascii=False), now, now, addon["id"]),
        )
        await db.commit()
    return {
        "ok": True,
        "id": addon["id"],
        "name": addon["name"],
        "status": "connected",
        "tools": tools,
        "connected_at": now,
        "boot_validated": boot is not None,
        "message": (
            f"MCP addon '{addon['name']}' connected. Use call_mcp with tool names "
            f"from the tools list."
        ),
    }


async def disconnect_mcp_addon(project_id: str, addon_id: str) -> dict[str, Any]:
    addons = await list_mcp_addons(project_id)
    addon = next((a for a in addons if a["id"] == addon_id or a["name"] == addon_id), None)
    if not addon:
        return {"ok": False, "error": "not_found", "message": f"MCP addon not found: {addon_id}"}
    _connected_mcp.pop((project_id, addon["id"]), None)
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE agent_mcp_addons SET status = 'available', connected_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (now, addon["id"]),
        )
        await db.commit()
    return {"ok": True, "id": addon["id"], "status": "available"}


# Argument schemas for built-in MCP tools (DAV-146).
_BUILTIN_MCP_SCHEMAS: dict[str, dict[str, Any]] = {
    "syte_service": {
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "min_len": 1, "max_len": 64},
            "command": {"type": "string", "max_len": 4000},
            "cwd": {"type": "string", "max_len": 500},
            "lines": {"type": "integer", "min": 1, "max": 2000},
            "timeout": {"type": "integer", "min": 1, "max": 900},
        },
    },
    "service": {
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "min_len": 1, "max_len": 64},
            "command": {"type": "string", "max_len": 4000},
            "cwd": {"type": "string", "max_len": 500},
            "lines": {"type": "integer", "min": 1, "max": 2000},
            "timeout": {"type": "integer", "min": 1, "max": 900},
        },
    },
    "syte_access": {
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "min_len": 1, "max_len": 64},
            "url": {"type": "string", "max_len": 2000},
            "lines": {"type": "integer", "min": 1, "max": 2000},
        },
    },
    "access": {
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "min_len": 1, "max_len": 64},
            "url": {"type": "string", "max_len": 2000},
            "lines": {"type": "integer", "min": 1, "max": 2000},
        },
    },
    "web_search": {
        "required": [],
        "properties": {
            "query": {"type": "string", "min_len": 1, "max_len": 500},
            "q": {"type": "string", "min_len": 1, "max_len": 500},
            "max_results": {"type": "integer", "min": 1, "max": 10},
        },
        "require_any": [["query", "q"]],
    },
    "search": {
        "required": [],
        "properties": {
            "query": {"type": "string", "min_len": 1, "max_len": 500},
            "q": {"type": "string", "min_len": 1, "max_len": 500},
            "max_results": {"type": "integer", "min": 1, "max": 10},
        },
        "require_any": [["query", "q"]],
    },
}


def validate_builtin_mcp_arguments(
    tool: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate built-in MCP tool args. Returns an error dict, or None if ok."""
    schema = _BUILTIN_MCP_SCHEMAS.get(tool)
    if schema is None:
        return None
    args = dict(arguments or {})
    props = schema.get("properties") or {}
    # Reject unknown keys (strict schema for builtins).
    unknown = [k for k in args if k not in props]
    if unknown:
        return {
            "ok": False,
            "error": "invalid_arguments",
            "message": f"Unknown argument(s) for {tool}: {', '.join(unknown)}",
            "allowed": sorted(props.keys()),
        }
    for key in schema.get("required") or []:
        if key not in args or args.get(key) in (None, ""):
            return {
                "ok": False,
                "error": "invalid_arguments",
                "message": f"Missing required argument '{key}' for {tool}",
            }
    for group in schema.get("require_any") or []:
        if not any(args.get(k) not in (None, "") for k in group):
            return {
                "ok": False,
                "error": "invalid_arguments",
                "message": f"One of {group} is required for {tool}",
            }
    for key, rules in props.items():
        if key not in args or args[key] is None:
            continue
        value = args[key]
        expected = rules.get("type")
        if expected == "string" and not isinstance(value, str):
            return {
                "ok": False,
                "error": "invalid_arguments",
                "message": f"Argument '{key}' must be a string",
            }
        if expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                try:
                    value = int(value)
                    args[key] = value
                except (TypeError, ValueError):
                    return {
                        "ok": False,
                        "error": "invalid_arguments",
                        "message": f"Argument '{key}' must be an integer",
                    }
            if "min" in rules and value < rules["min"]:
                return {
                    "ok": False,
                    "error": "invalid_arguments",
                    "message": f"Argument '{key}' must be >= {rules['min']}",
                }
            if "max" in rules and value > rules["max"]:
                return {
                    "ok": False,
                    "error": "invalid_arguments",
                    "message": f"Argument '{key}' must be <= {rules['max']}",
                }
        if expected == "string" and isinstance(value, str):
            if "min_len" in rules and len(value) < rules["min_len"]:
                return {
                    "ok": False,
                    "error": "invalid_arguments",
                    "message": f"Argument '{key}' is too short",
                }
            if "max_len" in rules and len(value) > rules["max_len"]:
                return {
                    "ok": False,
                    "error": "invalid_arguments",
                    "message": f"Argument '{key}' is too long (max {rules['max_len']})",
                }
            enum_vals = rules.get("enum")
            if enum_vals and value not in enum_vals:
                return {
                    "ok": False,
                    "error": "invalid_arguments",
                    "message": f"Argument '{key}' must be one of {enum_vals}",
                }
    return None


async def call_mcp_addon(
    project_id: str,
    addon_id: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    addons = await list_mcp_addons(project_id)
    addon = next((a for a in addons if a["id"] == addon_id or a["name"] == addon_id), None)
    if not addon:
        return {"ok": False, "error": "not_found", "message": f"MCP addon not found: {addon_id}"}
    key = (project_id, addon["id"])
    if not _connected_mcp.get(key, {}).get("connected") and addon.get("status") != "connected":
        connected = await connect_mcp_addon(project_id, addon["id"])
        if not connected.get("ok"):
            return connected

    args = dict(arguments or {})
    if not isinstance(arguments, dict) and arguments is not None:
        return {
            "ok": False,
            "error": "invalid_arguments",
            "message": "MCP arguments must be a JSON object",
        }

    # Validate built-in tool schemas before dispatch.
    if addon["name"] in {"syte", "web_search"}:
        validation_error = validate_builtin_mcp_arguments(tool, args)
        if validation_error is not None:
            return validation_error

    if addon["name"] == "syte":
        if tool in {"syte_service", "service"}:
            from syte.agent_service import run_service_action

            return await run_service_action(
                project_id,
                str(args.get("action") or "status"),
                command=args.get("command"),
                cwd=str(args.get("cwd") or "app"),
                lines=int(args.get("lines") or 200),
                timeout=int(args.get("timeout") or 300),
                source="mcp",
            )
        if tool in {"syte_access", "access"}:
            from syte.preview_access import run_access_action

            return await run_access_action(
                project_id,
                str(args.get("action") or "status"),
                url=args.get("url"),
                lines=int(args.get("lines") or 200),
            )
        return {
            "ok": False,
            "error": "unknown_tool",
            "message": f"Unknown syte MCP tool: {tool}",
            "available": ["syte_service", "syte_access"],
        }

    if addon["name"] == "web_search":
        from syte.web_search import web_search as do_web_search

        if tool not in {"web_search", "search"}:
            return {
                "ok": False,
                "error": "unknown_tool",
                "message": f"Unknown web_search MCP tool: {tool}",
                "available": ["web_search"],
            }
        return await do_web_search(
            str(args.get("query") or args.get("q") or ""),
            max_results=int(args.get("max_results") or 5),
        )

    return {
        "ok": False,
        "error": "mcp_dispatch_unsupported",
        "message": (
            f"Custom MCP addon '{addon['name']}' is registered/connected, but "
            "out-of-process tool dispatch is not enabled in this runtime. Use "
            "built-in syte MCP or native agent tools."
        ),
        "addon": addon["name"],
        "tool": tool,
        "arguments": args,
    }
