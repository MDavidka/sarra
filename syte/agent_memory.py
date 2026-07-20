"""Layered agent memory: summaries, active files, workspace index, visual analyses.

Complements the existing SQLite ``agent_sessions`` / ``agent_messages`` tables and
Turso durable sessions. Clients (sycord.com) get resume metadata without
re-scanning the whole workspace every turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from syte.config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session_id TEXT,
    up_to_session_number INTEGER NOT NULL DEFAULT 0,
    summary_text TEXT NOT NULL DEFAULT '',
    key_decisions TEXT NOT NULL DEFAULT '[]',
    design_tokens_snapshot TEXT NOT NULL DEFAULT '{}',
    technical_state TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_summaries_project
ON agent_summaries(project_id, up_to_session_number);

CREATE TABLE IF NOT EXISTS agent_session_meta (
    project_id TEXT NOT NULL,
    session_number INTEGER NOT NULL,
    turso_session_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    model_profile TEXT,
    active_files TEXT NOT NULL DEFAULT '[]',
    last_summary_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, session_number)
);
CREATE INDEX IF NOT EXISTS idx_agent_session_meta_turso
ON agent_session_meta(turso_session_id);

CREATE TABLE IF NOT EXISTS workspace_index (
    project_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    semantic_tags TEXT NOT NULL DEFAULT '[]',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, path)
);
CREATE INDEX IF NOT EXISTS idx_workspace_index_project
ON workspace_index(project_id, updated_at);

CREATE TABLE IF NOT EXISTS visual_analyses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT,
    session_number INTEGER NOT NULL DEFAULT 0,
    screenshot_id TEXT,
    screenshot_url TEXT NOT NULL DEFAULT '',
    viewport TEXT NOT NULL DEFAULT 'desktop',
    description TEXT NOT NULL DEFAULT '',
    layout TEXT NOT NULL DEFAULT '',
    color_scheme TEXT NOT NULL DEFAULT '',
    typography TEXT NOT NULL DEFAULT '',
    components TEXT NOT NULL DEFAULT '',
    accessibility TEXT NOT NULL DEFAULT '',
    performance TEXT NOT NULL DEFAULT '',
    issues TEXT NOT NULL DEFAULT '[]',
    suggestions TEXT NOT NULL DEFAULT '[]',
    mobile_tweaks TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visual_analyses_project
ON visual_analyses(project_id, created_at);

CREATE TABLE IF NOT EXISTS design_profiles (
    project_id TEXT PRIMARY KEY,
    style_key TEXT NOT NULL DEFAULT '',
    theme_key TEXT NOT NULL DEFAULT '',
    design_tokens TEXT NOT NULL DEFAULT '{}',
    design_system_css TEXT NOT NULL DEFAULT '',
    agent_instructions TEXT NOT NULL DEFAULT '',
    reference_blueprint TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'theme',
    updated_at TEXT NOT NULL
);
"""

_SCHEMA_EPOCH = 1
_ensured_paths: dict[str, int] = {}

# Heuristic tag patterns for workspace file indexing.
_TAG_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("layout", re.compile(r"(layout|template)\.(tsx?|jsx?)$", re.I)),
    ("navbar", re.compile(r"(nav|header|navbar|topbar)", re.I)),
    ("hero", re.compile(r"hero", re.I)),
    ("footer", re.compile(r"footer", re.I)),
    ("pricing", re.compile(r"pricing", re.I)),
    ("colors", re.compile(r"(globals?|theme|tokens?|variables)\.(css|scss|ts|js)$", re.I)),
    ("colors", re.compile(r"design-tokens", re.I)),
    ("page", re.compile(r"page\.(tsx?|jsx?)$", re.I)),
    ("component", re.compile(r"components?/", re.I)),
    ("config", re.compile(r"(tailwind|next|tsconfig|package)\.", re.I)),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_memory_tables() -> None:
    path = str(settings.resolved_db_path)
    if _ensured_paths.get(path) == _SCHEMA_EPOCH:
        return
    settings.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=path)
        await db.executescript(SCHEMA)
        await db.commit()
    _ensured_paths[path] = _SCHEMA_EPOCH


# ---------------------------------------------------------------------------
# Session metadata + active files
# ---------------------------------------------------------------------------


async def upsert_session_meta(
    project_id: str,
    session_number: int,
    *,
    turso_session_id: str | None = None,
    status: str = "open",
    model_profile: str | None = None,
    active_files: list[str] | None = None,
    last_summary_id: int | None = None,
) -> dict[str, Any]:
    await ensure_memory_tables()
    now = _now()
    files_json = json.dumps(active_files or [], ensure_ascii=False)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_session_meta "
            "(project_id, session_number, turso_session_id, status, model_profile, "
            "active_files, last_summary_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, session_number) DO UPDATE SET "
            "turso_session_id = COALESCE(excluded.turso_session_id, agent_session_meta.turso_session_id), "
            "status = excluded.status, "
            "model_profile = COALESCE(excluded.model_profile, agent_session_meta.model_profile), "
            "active_files = CASE WHEN excluded.active_files = '[]' "
            "THEN agent_session_meta.active_files ELSE excluded.active_files END, "
            "last_summary_id = COALESCE(excluded.last_summary_id, agent_session_meta.last_summary_id), "
            "updated_at = excluded.updated_at",
            (
                project_id,
                int(session_number),
                turso_session_id,
                status,
                model_profile,
                files_json,
                last_summary_id,
                now,
                now,
            ),
        )
        await db.commit()
    return await get_session_meta(project_id, session_number) or {}


async def touch_active_file(project_id: str, session_number: int, path: str) -> list[str]:
    """Append a file path to the session's active_files list (capped)."""
    await ensure_memory_tables()
    path = path.strip().lstrip("./")
    if not path:
        return []
    meta = await get_session_meta(project_id, session_number)
    files: list[str] = list((meta or {}).get("active_files") or [])
    if path in files:
        files.remove(path)
    files.append(path)
    files = files[-40:]
    await upsert_session_meta(
        project_id,
        session_number,
        active_files=files,
        turso_session_id=(meta or {}).get("turso_session_id"),
        status=(meta or {}).get("status") or "open",
        model_profile=(meta or {}).get("model_profile"),
    )
    return files


async def get_session_meta(project_id: str, session_number: int) -> dict[str, Any] | None:
    await ensure_memory_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agent_session_meta WHERE project_id = ? AND session_number = ?",
            (project_id, int(session_number)),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return _session_meta_row(dict(row))


async def latest_session_meta(project_id: str) -> dict[str, Any] | None:
    await ensure_memory_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agent_session_meta WHERE project_id = ? "
            "ORDER BY session_number DESC LIMIT 1",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return _session_meta_row(dict(row))


def _session_meta_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        files = json.loads(row.get("active_files") or "[]")
    except json.JSONDecodeError:
        files = []
    return {
        "project_id": row["project_id"],
        "session_number": int(row["session_number"]),
        "turso_session_id": row.get("turso_session_id"),
        "status": row.get("status") or "open",
        "model_profile": row.get("model_profile"),
        "active_files": files if isinstance(files, list) else [],
        "last_summary_id": row.get("last_summary_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


async def save_summary(
    project_id: str,
    *,
    summary_text: str,
    up_to_session_number: int,
    key_decisions: list[str] | None = None,
    design_tokens_snapshot: dict[str, Any] | None = None,
    technical_state: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    await ensure_memory_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cur = await db.execute(
            "INSERT INTO agent_summaries "
            "(project_id, session_id, up_to_session_number, summary_text, key_decisions, "
            "design_tokens_snapshot, technical_state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                session_id,
                int(up_to_session_number),
                summary_text[:12000],
                json.dumps(key_decisions or [], ensure_ascii=False),
                json.dumps(design_tokens_snapshot or {}, ensure_ascii=False),
                (technical_state or "")[:4000],
                now,
            ),
        )
        await db.commit()
        summary_id = int(cur.lastrowid)
    return await get_summary(summary_id) or {
        "id": summary_id,
        "project_id": project_id,
        "summary_text": summary_text,
        "up_to_session_number": up_to_session_number,
        "created_at": now,
    }


async def get_summary(summary_id: int) -> dict[str, Any] | None:
    await ensure_memory_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agent_summaries WHERE id = ?", (int(summary_id),)
        ) as cur:
            row = await cur.fetchone()
    return _summary_row(dict(row)) if row else None


async def latest_summary(project_id: str) -> dict[str, Any] | None:
    await ensure_memory_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agent_summaries WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
    return _summary_row(dict(row)) if row else None


def _summary_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        decisions = json.loads(row.get("key_decisions") or "[]")
    except json.JSONDecodeError:
        decisions = []
    try:
        tokens = json.loads(row.get("design_tokens_snapshot") or "{}")
    except json.JSONDecodeError:
        tokens = {}
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "session_id": row.get("session_id"),
        "up_to_session_number": int(row.get("up_to_session_number") or 0),
        "summary_text": row.get("summary_text") or "",
        "key_decisions": decisions if isinstance(decisions, list) else [],
        "design_tokens_snapshot": tokens if isinstance(tokens, dict) else {},
        "technical_state": row.get("technical_state") or "",
        "created_at": row.get("created_at"),
    }


async def maybe_summarize_session(
    project_id: str,
    session_number: int,
    *,
    turso_session_id: str | None = None,
    min_messages: int = 4,
) -> dict[str, Any] | None:
    """Create a local extractive summary when a session has enough messages.

    Uses local text extraction (no extra LLM call) so summarization is always
    available offline. Later turns inject this as long-term memory.
    """
    from syte.cloud_agent_store import conversation_messages

    messages = await conversation_messages(
        project_id,
        limit=120,
        last_session_only=False,
        session_number=session_number,
    )
    user_assistant = [
        m for m in messages if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
    if len(user_assistant) < min_messages:
        return None

    story_bits: list[str] = []
    decisions: list[str] = []
    files: list[str] = []
    for msg in user_assistant:
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            story_bits.append(f"User asked: {content[:240]}")
        else:
            story_bits.append(f"Agent: {content[:240]}")
            for line in content.splitlines():
                low = line.lower()
                if any(k in low for k in ("chose", "decided", "using", "theme", "color", "font")):
                    decisions.append(line.strip()[:200])
        for match in re.findall(
            r"(?:app/|components/|src/)[A-Za-z0-9_./\-]+\.(?:tsx?|jsx?|css|md)",
            content,
        ):
            files.append(match)

    meta = await get_session_meta(project_id, session_number)
    active = list((meta or {}).get("active_files") or []) + files
    # Dedupe preserving order
    seen: set[str] = set()
    unique_files: list[str] = []
    for f in active:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    summary_text = (
        f"Story so far (session {session_number}):\n"
        + "\n".join(story_bits[-12:])
        + "\n\nLast touched files: "
        + (", ".join(unique_files[-12:]) or "none recorded")
    )
    technical_state = (
        f"Key components/routes touched: {', '.join(unique_files[-20:]) or 'n/a'}"
    )
    profile = await get_design_profile(project_id)
    tokens = (profile or {}).get("design_tokens") or {}

    summary = await save_summary(
        project_id,
        summary_text=summary_text,
        up_to_session_number=session_number,
        key_decisions=decisions[-15:],
        design_tokens_snapshot=tokens if isinstance(tokens, dict) else {},
        technical_state=technical_state,
        session_id=turso_session_id,
    )
    await upsert_session_meta(
        project_id,
        session_number,
        turso_session_id=turso_session_id,
        status="completed",
        last_summary_id=summary["id"],
        active_files=unique_files[-40:],
    )
    return summary


def memory_context_block(project_id_summary: dict[str, Any] | None, active_files: list[str]) -> str:
    """Build a compact system-prompt addon from the latest summary + active files."""
    parts: list[str] = ["## Project memory (do not re-scan the whole workspace)"]
    if project_id_summary:
        parts.append(project_id_summary.get("summary_text") or "")
        decisions = project_id_summary.get("key_decisions") or []
        if decisions:
            parts.append("Key decisions:\n- " + "\n- ".join(str(d) for d in decisions[:10]))
        tech = project_id_summary.get("technical_state") or ""
        if tech:
            parts.append(tech)
    if active_files:
        parts.append(
            "Prefer these recently touched files before listing the workspace:\n- "
            + "\n- ".join(active_files[-15:])
        )
    parts.append(
        "Only re-scan the full workspace if git HEAD changed externally or the user "
        "explicitly asks for a full resync."
    )
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Workspace index
# ---------------------------------------------------------------------------


def semantic_tags_for_path(path: str) -> list[str]:
    tags: list[str] = []
    for tag, pattern in _TAG_RULES:
        if pattern.search(path) and tag not in tags:
            tags.append(tag)
    return tags


async def upsert_workspace_file(
    project_id: str,
    path: str,
    *,
    content: str | bytes | None = None,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    await ensure_memory_tables()
    path = path.strip().lstrip("./")
    now = _now()
    if isinstance(content, bytes):
        digest = hashlib.sha256(content).hexdigest()[:24]
        size = size_bytes if size_bytes is not None else len(content)
    elif isinstance(content, str):
        raw = content.encode("utf-8", errors="replace")
        digest = hashlib.sha256(raw).hexdigest()[:24]
        size = size_bytes if size_bytes is not None else len(raw)
    else:
        digest = ""
        size = int(size_bytes or 0)
    tags = semantic_tags_for_path(path)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO workspace_index "
            "(project_id, path, content_hash, last_modified, semantic_tags, size_bytes, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, path) DO UPDATE SET "
            "content_hash = excluded.content_hash, last_modified = excluded.last_modified, "
            "semantic_tags = excluded.semantic_tags, size_bytes = excluded.size_bytes, "
            "updated_at = excluded.updated_at",
            (project_id, path, digest, now, json.dumps(tags), size, now),
        )
        await db.commit()
    return {
        "project_id": project_id,
        "path": path,
        "content_hash": digest,
        "semantic_tags": tags,
        "size_bytes": size,
        "updated_at": now,
    }


async def lookup_workspace_paths(
    project_id: str,
    *,
    tags: list[str] | None = None,
    query: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Find indexed files by semantic tag and/or substring query."""
    await ensure_memory_tables()
    limit = max(1, min(limit, 200))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT path, content_hash, last_modified, semantic_tags, size_bytes, updated_at "
            "FROM workspace_index WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
            (project_id, limit * 5),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    wanted = {t.lower() for t in (tags or []) if t}
    q = (query or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            row_tags = json.loads(row.get("semantic_tags") or "[]")
        except json.JSONDecodeError:
            row_tags = []
        path = str(row.get("path") or "")
        if wanted and not wanted.intersection({str(t).lower() for t in row_tags}):
            if not (q and q in path.lower()):
                continue
        elif q and q not in path.lower() and not any(q in str(t).lower() for t in row_tags):
            if wanted:
                pass  # already matched tags
            else:
                continue
        out.append({
            "path": path,
            "content_hash": row.get("content_hash") or "",
            "last_modified": row.get("last_modified") or "",
            "semantic_tags": row_tags,
            "size_bytes": int(row.get("size_bytes") or 0),
            "updated_at": row.get("updated_at"),
        })
        if len(out) >= limit:
            break
    return out


async def scan_workspace_index(project_id: str, *, max_files: int = 400) -> dict[str, Any]:
    """Walk the project app/ tree and refresh the lightweight index."""
    from syte.workspace import workspace_path

    root = workspace_path(project_id) / "app"
    if not root.exists():
        return {"ok": False, "indexed": 0, "message": "app/ missing"}
    indexed = 0
    skip_dirs = {".git", "node_modules", ".next", "dist", "build", "__pycache__", ".turbo"}
    for path in root.rglob("*"):
        if indexed >= max_files:
            break
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in {
            ".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".md", ".json", ".html",
            ".py", ".toml", ".yml", ".yaml",
        }:
            continue
        try:
            rel = str(path.relative_to(workspace_path(project_id))).replace("\\", "/")
            data = path.read_bytes()[:200_000]
            await upsert_workspace_file(project_id, rel, content=data)
            indexed += 1
        except OSError:
            continue
    return {"ok": True, "indexed": indexed}


def prompt_tags_from_message(message: str) -> list[str]:
    """Map natural-language references to semantic workspace tags."""
    low = (message or "").lower()
    mapping = {
        "navbar": ["navbar"],
        "nav ": ["navbar"],
        "header": ["navbar"],
        "hero": ["hero"],
        "footer": ["footer"],
        "pricing": ["pricing"],
        "color": ["colors"],
        "theme": ["colors"],
        "layout": ["layout"],
        "page": ["page"],
        "component": ["component"],
    }
    tags: list[str] = []
    for needle, vals in mapping.items():
        if needle in low:
            for v in vals:
                if v not in tags:
                    tags.append(v)
    return tags


# ---------------------------------------------------------------------------
# Visual analyses
# ---------------------------------------------------------------------------


async def save_visual_analysis(
    project_id: str,
    *,
    viewport: str = "desktop",
    description: str = "",
    issues: list[str] | None = None,
    suggestions: list[str] | None = None,
    screenshot_id: str | None = None,
    screenshot_url: str = "",
    session_id: str | None = None,
    session_number: int = 0,
    layout: str = "",
    color_scheme: str = "",
    typography: str = "",
    components: str = "",
    accessibility: str = "",
    performance: str = "",
    mobile_tweaks: list[str] | None = None,
    raw: dict[str, Any] | None = None,
    analysis_id: str | None = None,
) -> dict[str, Any]:
    import uuid

    await ensure_memory_tables()
    now = _now()
    aid = analysis_id or uuid.uuid4().hex
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO visual_analyses "
            "(id, project_id, session_id, session_number, screenshot_id, screenshot_url, "
            "viewport, description, layout, color_scheme, typography, components, "
            "accessibility, performance, issues, suggestions, mobile_tweaks, raw_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                aid,
                project_id,
                session_id,
                int(session_number or 0),
                screenshot_id,
                screenshot_url,
                viewport,
                description[:8000],
                layout[:4000],
                color_scheme[:2000],
                typography[:2000],
                components[:4000],
                accessibility[:2000],
                performance[:2000],
                json.dumps(issues or [], ensure_ascii=False),
                json.dumps(suggestions or [], ensure_ascii=False),
                json.dumps(mobile_tweaks or [], ensure_ascii=False),
                json.dumps(raw or {}, ensure_ascii=False),
                now,
            ),
        )
        await db.commit()
    return await get_visual_analysis(aid) or {"id": aid, "project_id": project_id}


async def get_visual_analysis(analysis_id: str) -> dict[str, Any] | None:
    await ensure_memory_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM visual_analyses WHERE id = ?", (analysis_id,)
        ) as cur:
            row = await cur.fetchone()
    return _visual_row(dict(row)) if row else None


async def latest_visual_analysis(project_id: str) -> dict[str, Any] | None:
    await ensure_memory_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM visual_analyses WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
    return _visual_row(dict(row)) if row else None


async def list_visual_analyses(project_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    await ensure_memory_tables()
    limit = max(1, min(limit, 100))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM visual_analyses WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_visual_row(dict(r)) for r in rows]


def _visual_row(row: dict[str, Any]) -> dict[str, Any]:
    def _list(key: str) -> list[Any]:
        try:
            val = json.loads(row.get(key) or "[]")
            return val if isinstance(val, list) else []
        except json.JSONDecodeError:
            return []

    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except json.JSONDecodeError:
        raw = {}
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "session_id": row.get("session_id"),
        "session_number": int(row.get("session_number") or 0),
        "screenshot_id": row.get("screenshot_id"),
        "screenshot_url": row.get("screenshot_url") or "",
        "viewport": row.get("viewport") or "desktop",
        "description": row.get("description") or "",
        "layout": row.get("layout") or "",
        "color_scheme": row.get("color_scheme") or "",
        "typography": row.get("typography") or "",
        "components": row.get("components") or "",
        "accessibility": row.get("accessibility") or "",
        "performance": row.get("performance") or "",
        "issues": _list("issues"),
        "suggestions": _list("suggestions"),
        "mobile_tweaks": _list("mobile_tweaks"),
        "raw": raw if isinstance(raw, dict) else {},
        "created_at": row.get("created_at"),
    }


def visual_feedback_prompt(analysis: dict[str, Any]) -> str:
    """System hint for 'Improve design from screenshot' turns."""
    issues = analysis.get("issues") or []
    suggestions = analysis.get("suggestions") or []
    return (
        "## Visual feedback (primary source of truth for this turn)\n"
        f"Viewport: {analysis.get('viewport')}\n"
        f"Description:\n{analysis.get('description') or ''}\n\n"
        f"Layout: {analysis.get('layout') or 'n/a'}\n"
        f"Color scheme: {analysis.get('color_scheme') or 'n/a'}\n"
        f"Typography: {analysis.get('typography') or 'n/a'}\n"
        f"Components: {analysis.get('components') or 'n/a'}\n"
        f"Accessibility: {analysis.get('accessibility') or 'n/a'}\n\n"
        "Issues:\n- "
        + ("\n- ".join(str(i) for i in issues) if issues else "none listed")
        + "\n\nSuggested improvements:\n- "
        + ("\n- ".join(str(s) for s in suggestions) if suggestions else "none listed")
        + "\n\nGenerate minimal diffs to fix these issues; don't change overall structure "
        "unless necessary."
    )


# ---------------------------------------------------------------------------
# Design profiles
# ---------------------------------------------------------------------------


async def save_design_profile(
    project_id: str,
    *,
    style_key: str = "",
    theme_key: str = "",
    design_tokens: dict[str, Any] | None = None,
    design_system_css: str = "",
    agent_instructions: str = "",
    reference_blueprint: dict[str, Any] | None = None,
    source: str = "theme",
) -> dict[str, Any]:
    await ensure_memory_tables()
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO design_profiles "
            "(project_id, style_key, theme_key, design_tokens, design_system_css, "
            "agent_instructions, reference_blueprint, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "style_key = excluded.style_key, theme_key = excluded.theme_key, "
            "design_tokens = excluded.design_tokens, "
            "design_system_css = excluded.design_system_css, "
            "agent_instructions = excluded.agent_instructions, "
            "reference_blueprint = excluded.reference_blueprint, "
            "source = excluded.source, updated_at = excluded.updated_at",
            (
                project_id,
                style_key,
                theme_key,
                json.dumps(design_tokens or {}, ensure_ascii=False),
                design_system_css[:20000],
                agent_instructions[:8000],
                json.dumps(reference_blueprint or {}, ensure_ascii=False),
                source,
                now,
            ),
        )
        await db.commit()
    return await get_design_profile(project_id) or {"project_id": project_id}


async def get_design_profile(project_id: str) -> dict[str, Any] | None:
    await ensure_memory_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM design_profiles WHERE project_id = ?", (project_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        tokens = json.loads(data.get("design_tokens") or "{}")
    except json.JSONDecodeError:
        tokens = {}
    try:
        blueprint = json.loads(data.get("reference_blueprint") or "{}")
    except json.JSONDecodeError:
        blueprint = {}
    return {
        "project_id": data["project_id"],
        "style_key": data.get("style_key") or "",
        "theme_key": data.get("theme_key") or "",
        "design_tokens": tokens if isinstance(tokens, dict) else {},
        "design_system_css": data.get("design_system_css") or "",
        "agent_instructions": data.get("agent_instructions") or "",
        "reference_blueprint": blueprint if isinstance(blueprint, dict) else {},
        "source": data.get("source") or "theme",
        "updated_at": data.get("updated_at"),
    }


def design_profile_prompt_block(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    tokens = profile.get("design_tokens") or {}
    css = (profile.get("design_system_css") or "").strip()
    instructions = (profile.get("agent_instructions") or "").strip()
    blueprint = profile.get("reference_blueprint") or {}
    parts = [
        "## Project design system (always follow)",
        f"Style profile: {profile.get('style_key') or 'custom'}",
        f"Theme: {profile.get('theme_key') or 'n/a'}",
    ]
    if tokens:
        parts.append("Design tokens (JSON):\n" + json.dumps(tokens, indent=2)[:3000])
    if css:
        parts.append("CSS variables excerpt:\n```css\n" + css[:2500] + "\n```")
    if instructions:
        parts.append("AI Agent Instructions:\n" + instructions[:3000])
    if blueprint:
        parts.append(
            "Reference blueprint (match layout/spacing/typography patterns, not wording):\n"
            + json.dumps(blueprint, indent=2)[:2500]
        )
    parts.append(
        "Use var(--color-primary) / theme CSS variables; follow the spacing scale; "
        "keep layout density consistent with this design system."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Resume / summary payload for APIs
# ---------------------------------------------------------------------------


async def project_memory_snapshot(project_id: str) -> dict[str, Any]:
    """Aggregate resume metadata for GET agent sessions / external summary."""
    from syte.cloud_agent_store import current_session_number, current_turso_session_id

    summary = await latest_summary(project_id)
    meta = await latest_session_meta(project_id)
    visual = await latest_visual_analysis(project_id)
    profile = await get_design_profile(project_id)
    session_number = await current_session_number(project_id)
    turso_id = await current_turso_session_id(project_id)
    active_files = list((meta or {}).get("active_files") or [])
    last_work = ""
    if summary:
        last_work = (summary.get("summary_text") or "").splitlines()[0][:200]
    elif active_files:
        last_work = f"Last touched: {', '.join(active_files[-5:])}"

    resume_session = None
    if turso_id:
        resume_session = {
            "turso_session_id": turso_id,
            "session_number": session_number,
            "session_url": f"/api/agent_session/{turso_id}",
            "status": (meta or {}).get("status") or "open",
        }

    return {
        "project_id": project_id,
        "session_number": session_number,
        "last_work": last_work,
        "active_files": active_files[-20:],
        "latest_summary": (
            {
                "id": summary["id"],
                "up_to_session_number": summary["up_to_session_number"],
                "summary_text": (summary.get("summary_text") or "")[:500],
                "key_decisions": (summary.get("key_decisions") or [])[:8],
            }
            if summary
            else None
        ),
        "latest_session_meta": meta,
        "latest_visual_analysis": (
            {
                "id": visual["id"],
                "viewport": visual.get("viewport"),
                "screenshot_url": visual.get("screenshot_url"),
                "issues": (visual.get("issues") or [])[:5],
                "suggestions": (visual.get("suggestions") or [])[:5],
                "created_at": visual.get("created_at"),
            }
            if visual
            else None
        ),
        "design_profile": (
            {
                "style_key": profile.get("style_key"),
                "theme_key": profile.get("theme_key"),
                "source": profile.get("source"),
                "has_tokens": bool(profile.get("design_tokens")),
                "updated_at": profile.get("updated_at"),
            }
            if profile
            else None
        ),
        "resume_session": resume_session,
        "open_session": resume_session if resume_session and resume_session.get("status") == "open" else None,
    }
