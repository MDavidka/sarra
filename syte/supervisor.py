import asyncio
import logging

from syte.certificates import apply_proxy_config, ensure_caddy
from syte.openhands_agent import (
    is_agent_running,
    openhands_installed,
    probe_agent_http,
    warm_agent,
)
from syte.database import list_projects, update_project
from syte import process_manager
from syte.workspace import command_exists

logger = logging.getLogger("syte.supervisor")

_running = False
_fail_counts: dict[str, int] = {}


async def maintain() -> None:
    """Keep Caddy, deployed services, and OpenHands agents running."""
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

    for project in projects:
        pid = project["id"]
        status = project.get("agent_status") or "stopped"
        if status == "stopped":
            continue
        if is_agent_running(pid):
            port = project.get("agent_port")
            if port and (await probe_agent_http(int(port))).get("ok"):
                continue
        logger.warning("Warming OpenHands agent for %s (status=%s)", pid, status)
        result = await warm_agent(pid, source="supervisor")
        if not result.get("ok"):
            message = str(result.get("message") or "Agent warm-up failed")
            logger.error("Failed to warm OpenHands agent for %s: %s", pid, message)
            await update_project(
                pid,
                {"agent_status": "error", "agent_last_error": message[:4000]},
            )

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
    """Apply proxy config and ensure stack is up on boot."""
    await apply_proxy_config()
    ensure_caddy()
    await maintain()


async def autostart_project_agents() -> None:
    """Start OpenHands agents for projects that should run continuously."""
    from syte.openhands_agent import bridge_settings

    if not openhands_installed():
        return
    try:
        bridge = await bridge_settings()
    except Exception:
        return
    has_key = any(bridge["profiles"][name]["api_key"] for name in bridge["profiles"])
    if not has_key:
        return

    projects = await list_projects()
    for project in projects:
        pid = project["id"]
        status = project.get("agent_status") or "running"
        if status == "stopped":
            continue
        result = await warm_agent(pid, source="startup")
        if result.get("ok"):
            logger.info("Scheduled OpenHands warm-up for %s", pid)
        else:
            message = str(result.get("message") or "Agent warm-up failed")
            logger.warning(
                "Autostart OpenHands agent failed for %s: %s",
                pid,
                message[:200],
            )
