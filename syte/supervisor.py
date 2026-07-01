import asyncio
import logging

from syte.certificates import apply_proxy_config, ensure_caddy
from syte.database import list_projects
from syte import process_manager

logger = logging.getLogger("syte.supervisor")

_running = False


async def maintain() -> None:
    """Keep Caddy and deployed services running."""
    ensure_caddy()
    projects = await list_projects()
    for project in projects:
        if project.get("status") != "running":
            continue
        pid = project["id"]
        deploy_type = project.get("deploy_type", "shell")
        if process_manager.is_running(pid, deploy_type):
            continue
        logger.warning("Restarting service %s (%s)", pid, deploy_type)
        ok, msg = process_manager.start_project(
            pid,
            project["port"],
            project["start_command"],
            project.get("env_vars", "{}"),
            deploy_type,
            project.get("dockerfile_path"),
        )
        if ok:
            logger.info("Restarted %s: %s", pid, msg)
        else:
            logger.error("Failed to restart %s: %s", pid, msg)


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
    """Apply proxy config and ensure stack is up on boot."""
    await apply_proxy_config()
    ensure_caddy()
    await maintain()
