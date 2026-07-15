"""Server-Sent Events (SSE) streaming for build/deploy/preview/runtime logs.

This module contains the file-tailing log stream generators
(:func:`stream_project_logs`, :func:`stream_preview_logs`,
:func:`stream_agent_logs`) — they replay the tail of a log file, then poll the
file for appended lines and emit each new line as an SSE ``data:`` frame. Used
for build/deploy/preview/runtime console output.

The structured, Cursor-like agent *activity* feed (durable per-turn events:
requests, thinking, tool calls, replies) is **not** a live stream any more.
Every agent turn now writes its activity to a durable Turso session
(:mod:`syte.turso_store`) keyed by a UUID as it happens, and clients fetch
that session by UUID from the Turso access routes
(``GET /api/agent_session/{session_id}`` and its ``/api/internal`` and
``/sycord/api`` mirrors) instead of opening a long-lived SSE connection.
Asking the agent something is unchanged — still a normal request/response API
call (``agent_communicate`` / ``agent_change`` / the GUI chat endpoint).
"""

import asyncio
import json
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
    ``ping`` heartbeats. This is raw runtime output; for structured per-turn
    activity fetch the durable Turso session instead (see
    :mod:`syte.turso_store` and ``GET /api/agent_session/{session_id}``).
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
