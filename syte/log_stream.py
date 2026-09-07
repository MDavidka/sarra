"""Server-Sent Events (SSE) streaming for deployment and preview logs.

The module tails project build, application, container, and preview log files,
emitting each appended line as an SSE ``data:`` frame with periodic heartbeats.
"""

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import Request

from syte.docker_deploy import _build_log_path, container_name, docker_container_exists
from syte.process_manager import get_logs
from syte.workspace import deploy_log_path, run_cmd, workspace_path


def _check_disconnect(request: Optional[Request]) -> bool:
    """Best-effort disconnect check usable from sync helpers.

    The async generators below call this between blocking reads; on a closed
    tab the FastAPI/Starlette ``Request.is_disconnected()`` future resolves
    and the generator breaks the loop instead of polling until the timeout.
    """
    if request is None:
        return False
    try:
        return bool(request.is_disconnected())
    except Exception:
        return False


def _format_frame(label: str, line: Optional[str] = None) -> bytes:
    payload = {"type": label}
    if line is not None:
        payload["text"] = line
    return f"data: {json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}\n\n".encode("utf-8")


async def _read_new_lines(
    path: Path,
    offset: int,
) -> tuple[int, list[str]]:
    """Read newly-appended lines from ``path`` without blocking the event loop.

    Returns the new offset and the lines read. Offloaded to a thread because
    ``path.open()`` + ``f.read()`` are blocking syscalls.
    """
    def _read() -> tuple[int, list[str]]:
        if not path.exists():
            return offset, []
        size = path.stat().st_size
        start = offset
        if size < start:
            start = 0
        if size <= start:
            return start, []
        with path.open("r", errors="replace") as f:
            f.seek(start)
            chunk = f.read()
            return f.tell(), chunk.splitlines()

    return await asyncio.to_thread(_read)


async def stream_project_logs(
    project_id: str,
    deploy_type: str = "shell",
    *,
    live_only: bool = False,
    request: Optional[Request] = None,
) -> AsyncGenerator[bytes, None]:
    """SSE generator — tails build.log, app.log, and docker container output.

    Emits a snapshot of recent lines (unless ``live_only``), then polls the log
    files twice a second for ~37 minutes, forwarding each appended line as a
    ``data:`` frame typed ``deploy``/``build``/``app``/``container``. A ``ping``
    heartbeat is sent every few seconds. Aborts promptly when ``request`` has
    disconnected so abandoned tabs don't keep server-side tailers alive.
    """
    ws = workspace_path(project_id)
    deploy_log = deploy_log_path(project_id)
    build_log = _build_log_path(project_id)
    app_log = ws / "app.log"

    if not live_only:
        snapshot = await asyncio.to_thread(get_logs, project_id, 200, deploy_type)
        if snapshot and snapshot != "No logs yet.":
            for line in snapshot.splitlines():
                yield _format_frame("log", line)

    def _stat_size(p: Path) -> int:
        try:
            return p.stat().st_size
        except FileNotFoundError:
            return 0

    offsets: dict[Path, int] = {
        path: (await asyncio.to_thread(_stat_size, path))
        for path in (deploy_log, build_log, app_log)
    }

    if live_only:
        yield _format_frame("session", "Live deploy session started")

    docker_tick = 0
    last_docker_lines: list[str] = []  # bounded ring, see below
    for _ in range(4500):
        if _check_disconnect(request):
            break

        for path, label in ((deploy_log, "deploy"), (build_log, "build"), (app_log, "app")):
            if not path.exists():
                continue
            pos = offsets.get(path, 0)
            new_offset, lines = await _read_new_lines(path, pos)
            offsets[path] = new_offset
            for line in lines:
                yield _format_frame(label, line)

        if deploy_type == "docker" and docker_tick % 8 == 0:
            exists = await asyncio.to_thread(docker_container_exists, project_id)
            if exists:
                name = container_name(project_id)

                def _docker_tail() -> tuple[int, str]:
                    code, out = run_cmd(["docker", "logs", "--tail", "8", name])
                    return code, out

                code, out = await asyncio.to_thread(_docker_tail)
                if code == 0 and out.strip():
                    for line in out.strip().splitlines():
                        if line not in last_docker_lines:
                            last_docker_lines.append(line)
                            # Keep the dedupe window bounded — long-lived
                            # streams used to leak memory here.
                            if len(last_docker_lines) > 200:
                                last_docker_lines = last_docker_lines[-200:]
                            yield _format_frame("container", line)

        docker_tick += 1
        if docker_tick % 10 == 0:
            yield _format_frame("ping")
        await asyncio.sleep(0.5)


async def stream_preview_logs(
    project_id: str,
    *,
    live_only: bool = False,
    request: Optional[Request] = None,
) -> AsyncGenerator[bytes, None]:
    """SSE generator — tails preview.log for live dev-server output.

    Replays the recent preview log (unless ``live_only``), then polls every
    250ms for up to ~30 minutes, emitting each new line as a ``preview`` frame
    with periodic ``ping`` heartbeats. Aborts promptly when ``request`` has
    disconnected.
    """
    from syte.preview_manager import get_preview_logs, preview_log_path

    log_path = preview_log_path(project_id)

    if not live_only:
        snapshot = await asyncio.to_thread(get_preview_logs, project_id, 300)
        if snapshot and snapshot != "No preview logs yet.":
            for line in snapshot.splitlines():
                yield _format_frame("preview", line)

    def _stat_size(p: Path) -> int:
        try:
            return p.stat().st_size
        except FileNotFoundError:
            return 0

    offset = await asyncio.to_thread(_stat_size, log_path)

    if live_only:
        yield _format_frame("session", "Live preview session")

    for _ in range(7200):
        if _check_disconnect(request):
            break
        new_offset, lines = await _read_new_lines(log_path, offset)
        offset = new_offset
        for line in lines:
            yield _format_frame("preview", line)
        if _ % 20 == 0:
            yield _format_frame("ping")
        await asyncio.sleep(0.25)