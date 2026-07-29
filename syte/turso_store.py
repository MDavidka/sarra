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

If remote Turso is not configured (or temporarily unreachable), session
open/event/close still succeed via the local SQLite fallback in
:mod:`syte.local_session_store`. That guarantees ``agent_change`` can always
return a pollable ``turso_session_id`` for clients (e.g. sycord-pages) that
require one. Remote Turso remains preferred when configured.

In addition to the activity/event trail (``agent_session`` /
``agent_session_event``), this module also durably persists the raw chat
*messages* themselves (user / assistant / tool) in a single shared
``agent_message`` table (see :func:`record_message`, :func:`list_messages`,
:func:`count_messages`). Every project and every session writes into this
same table — messages are never split across per-project or per-session
tables, only filtered by ``session_id`` / ``project_id`` /
``session_number`` columns. This is what backs the "all messages saved"
sync-status check (the green/red "brain" indicator in the GUI): callers
compare the count of locally-appended messages for a session against
``count_messages()`` for that session's Turso rows.
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
        updated_at TEXT NOT NULL,
        ended_at TEXT
    )
    """,
    # Additive migration for databases created before ended_at existed.
    # Duplicate-column errors are ignored by the resilient schema init loop.
    "ALTER TABLE agent_session ADD COLUMN ended_at TEXT",
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
    # Durable, single-table store for every chat message produced by the
    # cloud agent (user / assistant / tool). All projects and all sessions
    # share this one ``agent_message`` table in the configured Turso
    # database — messages are logically separated by ``session_id`` (the
    # durable Turso session UUID, one per user turn) and, secondarily, by
    # ``project_id`` / ``session_number`` for cross-session queries. This is
    # distinct from ``agent_session_event`` (the audit/activity trail):
    # ``agent_message`` mirrors the exact role/content rows written locally
    # in ``syte.cloud_agent_store.agent_messages`` so the full conversation
    # can be reconstructed from Turso alone.
    """
    CREATE TABLE IF NOT EXISTS agent_message (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        session_number INTEGER NOT NULL DEFAULT 0,
        local_message_id INTEGER,
        request_id TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        tool_call_id TEXT,
        tool_calls TEXT,
        reasoning_content TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_message_session "
    "ON agent_message(session_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_message_project "
    "ON agent_message(project_id, session_number, id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_message_local_id "
    "ON agent_message(project_id, local_message_id) "
    "WHERE local_message_id IS NOT NULL",
    # ------------------------------------------------------------------
    # Per-request rollup: the user request, when it arrived, how much
    # activity it produced, and what it cost. One row per agent turn,
    # inserted when the turn starts (status='running') and completed at the
    # end of generation, when token usage / USD cost are finally known.
    # See docs/turso-persistence.md for a worked example of the two writes.
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agent_request (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL,
        session_number INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'api',
        model_profile TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT '',
        thinking_level TEXT NOT NULL DEFAULT '',
        request TEXT NOT NULL DEFAULT '',
        reply TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running',
        error TEXT NOT NULL DEFAULT '',
        activity_count INTEGER NOT NULL DEFAULT 0,
        subagent_count INTEGER NOT NULL DEFAULT 0,
        steps INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        thinking_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL,
        cost_label TEXT NOT NULL DEFAULT '',
        timestamp TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_request_request_id "
    "ON agent_request(project_id, request_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_request_session "
    "ON agent_request(session_id, id)",
    # ------------------------------------------------------------------
    # Delegated subagent tasks: the task text, the declared file scope the
    # subagent is allowed to touch, its start time, and its own cost.
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agent_subagent_task (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL,
        session_number INTEGER NOT NULL DEFAULT 0,
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
        activity_count INTEGER NOT NULL DEFAULT 0,
        steps INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        thinking_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL,
        cost_label TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        ended_at TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_subagent_task_id "
    "ON agent_subagent_task(project_id, task_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_subagent_task_session "
    "ON agent_subagent_task(session_id, id)",
    # ------------------------------------------------------------------
    # Every activity line produced *by a subagent* (tool calls, thinking,
    # file writes). Kept separate from agent_session_event so the GUI's
    # subagent tab can be reconstructed without filtering the main feed.
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agent_subagent_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL,
        parent_request_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        tool TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_subagent_activity_task "
    "ON agent_subagent_activity(task_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_subagent_activity_session "
    "ON agent_subagent_activity(session_id, id)",
)

# One cached client + schema-ready flag per (url, token) pair so settings
# changes (saved from the AI tab) transparently pick up a fresh connection.
_client_cache: dict[tuple[str, str], Any] = {}
_schema_ready: set[tuple[str, str]] = set()
# Last error observed for a given (url, token) pair — surfaced through
# turso_debug_status() so the "brain won't turn green" case can be diagnosed
# from the GUI / browser console instead of only server logs.
_last_error: dict[tuple[str, str], str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_schema_exc(exc: BaseException) -> str:
    """Readable schema-failure text (KeyError('result') otherwise looks like ``'result'``)."""
    if isinstance(exc, KeyError):
        return f"KeyError({exc!s})"
    code = getattr(exc, "code", None)
    explanation = getattr(exc, "explanation", None)
    if code and explanation:
        return f"{type(exc).__name__}: {code}: {explanation}"
    return f"{type(exc).__name__}: {exc}"


def _is_additive_column_migration(stmt: str) -> bool:
    normalized = " ".join(stmt.split()).upper()
    return normalized.startswith("ALTER TABLE") and " ADD COLUMN " in normalized


def _is_benign_schema_failure(stmt: str, exc: BaseException) -> bool:
    """Return True when a schema statement failure is expected / safe to ignore.

    Additive ``ALTER TABLE … ADD COLUMN`` migrations fail once the column already
    exists (fresh ``CREATE TABLE`` already includes it). Local libSQL reports
    ``duplicate column name``; remote Turso/Hrana sometimes raises a bare
    ``KeyError('result')`` instead — which the GUI showed as
    ``… -> 'result'`` and falsely kept the brain red.
    """
    msg = " ".join(
        str(part).lower()
        for part in (
            exc,
            getattr(exc, "code", ""),
            getattr(exc, "explanation", ""),
            getattr(exc, "message", ""),
        )
        if part
    )
    if "duplicate column" in msg or "already exists" in msg:
        return True
    if _is_additive_column_migration(stmt):
        # Idempotent migrations: never poison schema_errors / brain indicator.
        return True
    return False


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
    _last_error.clear()


def normalize_turso_url(url: str) -> str:
    """Rewrite remote ``libsql://`` URLs to ``https://`` for the HTTP client.

    Turso's dashboard still issues ``libsql://…`` connection strings, and the
    Python ``libsql-client`` package historically maps that scheme to a
    WebSocket (``wss://``) Hrana connection. AWS-hosted Turso databases
    (hostnames like ``*.aws-*.turso.io``) reject WebSocket upgrades with
    HTTP 400 ``protocol upgrade not supported (websocket)`` / aiohttp
    ``Invalid response status``. The same host accepts the HTTP Hrana API
    when the URL scheme is ``https://``.

    Local ``file:`` / ``file://`` URLs and already-``http(s):`` / ``ws(s):``
    URLs are left unchanged. Callers may keep pasting the dashboard's
    ``libsql://`` value — Syte normalizes it before opening a client.
    """
    stripped = (url or "").strip()
    if not stripped:
        return stripped
    lower = stripped.lower()
    if lower.startswith("libsql://"):
        return "https://" + stripped[len("libsql://") :]
    return stripped


def _websocket_rejected_hint(error: str) -> str:
    """Return an operator-facing hint when Turso rejects a WebSocket upgrade."""
    lower = (error or "").lower()
    if (
        "invalid response status" in lower
        or "protocol upgrade not supported" in lower
        or ("wss://" in lower and ("400" in lower or "505" in lower))
    ):
        return (
            "Turso rejected the WebSocket (wss) upgrade — AWS-hosted "
            "databases only support HTTPS. Syte rewrites libsql:// → https:// "
            "automatically; if you still see this, clear the cache by "
            "re-saving Settings → AI (Turso URL/token) or confirm the token "
            "matches this database."
        )
    return ""


def _build_client(url: str, token: str):
    import libsql_client

    kwargs: dict[str, Any] = {}
    if token:
        kwargs["auth_token"] = token
    # Always connect via the normalized URL so a dashboard ``libsql://`` paste
    # uses HTTPS Hrana instead of the broken WebSocket path on AWS Turso.
    return libsql_client.create_client(normalize_turso_url(url), **kwargs)


async def get_turso_client() -> Any | None:
    """Return a ready-to-use Turso client, or ``None`` if not configured.

    Schema initialization is deliberately **per-statement resilient**: each
    ``CREATE TABLE`` / ``CREATE INDEX`` in ``SCHEMA_STATEMENTS`` is attempted
    independently. Earlier versions ran the whole list in one loop and
    aborted the *entire* client on the first failing statement — since the
    client is then evicted from ``_client_cache`` and ``_schema_ready`` is
    never populated, every later call re-ran (and re-failed on) the same
    statement, permanently disabling all Turso writes (the message-save
    "brain" indicator would stay red forever) even with fully valid
    credentials, as long as any single statement — e.g. one particular index
    — was rejected by that Turso database (version/engine differences,
    quota, etc.). Now a failing statement is logged and skipped so tables
    that *do* create successfully (most importantly ``agent_message``) are
    still usable, and the specific failure is recorded via
    :func:`turso_debug_status` for diagnosis.
    """
    url, token = await turso_settings()
    if not url:
        return None
    key = (url, token)
    client = _client_cache.get(key)
    if client is None:
        try:
            client = _build_client(url, token)
        except Exception as exc:
            logger.exception("Failed to create Turso client for %s", url)
            _last_error[key] = f"client_creation_failed: {exc}"
            return None
        _client_cache[key] = client
    if key not in _schema_ready:
        failures: list[str] = []
        for stmt in SCHEMA_STATEMENTS:
            try:
                await client.execute(stmt)
            except Exception as exc:
                # Additive ALTER COLUMN migrations are expected to fail once the
                # column already exists (fresh CREATE TABLE already includes it).
                # Remote Turso may also raise KeyError('result') instead of a
                # typed duplicate-column error — treat those as benign too.
                if _is_benign_schema_failure(stmt, exc):
                    logger.debug(
                        "Turso schema migration skipped (already applied): %s (%s)",
                        " ".join(stmt.split())[:80],
                        _format_schema_exc(exc),
                    )
                    continue
                short = stmt.strip().splitlines()[0][:80]
                failures.append(f"{short}... -> {_format_schema_exc(exc)}")
                logger.warning(
                    "Turso schema statement failed (continuing): %s -> %s",
                    short,
                    _format_schema_exc(exc),
                )
        if failures:
            _last_error[key] = "; ".join(failures)
            logger.error(
                "Turso schema init had %d failing statement(s) for %s — "
                "continuing with whatever tables/indexes succeeded: %s",
                len(failures), url, "; ".join(failures),
            )
        else:
            _last_error.pop(key, None)
        # Mark ready even on partial failure — a missing *index* must never
        # block INSERT/SELECT against a table that *did* get created.
        _schema_ready.add(key)
    return client


async def turso_debug_status() -> dict[str, Any]:
    """Diagnostic snapshot for the 'why is the brain red' debugging path.

    Attempts a real round-trip (client build + a trivial ``SELECT 1``) against
    the configured database so connectivity/auth problems are surfaced
    immediately, rather than only showing up as a generic ``all_saved: false``
    later. Never raises. Intended to be exposed through an API route and
    logged to the browser console by the GUI when the brain indicator is red.
    """
    url, token = await turso_settings()
    if not url:
        return {
            "configured": False,
            "database_url": "",
            "effective_url": "",
            "reachable": False,
            "error": "turso_database_url is not set",
            "hint": "",
            "schema_ready": False,
            "schema_errors": "",
        }
    key = (url, token)
    effective = normalize_turso_url(url)
    result: dict[str, Any] = {
        "configured": True,
        "database_url": url,
        "effective_url": effective,
        "auth_token_set": bool(token),
        "reachable": False,
        "error": "",
        "hint": "",
        "schema_ready": key in _schema_ready,
        "schema_errors": _last_error.get(key, ""),
    }
    client = await get_turso_client()
    if client is None:
        err = _last_error.get(key, "get_turso_client() returned None")
        result["error"] = err
        result["hint"] = _websocket_rejected_hint(err)
        return result
    try:
        await client.execute("SELECT 1")
        result["reachable"] = True
    except Exception as exc:
        err = f"round_trip_failed: {exc}"
        result["error"] = err
        result["hint"] = _websocket_rejected_hint(err)
        logger.exception("Turso debug round-trip failed for %s (effective %s)", url, effective)
    result["schema_ready"] = key in _schema_ready
    result["schema_errors"] = _last_error.get(key, "")
    return result


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
    """Create a durable agent session and return its UUID.

    Always opens a local pollable session first so ``agent_change`` can return
    ``turso_session_id`` even when remote Turso is unset. When Turso is
    configured the same UUID is also inserted remotely (best-effort).
    """
    from syte.local_session_store import open_local_session

    session_id = uuid.uuid4().hex
    now = _now()
    local_ok = False
    try:
        await open_local_session(
            session_id,
            project_id,
            session_number=session_number,
            model_profile=model_profile,
        )
        local_ok = True
    except Exception:
        logger.exception("Failed to open local agent session for %s", project_id)

    client = await get_turso_client()
    turso_ok = False
    if client is not None:
        try:
            await client.execute(
                "INSERT INTO agent_session "
                "(id, project_id, session_number, model_profile, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'open', ?, ?)",
                [session_id, project_id, int(session_number or 0), model_profile, now, now],
            )
            turso_ok = True
        except Exception:
            logger.exception(
                "Failed to mirror agent session %s to Turso for %s",
                session_id,
                project_id,
            )
    if not local_ok and not turso_ok:
        return None
    return session_id


async def close_session(session_id: str | None, *, status: str = "completed") -> bool:
    """Mark a durable Turso session terminal and stamp ``ended_at``.

    Returns ``True`` when the UPDATE succeeds (or Turso is not configured /
    ``session_id`` is empty — nothing to close). Returns ``False`` only when
    a write was attempted and failed after retry. Callers should treat a
    failed close as an operational issue: clients poll ``status != 'open'``
    and a stuck ``open`` session looks like endless generating.
    """
    if not session_id:        return True
    from syte.local_session_store import close_local_session

    await close_local_session(session_id, status=status)
    client = await get_turso_client()
    if client is None:
        return True
    now = _now()
    terminal = (status or "completed").strip() or "completed"
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            await client.execute(
                "UPDATE agent_session SET status = ?, updated_at = ?, ended_at = ? "
                "WHERE id = ?",
                [terminal, now, now, session_id],
            )
            return True
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Failed to close Turso agent session %s (attempt %d): %s",
                session_id,
                attempt + 1,
                exc,
            )
    logger.exception(
        "Failed to close Turso agent session %s after retry", session_id, exc_info=last_exc
    )
    return False


async def close_open_sessions_for_project(
    project_id: str,
    *,
    status: str = "cancelled",
    exclude_session_id: str | None = None,
) -> int:
    """Close orphaned ``open`` Turso/local sessions for a project (e.g. after restart)."""
    from syte.local_session_store import list_local_sessions_for_project

    now = _now()
    terminal = (status or "cancelled").strip() or "cancelled"
    closed = 0
    # Close local open sessions first (always available).
    try:
        for row in await list_local_sessions_for_project(project_id, limit=500):
            if row.get("status") != "open":
                continue
            sid = row.get("id")
            if not sid or sid == exclude_session_id:
                continue
            from syte.local_session_store import close_local_session

            await close_local_session(sid, status=terminal)
            closed += 1
    except Exception:
        logger.exception("Failed to close open local sessions for project %s", project_id)

    client = await get_turso_client()
    if client is None:
        return closed
    try:
        if exclude_session_id:
            rs = await client.execute(
                "UPDATE agent_session SET status = ?, updated_at = ?, ended_at = ? "
                "WHERE project_id = ? AND status = 'open' AND id != ?",
                [terminal, now, now, project_id, exclude_session_id],
            )
        else:
            rs = await client.execute(
                "UPDATE agent_session SET status = ?, updated_at = ?, ended_at = ? "
                "WHERE project_id = ? AND status = 'open'",
                [terminal, now, now, project_id],
            )
        rows = getattr(rs, "rows_affected", None)
        if rows is None:
            rows = getattr(rs, "rowsAffected", 0)
        return closed + int(rows or 0)
    except Exception:
        logger.exception("Failed to close open Turso sessions for project %s", project_id)
        return closed


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
    """Append one activity event to a durable session (local + Turso)."""
    if not session_id:
        return None
    from syte.local_session_store import record_local_event

    local_event = None
    try:
        local_event = await record_local_event(
            session_id,
            project_id,
            event_type,
            role=role,
            title=title,
            detail=detail,
            payload=payload,
            source=source,
        )
    except Exception:
        logger.exception("Failed to record local agent session event for %s", session_id)

    client = await get_turso_client()
    if client is None:
        return local_event
    now = _now()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    # Retry once: a transient HTTP failure used to silently drop the event row
    # (the activity trail then had holes even though Turso was healthy).
    result = await _write_with_retry(
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
        what=f"agent_session_event insert {session_id}/{event_type}",
    )
    if result is None:
        return local_event
    # Touching updated_at is bookkeeping only — a failure here must never make a
    # committed event row look unsaved to the caller.
    try:
        await client.execute(
            "UPDATE agent_session SET updated_at = ? WHERE id = ?", [now, session_id]
        )
    except Exception:
        logger.warning(
            "Turso agent_session_event stored, but touching agent_session.updated_at "
            "failed for %s (non-fatal)", session_id,
        )
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
    if client is not None:
        try:
            rs = await client.execute(
                "SELECT id, session_id, project_id, event_type, role, title, detail, "
                "payload, source, created_at FROM agent_session_event "
                "WHERE session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                [session_id, since_id, max(1, min(limit, 5000))],
            )
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
            if events or await _turso_session_exists(client, session_id):
                return events
        except Exception:
            logger.exception("Failed to list Turso agent session events for %s", session_id)

    from syte.local_session_store import list_local_events

    return await list_local_events(session_id, since_id=since_id, limit=limit)


async def _turso_session_exists(client: Any, session_id: str) -> bool:
    try:
        rs = await client.execute(
            "SELECT 1 AS n FROM agent_session WHERE id = ? LIMIT 1", [session_id]
        )
        return bool(rs.rows)
    except Exception:
        return False


async def record_message(
    session_id: str | None,
    project_id: str,
    role: str,
    content: str,
    *,
    session_number: int = 0,
    local_message_id: int | None = None,
    request_id: str = "",
    tool_call_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
) -> dict[str, Any] | None:
    """Durably persist one chat message (user/assistant/tool) to Turso.

    This is the write path behind the "save every message" contract: every
    message appended locally in :mod:`syte.cloud_agent_store` is mirrored
    here, in the *same* ``agent_message`` table regardless of project or
    session — rows are only ever distinguished by ``session_id`` /
    ``project_id`` / ``session_number``, never split across tables. Returns
    ``None`` (never raises) if Turso is not configured or the write fails, so
    callers can flip a per-message "saved" flag without ever blocking or
    failing the turn itself.
    """
    if not session_id:
        return None
    client = await get_turso_client()
    if client is None:
        return None
    now = _now()
    try:
        result = await client.execute(
            "INSERT INTO agent_message "
            "(session_id, project_id, session_number, local_message_id, request_id, "
            "role, content, tool_call_id, tool_calls, reasoning_content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                session_id,
                project_id,
                int(session_number or 0),
                local_message_id,
                request_id,
                role,
                content,
                tool_call_id,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                reasoning_content,
                now,
            ],
        )
    except Exception as exc:
        # ``idx_agent_message_local_id`` is a unique index on
        # ``(project_id, local_message_id)``. A retry (e.g. future
        # reconciliation of rows left ``turso_synced = 0``) that re-sends a
        # message already mirrored successfully must not be reported as a
        # fresh failure — treat a unique-constraint conflict as "already
        # saved" and return the existing row instead of ``None``.
        if local_message_id is not None and "UNIQUE" in str(exc).upper():
            existing = await _find_message_by_local_id(client, project_id, local_message_id)
            if existing is not None:
                return existing
        logger.exception(
            "Failed to record Turso agent message for session %s (local_id=%s)",
            session_id,
            local_message_id,
        )
        return None
    # The message row is the source of truth for "was this saved" — a
    # failure touching agent_session.updated_at (a cosmetic bookkeeping
    # field) must never cause an already-successful INSERT to be reported
    # back to the caller as unsynced. This was a real bug: an exception here
    # used to fall into the same except-block as the INSERT above and return
    # None, permanently marking a message "not saved" (feeding the red brain
    # indicator) even though the row was safely committed to Turso.
    try:
        await client.execute(
            "UPDATE agent_session SET updated_at = ? WHERE id = ?", [now, session_id]
        )
    except Exception:
        logger.warning(
            "Turso agent_message %s inserted, but touching agent_session.updated_at "
            "failed (non-fatal, message is still recorded as saved)",
            local_message_id,
        )
    return {
        "id": result.last_insert_rowid,
        "session_id": session_id,
        "project_id": project_id,
        "session_number": int(session_number or 0),
        "local_message_id": local_message_id,
        "request_id": request_id,
        "role": role,
        "content": content,
        "tool_call_id": tool_call_id,
        "tool_calls": tool_calls or None,
        "reasoning_content": reasoning_content,
        "created_at": now,
    }


async def _find_message_by_local_id(
    client: Any, project_id: str, local_message_id: int
) -> dict[str, Any] | None:
    """Look up an already-mirrored message row by its local join key.

    Used to make :func:`record_message` idempotent under retries: if the
    unique ``(project_id, local_message_id)`` index rejects a re-insert
    because the row was already written on a previous attempt, this returns
    that existing row so the caller still marks it ``turso_synced``.
    """
    try:
        rs = await client.execute(
            "SELECT id, session_id, project_id, session_number, local_message_id, "
            "request_id, role, content, tool_call_id, tool_calls, reasoning_content, "
            "created_at FROM agent_message WHERE project_id = ? AND local_message_id = ? "
            "LIMIT 1",
            [project_id, local_message_id],
        )
    except Exception:
        return None
    if not rs.rows:
        return None
    row = rs.rows[0]
    tool_calls_raw = _row_value(row, "tool_calls")
    try:
        tool_calls = json.loads(tool_calls_raw) if tool_calls_raw else None
    except (json.JSONDecodeError, TypeError):
        tool_calls = None
    return {
        "id": _row_value(row, "id"),
        "session_id": _row_value(row, "session_id"),
        "project_id": _row_value(row, "project_id"),
        "session_number": _row_value(row, "session_number"),
        "local_message_id": _row_value(row, "local_message_id"),
        "request_id": _row_value(row, "request_id"),
        "role": _row_value(row, "role"),
        "content": _row_value(row, "content"),
        "tool_call_id": _row_value(row, "tool_call_id"),
        "tool_calls": tool_calls,
        "reasoning_content": _row_value(row, "reasoning_content"),
        "created_at": _row_value(row, "created_at"),
    }


async def list_messages(session_id: str, *, limit: int = 5000) -> list[dict[str, Any]]:
    """List every message durably stored for one session, oldest first."""
    client = await get_turso_client()
    if client is None:
        return []
    try:
        rs = await client.execute(
            "SELECT id, session_id, project_id, session_number, local_message_id, "
            "request_id, role, content, tool_call_id, tool_calls, reasoning_content, "
            "created_at FROM agent_message WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            [session_id, max(1, min(limit, 20000))],
        )
    except Exception:
        logger.exception("Failed to list Turso agent messages for session %s", session_id)
        return []
    messages: list[dict[str, Any]] = []
    for row in rs.rows:
        tool_calls_raw = _row_value(row, "tool_calls")
        try:
            tool_calls = json.loads(tool_calls_raw) if tool_calls_raw else None
        except (json.JSONDecodeError, TypeError):
            tool_calls = None
        messages.append({
            "id": _row_value(row, "id"),
            "session_id": _row_value(row, "session_id"),
            "project_id": _row_value(row, "project_id"),
            "session_number": _row_value(row, "session_number"),
            "local_message_id": _row_value(row, "local_message_id"),
            "request_id": _row_value(row, "request_id"),
            "role": _row_value(row, "role"),
            "content": _row_value(row, "content"),
            "tool_call_id": _row_value(row, "tool_call_id"),
            "tool_calls": tool_calls,
            "reasoning_content": _row_value(row, "reasoning_content"),
            "created_at": _row_value(row, "created_at"),
        })
    return messages


async def count_messages(session_id: str) -> int:
    """Count durably-stored messages for one session (0 if Turso is unavailable)."""
    client = await get_turso_client()
    if client is None:
        return 0
    try:
        rs = await client.execute(
            "SELECT COUNT(*) AS n FROM agent_message WHERE session_id = ?", [session_id]
        )
    except Exception:
        logger.exception("Failed to count Turso agent messages for session %s", session_id)
        return 0
    if not rs.rows:
        return 0
    value = _row_value(rs.rows[0], "n")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def get_session(session_id: str, *, since_id: int = 0) -> dict[str, Any] | None:
    """Fetch one durable session (metadata + events) by UUID.

    Prefers remote Turso when the session exists there; otherwise serves the
    local SQLite fallback so polls keep working without Turso configured.
    """
    client = await get_turso_client()
    if client is not None:
        try:
            rs = await client.execute(
                "SELECT id, project_id, session_number, model_profile, status, "
                "created_at, updated_at, ended_at FROM agent_session WHERE id = ?",
                [session_id],
            )
            if rs.rows:
                row = rs.rows[0]
                session = {
                    "id": _row_value(row, "id"),
                    "project_id": _row_value(row, "project_id"),
                    "session_number": _row_value(row, "session_number"),
                    "model_profile": _row_value(row, "model_profile"),
                    "status": _row_value(row, "status"),
                    "created_at": _row_value(row, "created_at"),
                    "updated_at": _row_value(row, "updated_at"),
                    "ended_at": _row_value(row, "ended_at"),
                    "storage": "turso",
                }
                session["events"] = await list_events(session_id, since_id=since_id)
                return session
        except Exception:
            logger.exception("Failed to fetch Turso agent session %s", session_id)

    from syte.local_session_store import get_local_session

    return await get_local_session(session_id, since_id=since_id)


async def list_sessions_for_project(
    project_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """List sessions for a project (Turso when available, else local).

    When Turso is configured we prefer its list. If it returns empty (or is
    unreachable), fall back to local sessions so resume/list still works.
    """
    limit = max(1, min(limit, 500))
    client = await get_turso_client()
    if client is not None:
        try:
            rs = await client.execute(
                "SELECT id, session_number, model_profile, status, created_at, updated_at, "
                "ended_at FROM agent_session WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                [project_id, max(1, min(limit, 500))],
            )
            if rs.rows:
                return [
                    {
                        "id": _row_value(row, "id"),
                        "session_number": _row_value(row, "session_number"),
                        "model_profile": _row_value(row, "model_profile"),
                        "status": _row_value(row, "status"),
                        "created_at": _row_value(row, "created_at"),
                        "updated_at": _row_value(row, "updated_at"),
                        "ended_at": _row_value(row, "ended_at"),
                        "storage": "turso",
                    }
                    for row in rs.rows
                ]
        except Exception:
            logger.exception("Failed to list Turso agent sessions for %s", project_id)

    from syte.local_session_store import list_local_sessions_for_project

    return await list_local_sessions_for_project(project_id, limit=limit)


async def latest_session_id_for_project(project_id: str) -> str | None:
    sessions = await list_sessions_for_project(project_id, limit=1)
    return sessions[0]["id"] if sessions else None



# ---------------------------------------------------------------------------
# Detailed per-request / per-subagent persistence
#
# ``agent_session`` + ``agent_session_event`` describe *what happened*;
# the three tables below describe *the work itself* so a backend consumer can
# answer "what was asked, when, how much activity did it cause, what did it
# cost, and which subagents ran" with a single row per request.
#
# Write order for one turn (see docs/turso-persistence.md):
#   1. record_request(...)                      -> status='running', cost NULL
#   2. record_subagent_task(...) per delegation -> status='running', start time
#   3. record_subagent_activity(...) per line   -> subagent tab feed
#   4. finalize_subagent_task(...)              -> status + usage + cost
#   5. finalize_request(...)                    -> status + usage + COST (end)
# ---------------------------------------------------------------------------


async def _write_with_retry(sql: str, params: list[Any], *, what: str) -> Any | None:
    """Execute one INSERT/UPDATE with a single retry; never raise.

    Turso HTTP calls occasionally fail transiently (cold branch, dropped
    keep-alive). A single retry turns most of those into a successful write
    instead of a silently dropped row — the previous behaviour swallowed the
    first failure and lost the data permanently.
    """
    client = await get_turso_client()
    if client is None:
        return None
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            return await client.execute(sql, params)
        except Exception as exc:
            last_exc = exc
            if "UNIQUE" in str(exc).upper():
                # Idempotent replay (same request/task recorded twice) — not a loss.
                logger.debug("%s already recorded (unique conflict)", what)
                return None
            logger.warning("%s failed (attempt %d): %s", what, attempt + 1, exc)
    logger.error("%s failed after retry: %s", what, last_exc)
    return None


def _usage_ints(usage: dict[str, Any] | None) -> tuple[int, int, int, int, int]:
    data = usage or {}

    def _int(key: str) -> int:
        try:
            return int(data.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    total = _int("total_tokens") or (
        _int("input_tokens") + _int("output_tokens") + _int("thinking_tokens")
    )
    return (
        _int("input_tokens"),
        _int("output_tokens"),
        _int("thinking_tokens"),
        total,
        _int("steps"),
    )


def _cost_values(cost: dict[str, Any] | None) -> tuple[float | None, str]:
    data = cost or {}
    raw = data.get("cost_usd")
    try:
        cost_usd = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        cost_usd = None
    return cost_usd, str(data.get("label") or "")[:200]


async def record_request(
    request_id: str,
    project_id: str,
    request: str,
    *,
    session_id: str | None = None,
    session_number: int = 0,
    source: str = "api",
    model_profile: str = "",
    model: str = "",
    provider: str = "",
    thinking_level: Any = "",
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Persist the incoming request + its timestamp at the start of a turn.

    Cost is intentionally left NULL here: it is only known once generation
    finishes, and is written by :func:`finalize_request`.
    """
    if not request_id or not project_id:
        return None
    now = timestamp or _now()
    row = {
        "request_id": request_id,
        "session_id": session_id or "",
        "project_id": project_id,
        "session_number": int(session_number or 0),
        "source": source,
        "model_profile": model_profile or "",
        "model": model or "",
        "provider": provider or "",
        "thinking_level": "" if thinking_level is None else str(thinking_level),
        "request": request or "",
        "status": "running",
        "timestamp": now,
        "started_at": now,
    }
    result = await _write_with_retry(
        "INSERT INTO agent_request "
        "(request_id, session_id, project_id, session_number, source, model_profile, "
        "model, provider, thinking_level, request, status, timestamp, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            row["request_id"], row["session_id"], row["project_id"], row["session_number"],
            row["source"], row["model_profile"], row["model"], row["provider"],
            row["thinking_level"], row["request"][:20000], row["status"],
            row["timestamp"], row["started_at"],
        ],
        what=f"agent_request insert {request_id}",
    )
    if result is None:
        return None
    return {"id": getattr(result, "last_insert_rowid", None), **row}


async def finalize_request(
    request_id: str,
    project_id: str,
    *,
    status: str = "completed",
    reply: str = "",
    error: str = "",
    usage: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    activity_count: int = 0,
    subagent_count: int = 0,
    ended_at: str | None = None,
) -> bool:
    """Close a request row with its final activity volume and USD cost."""
    if not request_id or not project_id:
        return False
    inp, out, think, total, steps = _usage_ints(usage)
    cost_usd, cost_label = _cost_values(cost)
    now = ended_at or _now()
    result = await _write_with_retry(
        "UPDATE agent_request SET status = ?, reply = ?, error = ?, activity_count = ?, "
        "subagent_count = ?, steps = ?, input_tokens = ?, output_tokens = ?, "
        "thinking_tokens = ?, total_tokens = ?, cost_usd = ?, cost_label = ?, ended_at = ? "
        "WHERE project_id = ? AND request_id = ?",
        [
            status or "completed", (reply or "")[:20000], (error or "")[:2000],
            int(activity_count or 0), int(subagent_count or 0), steps,
            inp, out, think, total, cost_usd, cost_label, now,
            project_id, request_id,
        ],
        what=f"agent_request finalize {request_id}",
    )
    return result is not None


async def get_request(request_id: str, project_id: str) -> dict[str, Any] | None:
    client = await get_turso_client()
    if client is None:
        return None
    try:
        rs = await client.execute(
            "SELECT request_id, session_id, project_id, session_number, source, "
            "model_profile, model, provider, thinking_level, request, reply, status, "
            "error, activity_count, subagent_count, steps, input_tokens, output_tokens, "
            "thinking_tokens, total_tokens, cost_usd, cost_label, timestamp, started_at, "
            "ended_at FROM agent_request WHERE project_id = ? AND request_id = ? LIMIT 1",
            [project_id, request_id],
        )
    except Exception:
        logger.exception("Failed to fetch agent_request %s", request_id)
        return None
    if not rs.rows:
        return None
    row = rs.rows[0]
    keys = (
        "request_id", "session_id", "project_id", "session_number", "source",
        "model_profile", "model", "provider", "thinking_level", "request", "reply",
        "status", "error", "activity_count", "subagent_count", "steps", "input_tokens",
        "output_tokens", "thinking_tokens", "total_tokens", "cost_usd", "cost_label",
        "timestamp", "started_at", "ended_at",
    )
    return {key: _row_value(row, key) for key in keys}


async def record_subagent_task(
    task_id: str,
    project_id: str,
    task: str,
    *,
    session_id: str | None = None,
    session_number: int = 0,
    parent_request_id: str = "",
    mode: str = "research",
    profile: str = "",
    model: str = "",
    background: bool = False,
    files: list[str] | None = None,
    started_at: str | None = None,
) -> dict[str, Any] | None:
    """Persist a delegated subagent task: what it is, its file scope, its start time."""
    if not task_id or not project_id:
        return None
    now = started_at or _now()
    result = await _write_with_retry(
        "INSERT INTO agent_subagent_task "
        "(task_id, session_id, project_id, session_number, parent_request_id, task, "
        "mode, profile, model, background, files, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
        [
            task_id, session_id or "", project_id, int(session_number or 0),
            parent_request_id or "", (task or "")[:8000], mode or "research",
            profile or "", model or "", 1 if background else 0,
            json.dumps(list(files or []), ensure_ascii=False), now,
        ],
        what=f"agent_subagent_task insert {task_id}",
    )
    if result is None:
        return None
    return {
        "id": getattr(result, "last_insert_rowid", None),
        "task_id": task_id,
        "project_id": project_id,
        "session_id": session_id or "",
        "parent_request_id": parent_request_id or "",
        "task": task,
        "mode": mode,
        "profile": profile,
        "background": bool(background),
        "files": list(files or []),
        "status": "running",
        "started_at": now,
    }


async def finalize_subagent_task(
    task_id: str,
    project_id: str,
    *,
    status: str = "completed",
    result: str = "",
    error: str = "",
    usage: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    activity_count: int = 0,
    ended_at: str | None = None,
) -> bool:
    """Close a subagent task row with its outcome, usage and cost."""
    if not task_id or not project_id:
        return False
    inp, out, think, total, steps = _usage_ints(usage)
    cost_usd, cost_label = _cost_values(cost)
    now = ended_at or _now()
    written = await _write_with_retry(
        "UPDATE agent_subagent_task SET status = ?, result = ?, error = ?, "
        "activity_count = ?, steps = ?, input_tokens = ?, output_tokens = ?, "
        "thinking_tokens = ?, total_tokens = ?, cost_usd = ?, cost_label = ?, ended_at = ? "
        "WHERE project_id = ? AND task_id = ?",
        [
            status or "completed", (result or "")[:20000], (error or "")[:2000],
            int(activity_count or 0), steps, inp, out, think, total,
            cost_usd, cost_label, now, project_id, task_id,
        ],
        what=f"agent_subagent_task finalize {task_id}",
    )
    return written is not None


async def record_subagent_activity(
    task_id: str,
    project_id: str,
    event_type: str,
    *,
    session_id: str | None = None,
    parent_request_id: str = "",
    tool: str = "",
    title: str = "",
    detail: str = "",
    payload: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> bool:
    """Append one subagent activity line (feeds the GUI's subagent tab)."""
    if not task_id or not project_id or not event_type:
        return False
    written = await _write_with_retry(
        "INSERT INTO agent_subagent_activity "
        "(task_id, session_id, project_id, parent_request_id, event_type, tool, "
        "title, detail, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            task_id, session_id or "", project_id, parent_request_id or "",
            event_type, tool or "", (title or "")[:500], (detail or "")[:4000],
            json.dumps(payload or {}, ensure_ascii=False), created_at or _now(),
        ],
        what=f"agent_subagent_activity insert {task_id}/{event_type}",
    )
    return written is not None


async def list_subagent_tasks(
    *, session_id: str | None = None, project_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    client = await get_turso_client()
    if client is None:
        return []
    keys = (
        "task_id", "session_id", "project_id", "session_number", "parent_request_id",
        "task", "mode", "profile", "model", "background", "files", "status", "result",
        "error", "activity_count", "steps", "input_tokens", "output_tokens",
        "thinking_tokens", "total_tokens", "cost_usd", "cost_label", "started_at",
        "ended_at",
    )
    columns = ", ".join(keys)
    where = "session_id = ?" if session_id else "project_id = ?"
    value = session_id or project_id or ""
    try:
        rs = await client.execute(
            f"SELECT {columns} FROM agent_subagent_task WHERE {where} "
            "ORDER BY id ASC LIMIT ?",
            [value, max(1, min(limit, 1000))],
        )
    except Exception:
        logger.exception("Failed to list subagent tasks for %s", value)
        return []
    rows: list[dict[str, Any]] = []
    for row in rs.rows:
        item = {key: _row_value(row, key) for key in keys}
        try:
            item["files"] = json.loads(item.get("files") or "[]")
        except (json.JSONDecodeError, TypeError):
            item["files"] = []
        item["background"] = bool(item.get("background"))
        rows.append(item)
    return rows


async def list_subagent_activity(
    task_id: str, *, since_id: int = 0, limit: int = 1000
) -> list[dict[str, Any]]:
    client = await get_turso_client()
    if client is None:
        return []
    keys = (
        "id", "task_id", "session_id", "project_id", "parent_request_id",
        "event_type", "tool", "title", "detail", "payload", "created_at",
    )
    try:
        rs = await client.execute(
            f"SELECT {', '.join(keys)} FROM agent_subagent_activity "
            "WHERE task_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            [task_id, since_id, max(1, min(limit, 5000))],
        )
    except Exception:
        logger.exception("Failed to list subagent activity for %s", task_id)
        return []
    rows: list[dict[str, Any]] = []
    for row in rs.rows:
        item = {key: _row_value(row, key) for key in keys}
        try:
            item["payload"] = json.loads(item.get("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["payload"] = {}
        rows.append(item)
    return rows
