"""Server-Sent Events log streaming."""

import asyncio
import json
import time
from pathlib import Path

from syte.docker_deploy import _build_log_path, container_name, docker_container_exists
from syte.process_manager import get_logs
from syte.workspace import deploy_log_path, run_cmd, workspace_path


async def stream_project_logs(
    project_id: str,
    deploy_type: str = "shell",
    *,
    live_only: bool = False,
):
    """SSE generator — tails build.log, app.log, and docker container output."""
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
    """SSE generator — tails preview.log for live dev server output."""
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
    """SSE generator — tails OpenHands Agent Server logs."""
    from syte.openhands_agent import agent_log_path, get_agent_logs

    log_path = agent_log_path(project_id)

    if not live_only:
        snapshot = get_agent_logs(project_id, 300)
        if snapshot and snapshot != "No OpenHands agent logs yet.":
            for line in snapshot.splitlines():
                yield f"data: {json.dumps({'type': 'agent', 'text': line})}\n\n"

    offset = log_path.stat().st_size if log_path.exists() else 0
    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live OpenHands agent session'})}\n\n"

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


async def stream_agent_activity(
    project_id: str,
    *,
    live_only: bool = False,
    since_id: int = 0,
):
    """SSE generator — replay + native OpenHands activity (Cursor-like chat feed)."""
    from syte.agent_activity import list_agent_events, subscribe_agent_activity, unsubscribe_agent_activity

    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live agent activity stream'})}\n\n"

    for event in await list_agent_events(project_id, since_id=since_id, limit=500):
        yield f"data: {json.dumps({'type': 'activity', 'event': event})}\n\n"
        since_id = max(since_id, int(event.get("id") or 0))

    queue = subscribe_agent_activity(project_id)
    ping_interval = 10.0
    deadline = time.monotonic() + 3600.0
    next_ping = time.monotonic() + ping_interval

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            timeout = max(0.0, min(next_ping, deadline) - now)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
                since_id = max(since_id, int(event.get("id") or 0))
                yield f"data: {json.dumps({'type': 'activity', 'event': event})}\n\n"
                while not queue.empty():
                    event = queue.get_nowait()
                    since_id = max(since_id, int(event.get("id") or 0))
                    yield f"data: {json.dumps({'type': 'activity', 'event': event})}\n\n"
            except asyncio.TimeoutError:
                pass

            now = time.monotonic()
            if now >= next_ping:
                next_ping = now + ping_interval
                yield f"data: {json.dumps({'type': 'ping', 'since_id': since_id})}\n\n"
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


def _tagged_activity_event(event: dict) -> str:
    """Render one activity event as a parseable ``[tag]<json>`` record."""
    event_type = str(event.get("event_type") or "event")
    payload = event.get("payload") or {}
    phase = str(payload.get("phase") or "")

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
        "is_error": payload.get("is_error"),
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
    """SSE stream using compact ``[start]``, ``[think]``, and tool tags."""
    allowed = set(type_filter) if type_filter else None
    async for chunk in stream_agent_activity(
        project_id,
        live_only=live_only,
        since_id=since_id,
    ):
        if not chunk.startswith("data: "):
            continue
        try:
            message = json.loads(chunk[6:].strip())
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


async def stream_agent_activity_formatted(
    project_id: str,
    *,
    live_only: bool = False,
    since_id: int = 0,
    output_format: str = "text",
    type_filter: list[str] | None = None,
):
    """Plain text or JSONL stream derived from agent activity events."""
    allowed = set(type_filter) if type_filter else None
    async for chunk in stream_agent_activity(
        project_id,
        live_only=live_only,
        since_id=since_id,
    ):
        if not chunk.startswith("data: "):
            continue
        raw = chunk[6:].strip()
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
