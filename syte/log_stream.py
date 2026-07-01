"""Server-Sent Events log streaming."""

import asyncio
import json
from pathlib import Path

from syte.docker_deploy import _build_log_path, container_name
from syte.process_manager import get_logs
from syte.workspace import run_cmd, workspace_path


async def stream_project_logs(project_id: str, deploy_type: str = "shell"):
    """SSE generator — tails build.log, app.log, and docker container output."""
    ws = workspace_path(project_id)
    build_log = _build_log_path(project_id)
    app_log = ws / "app.log"

    snapshot = get_logs(project_id, 200, deploy_type)
    if snapshot and snapshot != "No logs yet.":
        for line in snapshot.splitlines():
            yield f"data: {json.dumps({'type': 'log', 'text': line})}\n\n"

    offsets: dict[Path, int] = {}
    for path in (build_log, app_log):
        offsets[path] = path.stat().st_size if path.exists() else 0

    docker_tick = 0
    for _ in range(4500):
        for path, label in ((build_log, "build"), (app_log, "app")):
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
            name = container_name(project_id)
            code, out = await asyncio.to_thread(
                run_cmd, ["docker", "logs", "--tail", "8", name]
            )
            if code == 0 and out.strip():
                for line in out.strip().splitlines():
                    yield f"data: {json.dumps({'type': 'container', 'text': line})}\n\n"

        docker_tick += 1
        yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        await asyncio.sleep(0.5)
