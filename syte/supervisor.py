import asyncio
import logging

from syte.certificates import apply_proxy_config, ensure_caddy
from syte.database import list_projects, update_project
from syte import process_manager
from syte.workspace import command_exists

logger = logging.getLogger("syte.supervisor")

_running = False
_fail_counts: dict[str, int] = {}


async def maintain() -> None:
    """Keep Caddy, deployed services, and Syte cloud agents running."""
    ensure_caddy()
    projects = await list_projects()
    for project in projects:
        if project.get("status") != "running":
            continue
        pid = project["id"]
        deploy_type = project.get("deploy_type", "shell")
        start_cmd = project.get("start_command", "")

        if deploy_type == "shell" and "npm" in (start_cmd or "").lower():
            if not command_exists("npm"):
                from syte.runtime import ensure_npm
                ok, msg = ensure_npm()
                if not ok:
                    logger.error("Stopping %s — %s", pid, msg)
                    await update_project(pid, {"status": "stopped"})
                    _fail_counts.pop(pid, None)
                    continue

        if process_manager.is_running(pid, deploy_type):
            _fail_counts.pop(pid, None)
            continue

        fails = _fail_counts.get(pid, 0) + 1
        _fail_counts[pid] = fails
        if fails > 3:
            logger.error("Giving up on %s after %d failed restarts", pid, fails)
            await update_project(pid, {"status": "stopped"})
            _fail_counts.pop(pid, None)
            continue

        logger.warning("Restarting service %s (%s), attempt %d", pid, deploy_type, fails)
        ok, msg = process_manager.start_project(
            pid,
            project["port"],
            start_cmd,
            project.get("env_vars", "{}"),
            deploy_type,
            project.get("dockerfile_path"),
        )
        if ok:
            logger.info("Restarted %s: %s", pid, msg)
            _fail_counts.pop(pid, None)
        else:
            logger.error("Failed to restart %s: %s", pid, msg)
            if fails >= 2:
                await update_project(pid, {"status": "stopped"})
                _fail_counts.pop(pid, None)

    try:
        from syte.preview_manager import expire_stale_previews
        await expire_stale_previews()
    except Exception as exc:
        logger.exception("Preview expiry check failed: %s", exc)


async def supervisor_loop(interval: int = 30) -> None:
    global _running
    _running = True
    while _running:
        try:
            await maintain()
        except Exception as exc:
            logger.exception("Supervisor error: %s", exc)
        await asyncio.sleep(interval)


def stop_supervisor() -> None:
    global _running
    _running = False


async def startup() -> None:
    """Apply VM services and resume durable cloud-agent requests."""
    await apply_proxy_config()
    ensure_caddy()
    await maintain()
    from syte.agent_jobs import resume_pending_requests
    resumed = await resume_pending_requests()
    if resumed:
        logger.info("Resumed %d durable cloud-agent request(s)", resumed)
