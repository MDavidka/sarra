"""Real-time agent activity feed for Cursor-like chat UIs (sycord.com)."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import zlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

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
CREATE INDEX IF NOT EXISTS idx_agent_events_project_created_at ON agent_events(project_id, created_at);
"""

AGENT_EVENTS_MAX_PER_PROJECT = 5000
AGENT_EVENTS_MAX_AGE_DAYS = 14

# High-frequency stream chunks — must not await Turso or prune on the hot path.
HOT_STREAM_EVENT_TYPES = frozenset({"token_delta", "thinking_delta"})
_HOT_PRUNE_EVERY = 250
# Cold tool/status events used to prune on *every* write, which added 1–10s of
# SQLite DELETE work between tool calls. Prune periodically instead.
_COLD_PRUNE_EVERY = 40

# Batch hot deltas before one SSE/local frame so Turso durable writes stay free.
HOT_DELTA_BATCH_MIN_CHARS = 300
HOT_DELTA_BATCH_MAX_CHARS = 500
HOT_DELTA_BATCH_MIN_TOKENS = 16
HOT_DELTA_BATCH_MAX_TOKENS = 32
# Flush idle buffers so slow streams still feel live.
HOT_DELTA_BATCH_FLUSH_MS = 80

# Tiny SSE / replay header keys kept on hot frames (everything else stripped).
_HOT_PAYLOAD_KEYS = frozenset({"delta", "request_id", "session", "agent", "subagent_task_id"})

try:
    import brotli as _brotli  # type: ignore
except Exception:  # pragma: no cover - optional
    _brotli = None

_turso_mirror_tasks: set[asyncio.Task[Any]] = set()
_delta_batchers: dict[str, "StreamDeltaBatcher"] = {}

# Cursor-like event kinds exposed to clients.
ACTIVITY_EVENT_TYPES = frozenset({
    "user_message",
    "assistant_message",
    "thinking",
    "thinking_delta",
    "usage",
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
    "subagent_started",
    "subagent_completed",
    "subagent_failed",
    # File scope a subagent is allowed to touch, published by the main agent
    # *before* it delegates so two agents never edit the same file.
    "subagent_scope",
})

# Chat lanes. Every event carries ``payload.agent``: the GUI renders "main"
# events in the Main tab and "subagent" events in the subagent tab, so the two
# feeds never interleave.
AGENT_LANE_MAIN = "main"
AGENT_LANE_SUBAGENT = "subagent"

_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
_hot_event_counts: dict[str, int] = defaultdict(int)
_cold_event_counts: dict[str, int] = defaultdict(int)
# Non-stream activity lines per request id — persisted as
# ``agent_request.activity_count`` when the turn finishes.
_request_activity_counts: dict[str, int] = {}
_MAX_TRACKED_REQUESTS = 256


def _approx_token_count(text: str) -> int:
    """Cheap token estimate (whitespace splits) for batch thresholds."""
    if not text:
        return 0
    return max(1, len(text.split()))


def _minimal_hot_payload(payload: Any) -> dict[str, Any]:
    """Keep only the tiny header + raw delta text on hot stream events."""
    if not isinstance(payload, dict):
        return {"delta": str(payload or "")}
    out: dict[str, Any] = {}
    for key in _HOT_PAYLOAD_KEYS:
        if key in payload and payload[key] is not None and payload[key] != "":
            out[key] = payload[key]
    if "delta" not in out:
        out["delta"] = str(payload.get("delta") or "")
    return out


def _slim_hot_event(event: dict[str, Any]) -> dict[str, Any]:
    """SSE / replay shape for token_delta / thinking_delta: raw text + tiny header."""
    event_type = str(event.get("event_type") or "")
    payload = _minimal_hot_payload(event.get("payload"))
    delta = str(payload.get("delta") or event.get("detail") or "")
    payload["delta"] = delta
    slim: dict[str, Any] = {
        "id": event.get("id"),
        "event_type": event_type,
        "detail": delta,
        "payload": payload,
    }
    # Optional lane hints when present (subagent streams).
    if payload.get("agent"):
        slim["agent"] = payload["agent"]
    return slim


def negotiate_sse_encoding(accept_encoding: str | None) -> str | None:
    """Pick brotli or gzip when the client advertises support."""
    raw = (accept_encoding or "").lower()
    if not raw:
        return None
    parts = [p.strip().split(";")[0] for p in raw.split(",") if p.strip()]
    if "br" in parts and _brotli is not None:
        return "br"
    if "gzip" in parts:
        return "gzip"
    if "deflate" in parts:
        return "deflate"
    return None


async def compress_sse_frames(
    frames: AsyncIterator[str | bytes],
    *,
    encoding: str | None,
) -> AsyncIterator[bytes]:
    """Wrap an SSE text frame iterator with streaming gzip / brotli / deflate."""
    if not encoding:
        async for frame in frames:
            yield frame.encode("utf-8") if isinstance(frame, str) else frame
        return

    if encoding == "br" and _brotli is not None:
        compressor = _brotli.Compressor(quality=4)
        async for frame in frames:
            chunk = frame.encode("utf-8") if isinstance(frame, str) else frame
            out = compressor.process(chunk)
            if out:
                yield out
        trail = compressor.finish()
        if trail:
            yield trail
        return

    if encoding == "deflate":
        compressor = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)
    else:
        # gzip (default)
        compressor = zlib.compressobj(level=6, wbits=16 + zlib.MAX_WBITS)
    async for frame in frames:
        chunk = frame.encode("utf-8") if isinstance(frame, str) else frame
        out = compressor.compress(chunk)
        if out:
            yield out
        # Flush sync points so EventSource sees frames promptly under compression.
        flushed = compressor.flush(zlib.Z_SYNC_FLUSH)
        if flushed:
            yield flushed
    trail = compressor.flush(zlib.Z_FINISH)
    if trail:
        yield trail


def _track_turso_event_task(task: asyncio.Task[Any]) -> None:
    _turso_mirror_tasks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _turso_mirror_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logging.getLogger(__name__).warning(
                "Background Turso event mirror failed: %s", exc,
            )

    task.add_done_callback(_done)


async def drain_turso_event_mirrors(*, timeout_s: float = 5.0) -> None:
    """Wait briefly for in-flight Turso event mirrors (tests / turn end)."""
    pending = [t for t in list(_turso_mirror_tasks) if not t.done()]
    if not pending:
        return
    await asyncio.wait(pending, timeout=timeout_s)


class StreamDeltaBatcher:
    """Collect 16–32 tokens or 300–500 characters before one hot frame.

    Keeps the SSE hot path cheap and frees the event loop for durable Turso
    writes (non-hot events + messages).
    """

    def __init__(
        self,
        project_id: str,
        event_type: str,
        *,
        request_id: str = "",
        session: int | str | None = None,
        source: str = "agent",
        agent: str = AGENT_LANE_MAIN,
        subagent_task_id: str | None = None,
        min_chars: int = HOT_DELTA_BATCH_MIN_CHARS,
        max_chars: int = HOT_DELTA_BATCH_MAX_CHARS,
        min_tokens: int = HOT_DELTA_BATCH_MIN_TOKENS,
        max_tokens: int = HOT_DELTA_BATCH_MAX_TOKENS,
        flush_ms: int = HOT_DELTA_BATCH_FLUSH_MS,
    ) -> None:
        self.project_id = project_id
        self.event_type = event_type
        self.request_id = request_id
        self.session = session
        self.source = source
        self.agent = agent
        self.subagent_task_id = subagent_task_id
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.flush_ms = flush_ms
        self._buf = ""
        self._tokens = 0
        self._flush_handle: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()

    def _batch_key(self) -> str:
        return (
            f"{self.project_id}|{self.event_type}|{self.request_id}|"
            f"{self.subagent_task_id or ''}"
        )

    def _should_flush(self) -> bool:
        chars = len(self._buf)
        if chars >= self.max_chars or self._tokens >= self.max_tokens:
            return True
        if chars >= self.min_chars and self._tokens >= self.min_tokens:
            return True
        return False

    def _schedule_idle_flush(self) -> None:
        loop = asyncio.get_running_loop()
        if self._flush_handle is not None:
            self._flush_handle.cancel()
        self._flush_handle = loop.call_later(
            self.flush_ms / 1000.0,
            lambda: asyncio.create_task(self.flush()),
        )

    async def push(self, delta: str) -> dict[str, Any] | None:
        if not delta:
            return None
        async with self._lock:
            self._buf += delta
            self._tokens += _approx_token_count(delta)
            if self._should_flush():
                return await self._flush_locked()
            self._schedule_idle_flush()
            return None

    async def flush(self) -> dict[str, Any] | None:
        async with self._lock:
            return await self._flush_locked()

    async def _flush_locked(self) -> dict[str, Any] | None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        text = self._buf
        if not text:
            return None
        self._buf = ""
        self._tokens = 0
        payload: dict[str, Any] = {"delta": text}
        if self.request_id:
            payload["request_id"] = self.request_id
        if self.session is not None and self.session != "":
            payload["session"] = self.session
        title = "Stream" if self.event_type == "token_delta" else "Thinking"
        return await record_agent_event(
            self.project_id,
            self.event_type,
            role="assistant",
            title=title,
            detail=text[:2000],
            payload=payload,
            source=self.source,
            agent=self.agent,
            subagent_task_id=self.subagent_task_id,
            # Hot path: never pass turso_session_id (skipped anyway).
        )


def get_delta_batcher(
    project_id: str,
    event_type: str,
    *,
    request_id: str = "",
    session: int | str | None = None,
    source: str = "agent",
    agent: str = AGENT_LANE_MAIN,
    subagent_task_id: str | None = None,
) -> StreamDeltaBatcher:
    key = f"{project_id}|{event_type}|{request_id}|{subagent_task_id or ''}"
    batcher = _delta_batchers.get(key)
    if batcher is None:
        batcher = StreamDeltaBatcher(
            project_id,
            event_type,
            request_id=request_id,
            session=session,
            source=source,
            agent=agent,
            subagent_task_id=subagent_task_id,
        )
        _delta_batchers[key] = batcher
    else:
        batcher.session = session if session is not None else batcher.session
        batcher.source = source or batcher.source
    return batcher


async def flush_delta_batchers(
    project_id: str,
    *,
    request_id: str | None = None,
) -> None:
    """Flush pending hot-delta buffers for a project (end of turn / cancel)."""
    keys = [
        k for k, b in list(_delta_batchers.items())
        if b.project_id == project_id
        and (request_id is None or b.request_id == request_id)
    ]
    for key in keys:
        batcher = _delta_batchers.pop(key, None)
        if batcher is not None:
            await batcher.flush()


def _bump_request_activity(payload: Any, event_type: str) -> None:
    if event_type in HOT_STREAM_EVENT_TYPES or not isinstance(payload, dict):
        return
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        return
    _request_activity_counts[request_id] = _request_activity_counts.get(request_id, 0) + 1
    overflow = len(_request_activity_counts) - _MAX_TRACKED_REQUESTS
    if overflow > 0:
        for stale in list(_request_activity_counts.keys())[:overflow]:
            _request_activity_counts.pop(stale, None)


def activity_count_for_request(request_id: str) -> int:
    """Return how many non-stream activity events a request produced."""
    return int(_request_activity_counts.get(str(request_id or ""), 0))


def clear_activity_count_for_request(request_id: str) -> None:
    _request_activity_counts.pop(str(request_id or ""), None)


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


async def _prune_agent_events(project_id: str) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=AGENT_EVENTS_MAX_AGE_DAYS)).isoformat()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        await db.execute(
            "DELETE FROM agent_events WHERE project_id = ? AND created_at < ?",
            (project_id, cutoff),
        )
        await db.execute(
            "DELETE FROM agent_events WHERE project_id = ? AND id NOT IN ("
            "SELECT id FROM agent_events WHERE project_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?"
            ")",
            (project_id, project_id, AGENT_EVENTS_MAX_PER_PROJECT),
        )
        await db.commit()


def _payload_session_number(payload: Any) -> int | None:
    """Return a numeric session mark from an event payload, or None if absent/invalid."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("session")
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _sanitize_event_payload(payload: Any) -> Any:
    """Drop oversized inline screenshot blobs before returning events to clients.

    Historical rows may still contain ``chat_image_base64`` (~90KB/shot). Serving
    those on chat-open (history + SSE backlog) can freeze or crash the browser tab.
    Clients already fall back to ``thumb_url`` / ``image_url``.
    """
    if not isinstance(payload, dict):
        return payload
    shots = payload.get("screenshots")
    if not isinstance(shots, list):
        return payload
    cleaned_shots = []
    changed = False
    for shot in shots:
        if isinstance(shot, dict) and "chat_image_base64" in shot:
            cleaned_shots.append({k: v for k, v in shot.items() if k != "chat_image_base64"})
            changed = True
        else:
            cleaned_shots.append(shot)
    if not changed:
        return payload
    return {**payload, "screenshots": cleaned_shots}


def _event_row_to_dict(row: tuple) -> dict[str, Any]:
    payload_raw = row[6] or "{}"
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        payload = {"raw": payload_raw}
    event_type = row[2]
    # Hot stream rows: strip verbose metadata so history/SSE backlog stays tiny.
    if event_type in HOT_STREAM_EVENT_TYPES:
        slim_payload = _minimal_hot_payload(payload)
        delta = str(slim_payload.get("delta") or row[5] or "")
        slim_payload["delta"] = delta
        return {
            "id": row[0],
            "event_type": event_type,
            "detail": delta,
            "payload": slim_payload,
        }
    return {
        "id": row[0],
        "project_id": row[1],
        "event_type": event_type,
        "role": row[3],
        "title": row[4],
        "detail": row[5],
        "payload": _sanitize_event_payload(payload),
        "source": row[7],
        "created_at": row[8],
    }


def _notify_subscribers(project_id: str, event: dict[str, Any]) -> None:
    """Fan out to SSE queues without awaiting I/O (low-latency token path)."""
    for queue in list(_subscribers.get(project_id, [])):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass


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
    agent: str = AGENT_LANE_MAIN,
    subagent_task_id: str | None = None,
) -> dict[str, Any]:
    """Persist an activity event locally and optionally mirror it to Turso.

    Local persistence (the ``agent_events`` SQLite table below) remains the
    fast, always-available store used by internal status/debug endpoints and
    the live SSE channel. When ``turso_session_id`` is supplied, non-stream
    events are also written to Turso so clients can fetch the whole session by
    UUID. High-frequency ``token_delta`` / ``thinking_delta`` chunks skip Turso
    and skip per-event prune so streaming cadence is not gated on remote I/O.

    Hot frames are stored and fan-out as *minimal deltas* (raw text + tiny
    header). Durable Turso mirrors for cold events run in the background so a
    slow remote DB never blocks the turn or SSE.
    """
    await ensure_agent_events_table()
    is_hot = event_type in HOT_STREAM_EVENT_TYPES
    clean_payload = _sanitize_event_payload(payload or {})
    if not isinstance(clean_payload, dict):
        clean_payload = payload or {}
    # Stamp the chat lane so clients can split main vs subagent feeds. An
    # explicit payload["agent"] (already set by a caller) always wins.
    lane = AGENT_LANE_SUBAGENT if subagent_task_id and agent == AGENT_LANE_MAIN else agent
    if isinstance(clean_payload, dict):
        clean_payload = {
            "agent": clean_payload.get("agent") or lane or AGENT_LANE_MAIN,
            **clean_payload,
        }
        if subagent_task_id and not clean_payload.get("subagent_task_id"):
            clean_payload["subagent_task_id"] = subagent_task_id

    if is_hot:
        # Minimal-delta hot path: only raw text + tiny correlation header.
        clean_payload = _minimal_hot_payload(clean_payload)
        detail = str(clean_payload.get("delta") or detail or "")[:2000]
        clean_payload["delta"] = detail
        title = title[:80] if title else ("Stream" if event_type == "token_delta" else "Thinking")
        role = "assistant"
        # Drop verbose source labels from hot rows.
        source = source or "agent"

    _bump_request_activity(clean_payload, event_type)
    payload_json = json.dumps(clean_payload, ensure_ascii=False)
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

    if is_hot:
        event = {
            "id": event_id,
            "project_id": project_id,
            "event_type": event_type,
            "role": role,
            "title": title,
            "detail": detail,
            "payload": clean_payload,
            "source": source,
            "created_at": now,
        }
        # Fan out the slim wire shape so subscribers never see verbose metadata.
        _notify_subscribers(project_id, _slim_hot_event(event))
    else:
        event = {
            "id": event_id,
            "project_id": project_id,
            "event_type": event_type,
            "role": role,
            "title": title,
            "detail": detail,
            "payload": clean_payload,
            "source": source,
            "created_at": now,
        }
        _notify_subscribers(project_id, event)

    # Prune periodically — never on every cold tool write (that added multi-second
    # SQLite DELETE latency between tools).
    should_prune = False
    if is_hot:
        _hot_event_counts[project_id] += 1
        if _hot_event_counts[project_id] % _HOT_PRUNE_EVERY == 0:
            should_prune = True
    else:
        _cold_event_counts[project_id] += 1
        if _cold_event_counts[project_id] % _COLD_PRUNE_EVERY == 0:
            should_prune = True
    if should_prune:
        try:
            await _prune_agent_events(project_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to prune agent events for project %s", project_id
            )

    # Stream chunks are ephemeral for Turso; final assistant/tool events carry content.
    # Cold events mirror in the background so Turso latency never stalls the turn.
    if turso_session_id and not is_hot:
        async def _mirror() -> None:
            from syte.turso_store import record_event as record_turso_event

            try:
                await record_turso_event(
                    turso_session_id,
                    project_id,
                    event_type,
                    role=role,
                    title=title,
                    detail=detail,
                    payload=clean_payload,
                    source=source,
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to mirror agent event to Turso session %s", turso_session_id
                )

        try:
            _track_turso_event_task(asyncio.create_task(_mirror()))
        except RuntimeError:
            # No running loop (sync tests) — fall back to awaited write.
            await _mirror()
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
        filtered: list[dict[str, Any]] = []
        for event in events:
            session_num = _payload_session_number(event.get("payload"))
            if session_num is None:
                continue
            if session_num == session_filter:
                filtered.append(event)
            if len(filtered) >= limit:
                break
        events = filtered
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


def _sse_frame_for_event(event: dict[str, Any]) -> str:
    """Serialize one activity event as an SSE frame (slim for hot deltas)."""
    event_name = str(event.get("event_type") or "message")
    if event_name in HOT_STREAM_EVENT_TYPES:
        wire = _slim_hot_event(event)
    else:
        wire = event
    return (
        f"id: {wire.get('id')}\n"
        f"event: {event_name}\n"
        f"data: {json.dumps(wire, ensure_ascii=False)}\n\n"
    )


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

    Hot ``token_delta`` / ``thinking_delta`` frames carry only raw text + a tiny
    header (id, event_type, payload.delta / request_id / session).
    """
    # Replay recent backlog first so reconnects don't miss early tokens.
    # Incremental reconnects (since_id > 0) only need a small delta window.
    backlog_limit = 100 if int(since_id or 0) > 0 else 200
    backlog = await list_agent_events(
        project_id, since_id=since_id, limit=backlog_limit, session=session or None,
    )
    last_id = since_id
    for event in backlog:
        last_id = max(last_id, int(event.get("id") or 0))
        yield _sse_frame_for_event(event)

    queue = subscribe_agent_activity(project_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Surface unexpected stream failures so clients do not hang on a
                # silent dead connection (DAV-180).
                yield (
                    "event: error\n"
                    f"data: {json.dumps({'event_type': 'error', 'error': 'stream_failed', 'message': str(exc)[:500]}, ensure_ascii=False)}\n\n"
                )
                break
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
            yield _sse_frame_for_event(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield (
            "event: error\n"
            f"data: {json.dumps({'event_type': 'error', 'error': 'stream_failed', 'message': str(exc)[:500]}, ensure_ascii=False)}\n\n"
        )
    finally:
        unsubscribe_agent_activity(project_id, queue)


def sse_stream_response(
    request: Any,
    frame_iter: AsyncIterator[str],
):
    """Build a StreamingResponse with optional gzip/brotli on the SSE body."""
    from fastapi.responses import StreamingResponse

    accept = ""
    try:
        accept = request.headers.get("accept-encoding") or ""
    except Exception:
        accept = ""
    encoding = negotiate_sse_encoding(accept)
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"

    async def _gen():
        async for chunk in compress_sse_frames(frame_iter, encoding=encoding):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers=headers,
    )


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
