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
    """SSE generator — tails Continue agent logs."""
    from syte.continue_agent import agent_log_path, get_agent_logs

    log_path = agent_log_path(project_id)

    if not live_only:
        snapshot = get_agent_logs(project_id, 300)
        if snapshot and snapshot != "No Continue agent logs yet.":
            for line in snapshot.splitlines():
                yield f"data: {json.dumps({'type': 'agent', 'text': line})}\n\n"

    offset = log_path.stat().st_size if log_path.exists() else 0
    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live Continue agent session'})}\n\n"

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
    poll_state: bool = True,
):
    """SSE generator — replay + live agent activity (Cursor-like chat feed)."""
    from syte.agent_activity import ingest_agent_state, list_agent_events, subscribe_agent_activity, unsubscribe_agent_activity
    from syte.continue_agent import agent_local_url, get_agent_status

    if live_only:
        yield f"data: {json.dumps({'type': 'session', 'text': 'Live agent activity stream'})}\n\n"

    for event in await list_agent_events(project_id, since_id=since_id, limit=500):
        yield f"data: {json.dumps({'type': 'activity', 'event': event})}\n\n"
        since_id = max(since_id, int(event.get("id") or 0))

    queue = subscribe_agent_activity(project_id)
    last_state_poll = 0.0
    last_ping = 0.0
    last_processing_emit = 0.0
    state_poll_interval = 1.0
    ping_interval = 10.0
    max_ticks = 36000  # ~1 hour at 100ms per tick

    try:
        for _ in range(max_ticks):
            now = time.monotonic()

            drained = False
            while not queue.empty():
                event = queue.get_nowait()
                since_id = max(since_id, int(event.get("id") or 0))
                yield f"data: {json.dumps({'type': 'activity', 'event': event})}\n\n"
                drained = True

            if poll_state and not drained and (now - last_state_poll) >= state_poll_interval:
                last_state_poll = now
                status = await get_agent_status(project_id)
                port = status.get("agent_port")
                if status.get("agent_running") and port:
                    try:
                        import httpx

                        async with httpx.AsyncClient(timeout=3.0) as client:
                            response = await client.get(
                                f"{agent_local_url(int(port)).rstrip('/')}/state"
                            )
                        if response.status_code < 400:
                            state = response.json()
                            busy = state.get("isProcessing") or state.get("is_processing")
                            if busy and (now - last_processing_emit) >= 2.0:
                                last_processing_emit = now
                                yield f"data: {json.dumps({'type': 'activity', 'event': {'event_type': 'processing', 'role': 'system', 'title': 'Working', 'detail': 'Agent is processing…'}})}\n\n"
                            await ingest_agent_state(project_id, state, source="agent")
                            while not queue.empty():
                                event = queue.get_nowait()
                                since_id = max(since_id, int(event.get("id") or 0))
                                yield f"data: {json.dumps({'type': 'activity', 'event': event})}\n\n"
                    except Exception:
                        pass

            if (now - last_ping) >= ping_interval:
                last_ping = now
                yield f"data: {json.dumps({'type': 'ping', 'since_id': since_id})}\n\n"

            await asyncio.sleep(0.1)
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


async def stream_agent_activity_formatted(
    project_id: str,
    *,
    live_only: bool = False,
    since_id: int = 0,
    output_format: str = "text",
    type_filter: list[str] | None = None,
    poll_state: bool = True,
):
    """Plain text or JSONL stream derived from agent activity events."""
    allowed = set(type_filter) if type_filter else None
    async for chunk in stream_agent_activity(
        project_id,
        live_only=live_only,
        since_id=since_id,
        poll_state=poll_state,
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
