"""Server-Sent Events log streaming."""

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
