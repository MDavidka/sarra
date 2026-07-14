"""Server-Sent Events (SSE) streaming for logs and the agent activity feed.

This module contains every generator that streams a live view of a project
to HTTP clients. Two families of streams exist:

1. **File-tailing log streams** (:func:`stream_project_logs`,
   :func:`stream_preview_logs`, :func:`stream_agent_logs`) — replay the tail of
   a log file, then poll the file for appended lines and emit each new line as
   an SSE ``data:`` frame. Used for build/deploy/preview/runtime console output.

2. **The agent activity stream** (:func:`stream_agent_activity` and its
   ``tagged`` / ``marked`` / ``formatted`` re-encoders) — a structured,
   Cursor-like chat feed backed by the persisted ``agent_events`` table plus an
   in-process pub/sub fan-out (see :mod:`syte.agent_activity`). This is the
   canonical way for sycord.com and API clients to observe a durable agent turn
   end to end.

SSE framing
-----------
Every frame is UTF-8 text terminated by a blank line (``\\n\\n``). The activity
stream additionally emits standard SSE ``id:`` lines (the monotonic event id)
and a one-time ``retry:`` directive so browser ``EventSource`` clients resume
automatically after a dropped connection by replaying the last-seen id.

Frame shapes emitted by :func:`stream_agent_activity` (default ``sse`` format)::

    retry: 5000

    data: {"type": "session", "text": "Live agent activity stream"}

    id: 42
    data: {"type": "activity", "event": { ... persisted event ... }}

    data: {"type": "ping", "since_id": 42}

    data: {"type": "reconnect", "since_id": 42}

- ``session`` — emitted once when ``live_only`` is set, marking the live tail.
- ``activity`` — one persisted event; carries an ``id:`` line for resume.
- ``ping`` — heartbeat every :data:`ACTIVITY_PING_INTERVAL_SECONDS` to keep
  proxies/load balancers from idling the connection; carries the current
  ``since_id`` so pollers can checkpoint.
- ``reconnect`` — a final hint emitted when the per-connection deadline
  (:data:`ACTIVITY_STREAM_MAX_SECONDS`) is reached, asking the client to
  reopen the stream with ``since_id`` set to the value provided.

Reconnection contract
----------------------
Events are persisted *before* they are broadcast, so a client that records the
highest ``event.id`` it has seen and reconnects with ``since_id=<id>`` (or the
``Last-Event-ID`` header, which the endpoints translate to ``since_id``) is
guaranteed to receive every event it missed with no duplicates and no gaps.
"""

import asyncio
import json
import time
from pathlib import Path

from syte.docker_deploy import _build_log_path, container_name, docker_container_exists
from syte.process_manager import get_logs
from syte.workspace import deploy_log_path, run_cmd, workspace_path

# --- Agent activity stream tunables ---------------------------------------
# Heartbeat cadence. A ``ping`` frame is emitted at least this often so idle
# connections stay open through reverse proxies and browsers.
ACTIVITY_PING_INTERVAL_SECONDS = 10.0
# Hard per-connection lifetime. When reached the generator emits a
# ``reconnect`` hint and returns cleanly so the client reopens with ``since_id``.
ACTIVITY_STREAM_MAX_SECONDS = 3600.0
# Maximum number of historical events replayed on connect before going live.
ACTIVITY_REPLAY_LIMIT = 500
# EventSource auto-reconnect backoff advertised via the SSE ``retry:`` field.
ACTIVITY_RECONNECT_RETRY_MS = 5000


def _sse_payload(chunk: str) -> str | None:
    """Return the JSON body of an SSE frame, ignoring ``id:``/``retry:`` lines.

    The activity re-encoders (:func:`stream_agent_activity_tagged` and
    :func:`stream_agent_activity_formatted`) consume the raw frames produced by
    :func:`stream_agent_activity`. Because those frames may now contain an
    ``id:`` line before the ``data:`` line, callers must not assume the frame
    starts with ``data:``. This helper extracts the ``data:`` payload from a
    single- or multi-line frame and returns ``None`` for frames without one
    (for example the standalone ``retry:`` directive).
    """
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return line[6:].strip()
    return None


async def stream_project_logs(
    project_id: str,
    deploy_type: str = "shell",
    *,
    live_only: bool = False,
):
    """SSE generator — tails build.log, app.log, and docker container output.

    Emits a snapshot of recent lines (unless ``live_only``), then polls the log
    files twice a second for ~37 minutes, forwarding each appended line as a
    ``data:`` frame typed ``deploy``/``build``/``app``/``container``. A ``ping``
    heartbeat is sent every few seconds. When ``deploy_type == "docker"`` the
    live container's ``docker logs`` tail is also polled and de-duplicated.
    """
    ws = workspace_path(project_id)
    deploy_log = deploy_log_path(project_id)
    build_log = _build_log_path(project_id)
    app_log = ws / "app.log"

    if not live_only:
        snapshot = get_logs(project_id, 200, deploy_type)
        if snapshot and snapshot != "No logs yet.":
            for line in snapshot.splitlines():
                yield f"data: {json.dumps({'type': 'log', 'text': line})}\n\n"

    offsets: dict[Path, int] = {}
    for path in (deploy_log, build_log, app_log):
        offsets[path] = path.stat().st_size if path.exists() else 0

    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live deploy session started'})}\n\n"

    docker_tick = 0
    last_docker_lines: set[str] = set()
    for _ in range(4500):
        for path, label in ((deploy_log, "deploy"), (build_log, "build"), (app_log, "app")):
            if not path.exists():
                continue
            size = path.stat().st_size
            pos = offsets.get(path, 0)
            if size > pos:
                with path.open("r", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    offsets[path] = f.tell()
                for line in chunk.splitlines():
                    yield f"data: {json.dumps({'type': label, 'text': line})}\n\n"
            elif size < pos:
                offsets[path] = 0

        if deploy_type == "docker" and docker_tick % 8 == 0:
            if await asyncio.to_thread(docker_container_exists, project_id):
                name = container_name(project_id)
                code, out = await asyncio.to_thread(
                    run_cmd, ["docker", "logs", "--tail", "8", name]
                )
                if code == 0 and out.strip():
                    for line in out.strip().splitlines():
                        if line not in last_docker_lines:
                            last_docker_lines.add(line)
                            if len(last_docker_lines) > 200:
                                last_docker_lines.clear()
                            yield f"data: {json.dumps({'type': 'container', 'text': line})}\n\n"

        docker_tick += 1
        if docker_tick % 10 == 0:
            yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        await asyncio.sleep(0.5)


async def stream_preview_logs(project_id: str, *, live_only: bool = False):
    """SSE generator — tails preview.log for live dev-server output.

    Replays the recent preview log (unless ``live_only``), then polls every
    250ms for up to ~30 minutes, emitting each new line as a ``preview`` frame
    with periodic ``ping`` heartbeats.
    """
    from syte.preview_manager import get_preview_logs, preview_log_path

    log_path = preview_log_path(project_id)

    if not live_only:
        snapshot = get_preview_logs(project_id, 300)
        if snapshot and snapshot != "No preview logs yet.":
            for line in snapshot.splitlines():
                yield f"data: {json.dumps({'type': 'preview', 'text': line})}\n\n"

    offset = log_path.stat().st_size if log_path.exists() else 0

    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live preview session'})}\n\n"

    for _ in range(7200):
        if not log_path.exists():
            await asyncio.sleep(0.25)
            continue
        size = log_path.stat().st_size
        if size > offset:
            with log_path.open("r", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
            for line in chunk.splitlines():
                yield f"data: {json.dumps({'type': 'preview', 'text': line})}\n\n"
        elif size < offset:
            offset = 0
        if _ % 20 == 0:
            yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        await asyncio.sleep(0.25)


async def stream_agent_logs(project_id: str, *, live_only: bool = False):
    """SSE generator — tails the Syte cloud agent runtime log.

    Replays recent runtime output (unless ``live_only``), then polls the agent
    log every 250ms, emitting each new line as an ``agent`` frame with periodic
    ``ping`` heartbeats. This is raw runtime output; for the structured turn
    feed use :func:`stream_agent_activity` instead.
    """
    from syte.cloud_agent import agent_log_path, get_agent_logs

    log_path = agent_log_path(project_id)

    if not live_only:
        snapshot = get_agent_logs(project_id, 300)
        if snapshot and snapshot != "No Syte cloud agent logs yet.":
            for line in snapshot.splitlines():
                yield f"data: {json.dumps({'type': 'agent', 'text': line})}\n\n"

    offset = log_path.stat().st_size if log_path.exists() else 0
    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live Syte cloud agent session'})}\n\n"

    for tick in range(7200):
        if not log_path.exists():
            await asyncio.sleep(0.25)
            continue
        size = log_path.stat().st_size
        if size > offset:
            with log_path.open("r", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
            for line in chunk.splitlines():
                yield f"data: {json.dumps({'type': 'agent', 'text': line})}\n\n"
        elif size < offset:
            offset = 0
        if tick % 20 == 0:
            yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        await asyncio.sleep(0.25)


def _activity_frame(event: dict) -> str:
    """Frame one persisted event as ``id: <id>\\ndata: {...}\\n\\n``.

    Including the SSE ``id:`` line lets browser ``EventSource`` clients resume
    from the last-seen id automatically via the ``Last-Event-ID`` request
    header after a dropped connection.
    """
    event_id = event.get("id")
    body = json.dumps({"type": "activity", "event": event})
    prefix = f"id: {int(event_id)}\n" if event_id else ""
    return f"{prefix}data: {body}\n\n"


async def stream_agent_activity(
    project_id: str,
    *,
    live_only: bool = False,
    since_id: int = 0,
):
    """Stream a durable agent turn as JSON Server-Sent Events.

    This is the canonical, real-time "Cursor-like" chat feed. It first replays
    up to :data:`ACTIVITY_REPLAY_LIMIT` persisted events with ``id > since_id``
    (so a reconnecting client recovers everything it missed), then subscribes to
    the live in-process fan-out and forwards new events as they are recorded.

    Args:
        project_id: Workspace/project UUID whose activity feed to stream.
        live_only: When ``True`` emit an opening ``session`` marker frame; the
            replay phase still runs so ``since_id`` resume keeps working.
        since_id: Resume point — only events with a strictly greater id are
            replayed. Pass the highest ``event.id`` previously observed (or the
            value from the ``Last-Event-ID`` header) to reconnect without gaps
            or duplicates.

    Yields:
        SSE frames (see the module docstring) of ``type`` ``session``,
        ``activity``, ``ping``, and a terminal ``reconnect`` hint. ``activity``
        frames carry an ``id:`` line for automatic browser resume.

    Lifecycle of a single agent turn (correlate by ``event.payload.request_id``)::

        request_started -> processing -> [thinking]
            -> (tool_call_started -> tool_call_finished)*
            -> (request_completed | request_failed)

    The generator always unsubscribes from the fan-out in a ``finally`` block,
    and returns cleanly after :data:`ACTIVITY_STREAM_MAX_SECONDS` so long-lived
    connections are recycled rather than leaked.
    """
    from syte.agent_activity import (
        list_agent_events,
        subscribe_agent_activity,
        unsubscribe_agent_activity,
    )

    # Advertise the reconnect backoff up front so EventSource clients honour it.
    yield f"retry: {ACTIVITY_RECONNECT_RETRY_MS}\n\n"

    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live agent activity stream'})}\n\n"

    # Subscribe *before* the replay read so no event recorded during replay is
    # lost. Duplicates from the overlap window are collapsed by the ``since_id``
    # high-water mark below.
    queue = subscribe_agent_activity(project_id)
    try:
        for event in await list_agent_events(
            project_id, since_id=since_id, limit=ACTIVITY_REPLAY_LIMIT
        ):
            yield _activity_frame(event)
            since_id = max(since_id, int(event.get("id") or 0))

        deadline = time.monotonic() + ACTIVITY_STREAM_MAX_SECONDS
        next_ping = time.monotonic() + ACTIVITY_PING_INTERVAL_SECONDS

        while time.monotonic() < deadline:
            now = time.monotonic()
            timeout = max(0.0, min(next_ping, deadline) - now)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
                if int(event.get("id") or 0) > since_id:
                    since_id = int(event.get("id") or 0)
                    yield _activity_frame(event)
                # Drain any events queued while we were emitting, in id order.
                while not queue.empty():
                    event = queue.get_nowait()
                    if int(event.get("id") or 0) > since_id:
                        since_id = int(event.get("id") or 0)
                        yield _activity_frame(event)
            except asyncio.TimeoutError:
                pass

            now = time.monotonic()
            if now >= next_ping:
                next_ping = now + ACTIVITY_PING_INTERVAL_SECONDS
                yield f"data: {json.dumps({'type': 'ping', 'since_id': since_id})}\n\n"

        # Deadline reached: ask the client to reopen resuming from ``since_id``.
        yield f"data: {json.dumps({'type': 'reconnect', 'since_id': since_id})}\n\n"
    finally:
        unsubscribe_agent_activity(project_id, queue)


def _format_activity_event(event: dict, output_format: str) -> str | None:
    event_type = event.get("event_type") or ""
    detail = event.get("detail") or ""
    payload = event.get("payload") or {}
    request_id = payload.get("request_id") or ""

    if output_format == "jsonl":
        return json.dumps({
            "id": event.get("id"),
            "request_id": request_id,
            "type": event_type,
            "role": event.get("role"),
            "title": event.get("title"),
            "detail": detail,
            "payload": payload,
            "source": event.get("source"),
            "created_at": event.get("created_at"),
        }) + "\n"

    if event_type == "token_delta":
        return payload.get("delta") or detail
    if event_type == "user_message":
        return f"[user] {detail}\n"
    if event_type in ("assistant_message", "message_snapshot", "request_completed"):
        return f"[assistant] {detail}\n"
    if event_type == "request_started":
        return f"[user] {detail}\n"
    if event_type == "thinking":
        return f"[thinking] {detail}\n"
    if event_type in ("file_created", "file_modified", "file_deleted", "file_changed"):
        return f"[file] {detail}\n"
    if event_type in ("command_run", "command_output"):
        return f"[cmd] {detail}\n"
    if event_type in ("tool_call", "tool_call_started", "tool_call_finished", "service_action"):
        return f"[tool] {event.get('title') or event_type}: {detail}\n"
    if event_type == "request_failed":
        return f"[error] {detail}\n"
    if event_type == "processing":
        return "[agent] Working…\n"
    return f"[{event_type}] {detail}\n" if detail else None


# Tool-ish events that represent the *start* of an operation.
_TOOL_START_TYPES = frozenset({
    "tool_call",
    "tool_call_started",
    "command_run",
    "file_created",
    "file_modified",
    "file_deleted",
    "file_read",
    "file_search",
    "service_action",
})
# Tool-ish events that represent the *result* of an operation.
_TOOL_RESULT_TYPES = frozenset({"tool_call_finished", "command_output"})


def _derive_phase(event_type: str, payload: dict) -> str:
    """Return ``started``/``finished`` for tool events even when the producer
    omitted an explicit ``payload.phase`` (the cloud agent does not set it)."""
    phase = str(payload.get("phase") or "")
    if phase:
        return phase
    if event_type in _TOOL_RESULT_TYPES:
        return "finished"
    if event_type in _TOOL_START_TYPES:
        return "started"
    return ""


def _derive_is_error(payload: dict) -> bool | None:
    """Normalise tool result status. Producers report success as ``ok`` (bool);
    surface it as ``is_error`` so clients have a single stable field."""
    if payload.get("is_error") is not None:
        return bool(payload.get("is_error"))
    if payload.get("ok") is not None:
        return not bool(payload.get("ok"))
    return None


def _tagged_activity_event(event: dict) -> str:
    """Render one activity event as a parseable ``[tag]<json>`` record.

    The tagged encoding keeps SSE compatibility (each record is still delivered
    inside a ``data:`` frame) while giving clients a stable, compact vocabulary
    to switch on without inspecting raw event types. The body is a single-line
    UTF-8 JSON object; ``phase`` and ``is_error`` are derived so tool events are
    self-describing regardless of which producer emitted them.
    """
    event_type = str(event.get("event_type") or "event")
    payload = event.get("payload") or {}
    phase = _derive_phase(event_type, payload)

    if event_type == "request_started":
        tag = "start"
    elif event_type == "processing":
        tag = "processing"
    elif event_type == "thinking":
        tag = "think"
    elif event_type == "token_delta":
        tag = "delta"
    elif event_type == "request_completed":
        tag = "done"
    elif event_type == "request_failed":
        tag = "error"
    elif event_type == "user_message":
        tag = "user"
    elif event_type in {"assistant_message", "message_snapshot"}:
        tag = "message"
    elif phase == "finished" or event_type in {
        "tool_call_finished",
        "command_output",
    }:
        tag = "tool:result"
    elif phase == "started" or event_type in {
        "tool_call",
        "tool_call_started",
        "command_run",
        "file_created",
        "file_modified",
        "file_deleted",
        "file_read",
        "file_search",
        "service_action",
    }:
        tag = "tool:start"
    elif event_type == "file_changed":
        tag = "file"
    elif event_type.startswith("agent_") or event_type == "status":
        tag = "status"
    else:
        tag = event_type.replace("_", ":")

    text = str(event.get("detail") or "")
    if event_type == "token_delta":
        text = str(payload.get("delta") or text)
    elif event_type == "request_completed":
        text = str(payload.get("reply") or text)
    elif event_type == "request_failed":
        text = str(payload.get("message") or text)

    body = {
        "id": event.get("id"),
        "request_id": payload.get("request_id") or "",
        "type": event_type,
        "role": event.get("role") or "",
        "title": event.get("title") or "",
        "text": text,
        "created_at": event.get("created_at") or "",
    }
    optional_fields = {
        "phase": phase,
        "tool": payload.get("tool"),
        "tool_call_id": payload.get("tool_call_id"),
        "is_error": _derive_is_error(payload),
        "error": payload.get("error"),
        "execution_status": payload.get("execution_status"),
        "snapshot": payload.get("snapshot"),
    }
    body.update({
        key: value
        for key, value in optional_fields.items()
        if value not in (None, "")
    })
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return f"[{tag}]<{encoded}>"


async def stream_agent_activity_tagged(
    project_id: str,
    *,
    live_only: bool = False,
    since_id: int = 0,
    type_filter: list[str] | None = None,
):
    """Re-encode the activity stream using the compact tagged vocabulary.

    Wraps :func:`stream_agent_activity` and rewrites each frame as
    ``data: [tag]<json>`` where ``tag`` is one of ``start``, ``processing``,
    ``think``, ``tool:start``, ``tool:result``, ``delta``, ``message``,
    ``done``, ``error``, ``status``, ``session``, ``ping``, or ``reconnect``.
    The result is still ``text/event-stream`` so it works with both
    ``EventSource`` and streaming ``fetch`` clients.

    Args:
        project_id: Workspace/project UUID to stream.
        live_only: Forwarded to :func:`stream_agent_activity`.
        since_id: Resume point; see :func:`stream_agent_activity`.
        type_filter: When provided, only activity events whose ``event_type`` is
            in this collection are forwarded (``session``/``ping``/``reconnect``
            control frames are always passed through).
    """
    allowed = set(type_filter) if type_filter else None
    async for chunk in stream_agent_activity(
        project_id,
        live_only=live_only,
        since_id=since_id,
    ):
        raw = _sse_payload(chunk)
        if raw is None:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        message_type = message.get("type")
        if message_type == "activity" and message.get("event"):
            event = message["event"]
            event_type = event.get("event_type") or ""
            if allowed and event_type not in allowed:
                continue
            yield f"data: {_tagged_activity_event(event)}\n\n"
        elif message_type == "session":
            body = json.dumps(
                {"text": message.get("text") or "Live agent activity stream"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"data: [session]<{body}>\n\n"
        elif message_type == "ping":
            body = json.dumps(
                {"since_id": message.get("since_id") or 0},
                separators=(",", ":"),
            )
            yield f"data: [ping]<{body}>\n\n"
        elif message_type == "reconnect":
            body = json.dumps(
                {"since_id": message.get("since_id") or 0},
                separators=(",", ":"),
            )
            yield f"data: [reconnect]<{body}>\n\n"


def _marked_kind_for_event(event: dict) -> str:
    """Map an activity event to a compact marked-stream kind token."""
    payload = event.get("payload") or {}
    kind = str(payload.get("mark_kind") or "").strip()
    if kind:
        return kind
    event_type = str(event.get("event_type") or "")
    title = str(event.get("title") or "").strip().lower()
    if event_type == "thinking" or title == "plan":
        return "plan"
    if event_type in {"tool_call", "tool_call_started", "tool_call_finished", "command_run",
                      "command_output", "file_created", "file_modified", "file_deleted",
                      "file_read", "file_search", "service_action"}:
        return "tool"
    if event_type in {"request_started", "user_message"}:
        return "user"
    if event_type in {"request_completed", "assistant_message", "message_snapshot", "token_delta"}:
        return "message"
    if event_type == "request_failed":
        return "error"
    return "status"


def _marked_status_for_event(event: dict) -> str:
    """Return ``g`` (going) or ``d`` (done) for a marked stream line."""
    payload = event.get("payload") or {}
    status = str(payload.get("mark_status") or "").strip().lower()
    if status in {"g", "d"}:
        return status
    event_type = str(event.get("event_type") or "")
    phase = _derive_phase(event_type, payload)
    if event_type in {"processing", "thinking", "token_delta"} or phase == "started":
        return "g"
    return "d"


def _marked_text_for_event(event: dict) -> str:
    payload = event.get("payload") or {}
    event_type = str(event.get("event_type") or "")
    if event_type == "token_delta":
        return str(payload.get("delta") or event.get("detail") or "")
    if event_type == "request_completed":
        return str(payload.get("reply") or event.get("detail") or "")
    if event_type == "request_failed":
        return str(payload.get("message") or payload.get("error") or event.get("detail") or "")
    tool = payload.get("tool")
    detail = str(event.get("detail") or "")
    if tool and detail:
        return f"{tool} {detail}"
    if tool:
        return str(tool)
    return detail


def format_marked_activity_event(event: dict) -> str | None:
    """Render one activity event as ``S{session}{msg}(d|g)-<kind>text``.

    Example lines::

        S1001(d)-<tool>read_file {"path":"app/page.tsx"}
        S1002(g)-<plan>Inspect the layout first
        S2003(d)-<message>Updated the hero copy

    ``S`` + session number + zero-padded message index identify the line.
    ``(d)`` means done, ``(g)`` means going / in progress. Receivers that already
    rendered older ``[sessionN]`` blocks can load only ``session=last``.
    """
    payload = event.get("payload") or {}
    try:
        session = int(payload.get("session") or 0)
        index = int(payload.get("message_index") or 0)
    except (TypeError, ValueError):
        return None
    if session <= 0 or index <= 0:
        return None
    status = _marked_status_for_event(event)
    kind = _marked_kind_for_event(event)
    text = _marked_text_for_event(event).replace("\n", " ").strip()
    return f"S{session}{index:03d}({status})-<{kind}>{text}"


async def stream_agent_activity_marked(
    project_id: str,
    *,
    live_only: bool = False,
    since_id: int = 0,
    type_filter: list[str] | None = None,
):
    """Re-encode the activity stream using session message marks.

    Wire shape (still ``text/event-stream``)::

        data: [boot]
        data: [session1]
        data: S1001(d)-<user>Add dark mode
        data: S1002(g)-<tool>read_file ...
        data: S1003(d)-<tool>read_file ...
        data: S1004(d)-<plan>1. Inspect 2. Patch
        data: [session2]
        data: S2001(d)-<user>Also fix mobile nav
        data: S2003(g)-<plan>Updating header
        data: [ping]<{"since_id":42}>

    ``[boot]`` is emitted once on connect. ``[sessionN]`` is emitted whenever the
    session number increases (a new user message started agent work).
    """
    allowed = set(type_filter) if type_filter else None
    current_session = 0
    yield "data: [boot]\n\n"
    async for chunk in stream_agent_activity(
        project_id,
        live_only=live_only,
        since_id=since_id,
    ):
        raw = _sse_payload(chunk)
        if raw is None:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        message_type = message.get("type")
        if message_type == "activity" and message.get("event"):
            event = message["event"]
            event_type = event.get("event_type") or ""
            if allowed and event_type not in allowed:
                continue
            payload = event.get("payload") or {}
            try:
                session = int(payload.get("session") or 0)
            except (TypeError, ValueError):
                session = 0
            if session > current_session:
                current_session = session
                yield f"data: [session{session}]\n\n"
            line = format_marked_activity_event(event)
            if line:
                yield f"data: {line}\n\n"
        elif message_type == "ping":
            body = json.dumps(
                {"since_id": message.get("since_id") or 0},
                separators=(",", ":"),
            )
            yield f"data: [ping]<{body}>\n\n"
        elif message_type == "reconnect":
            body = json.dumps(
                {"since_id": message.get("since_id") or 0},
                separators=(",", ":"),
            )
            yield f"data: [reconnect]<{body}>\n\n"
        # The legacy ``session`` live marker is replaced by ``[boot]`` above.


async def stream_agent_activity_formatted(
    project_id: str,
    *,
    live_only: bool = False,
    since_id: int = 0,
    output_format: str = "text",
    type_filter: list[str] | None = None,
):
    """Re-encode the activity stream as plain text or JSON Lines.

    Wraps :func:`stream_agent_activity` and projects each activity event to a
    human- or machine-friendly line. Control frames (``session``, ``ping``,
    ``reconnect``) are dropped.

    Args:
        project_id: Workspace/project UUID to stream.
        live_only: Forwarded to :func:`stream_agent_activity`.
        since_id: Resume point; see :func:`stream_agent_activity`.
        output_format: ``"text"`` yields token deltas and ``[assistant]`` /
            ``[file]`` / ``[cmd]`` prefixed lines suitable for a terminal or
            ``fetch`` reader; ``"jsonl"`` yields one compact JSON object per
            line (``application/x-ndjson``) for CLI/pipeline consumers.
        type_filter: Optional allow-list of ``event_type`` values to forward.

    Note:
        This encoding is a raw byte stream, **not** ``text/event-stream`` — read
        it with ``fetch``/``requests``/``curl``, not ``EventSource``.
    """
    allowed = set(type_filter) if type_filter else None
    async for chunk in stream_agent_activity(
        project_id,
        live_only=live_only,
        since_id=since_id,
    ):
        raw = _sse_payload(chunk)
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if msg.get("type") != "activity" or not msg.get("event"):
            continue
        event = msg["event"]
        event_type = event.get("event_type") or ""
        if allowed and event_type not in allowed:
            continue
        line = _format_activity_event(event, output_format)
        if line:
            yield line
