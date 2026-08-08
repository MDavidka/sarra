"""Lifecycle management for the local 9Router Docker gateway.

The router is kept on loopback and published through Syte's Caddy route at
``https://api.sycord.site/v1``.  Its SQLite data is persisted outside the
container so redeploying the image does not remove provider connections,
models, or usage history.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from syte.config import settings
from syte.database import get_setting, set_setting
from syte.caddy_routes import NINE_ROUTER_PUBLIC_HOST

NINE_ROUTER_CONTAINER_NAME = "syte-9router"
NINE_ROUTER_IMAGE = "decolua/9router:latest"
NINE_ROUTER_CONTAINER_PORT = 20128
# Caddy already owns 127.0.0.1:20128 for the legacy remote-router TLS probe
# when the managed route is disabled. Keep the Docker host binding separate;
# the container can still listen on the upstream's documented port internally.
NINE_ROUTER_HOST_PORT = 20129
NINE_ROUTER_LOCAL_BASE_URL = f"http://127.0.0.1:{NINE_ROUTER_HOST_PORT}"
NINE_ROUTER_INTERNAL_BASE_URL = f"http://127.0.0.1:{NINE_ROUTER_CONTAINER_PORT}"
NINE_ROUTER_PUBLIC_API_URL = f"https://{NINE_ROUTER_PUBLIC_HOST}/v1"
NINE_ROUTER_DASHBOARD_PATH = "/dashboard"
NINE_ROUTER_DASHBOARD_URL = f"https://{NINE_ROUTER_PUBLIC_HOST}{NINE_ROUTER_DASHBOARD_PATH}"
NINE_ROUTER_DATA_DIR_NAME = "9router"
NINE_ROUTER_READINESS_TIMEOUT = 45.0
NINE_ROUTER_READINESS_POLL_SECONDS = 1.0
NINE_ROUTER_PASSWORD_SETTING = "nine_router_initial_password"
NINE_ROUTER_PASSWORD_REVEALED_SETTING = "nine_router_initial_password_revealed"
NINE_ROUTER_ENABLED_SETTING = "nine_router_public_enabled"


async def _run_docker(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
    """Run one Docker command without blocking the event loop."""
    command = ["docker", *args]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return process.returncode or 0, output.decode("utf-8", errors="replace").strip()
    except TimeoutError:
        return 1, f"Docker command timed out: {' '.join(args)}"
    except FileNotFoundError:
        return 1, "Docker is not installed or not available in PATH."
    except (OSError, RuntimeError, ValueError) as error:
        return 1, f"Docker command failed: {error}"


def _data_dir() -> Path:
    return settings.data_dir / NINE_ROUTER_DATA_DIR_NAME


def _parse_container(output: str) -> dict[str, Any] | None:
    if not output.strip():
        return None
    try:
        parsed: Any = json.loads(output)
    except json.JSONDecodeError:
        records: list[Any] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        parsed = records
    if isinstance(parsed, list):
        return parsed[0] if parsed and isinstance(parsed[0], dict) else None
    return parsed if isinstance(parsed, dict) else None


async def _probe_router_http() -> dict[str, Any]:
    """Verify that the official dashboard and OpenAI route answer locally.

    Docker's ``running`` state only proves that the Node process has not exited.
    The official deployment exposes the dashboard at ``/dashboard`` and the
    OpenAI-compatible API at ``/v1``; probe both before publishing Caddy.
    A 401/403 from ``/v1/models`` is still a reachable API when API-key
    enforcement is enabled, but it is reported separately from authentication.
    """
    import httpx

    api_key = (await get_setting("agent_9router_api_key", "")).strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    dashboard_status: int | None = None
    api_status: int | None = None
    try:
        async with httpx.AsyncClient(
            base_url=NINE_ROUTER_LOCAL_BASE_URL,
            timeout=3.0,
            follow_redirects=True,
        ) as client:
            dashboard = await client.get(NINE_ROUTER_DASHBOARD_PATH)
            api = await client.get("/v1/models", headers=headers)
        dashboard_status = dashboard.status_code
        api_status = api.status_code
    except (httpx.HTTPError, OSError) as error:
        return {
            "web_gui_ready": False,
            "api_ready": False,
            "api_authenticated": False,
            "dashboard_status": dashboard_status,
            "api_status": api_status,
            "readiness_message": f"9Router is still starting: {error}",
        }

    web_gui_ready = 200 <= dashboard_status < 400 or dashboard_status in {401, 403}
    api_authenticated = 200 <= api_status < 400
    api_ready = api_authenticated or api_status in {401, 403}
    if not web_gui_ready:
        message = f"9Router dashboard is not ready (HTTP {dashboard_status})."
    elif not api_ready:
        message = f"9Router API is not ready (HTTP {api_status})."
    elif not api_authenticated:
        message = "9Router dashboard and API are ready; save an API key before making authenticated API calls."
    else:
        message = "9Router dashboard and authenticated API are ready."
    return {
        "web_gui_ready": web_gui_ready,
        "api_ready": api_ready,
        "api_authenticated": api_authenticated,
        "dashboard_status": dashboard_status,
        "api_status": api_status,
        "readiness_message": message,
    }


async def router_status() -> dict[str, Any]:
    """Return a safe, browser-facing status for the managed container."""
    code, output = await _run_docker([
        "ps", "-a", "--filter", f"name={NINE_ROUTER_CONTAINER_NAME}", "--format", "json",
    ])
    result: dict[str, Any] = {
        "ok": code == 0,
        "running": False,
        "healthy": False,
        "container_id": "",
        "image": NINE_ROUTER_IMAGE,
        "port": NINE_ROUTER_HOST_PORT,
        "public_host": NINE_ROUTER_PUBLIC_HOST,
        "public_api_url": NINE_ROUTER_PUBLIC_API_URL,
        "dashboard_url": NINE_ROUTER_DASHBOARD_URL,
        "web_gui_ready": False,
        "api_ready": False,
        "api_authenticated": False,
        "dashboard_status": None,
        "api_status": None,
        "ready": False,
        "message": "",
        "enabled": (await get_setting(NINE_ROUTER_ENABLED_SETTING, "0")) == "1",
        "initial_password_set": bool((await get_setting(NINE_ROUTER_PASSWORD_SETTING, "")).strip()),
    }
    if code != 0:
        result["message"] = output or "Could not inspect the 9Router container."
        return result
    container = _parse_container(output)
    if not container:
        result["message"] = "9Router is not deployed."
        return result

    result["container_id"] = str(container.get("ID") or "")[:12]
    state = str(container.get("State") or "").lower()
    status = str(container.get("Status") or "")
    result["running"] = state == "running"
    if not result["running"]:
        result["message"] = f"9Router container is {status or state or 'stopped'}."
        return result

    readiness = await _probe_router_http()
    result.update(readiness)
    result["ready"] = bool(result["web_gui_ready"] and result["api_ready"])
    result["healthy"] = bool(result["ready"] and "unhealthy" not in status.lower())
    result["message"] = (
        f"9Router is running: {status}. {readiness['readiness_message']}"
        if result["ready"]
        else readiness["readiness_message"]
    )
    return result


async def _router_password() -> tuple[str, bool]:
    """Return the persistent password and whether it still needs one-time reveal."""
    password = (await get_setting(NINE_ROUTER_PASSWORD_SETTING, "")).strip()
    if not password:
        password = secrets.token_urlsafe(24)
        await set_setting(NINE_ROUTER_PASSWORD_SETTING, password)
    revealed = (await get_setting(NINE_ROUTER_PASSWORD_REVEALED_SETTING, "0")).strip() == "1"
    return password, not revealed


async def start_router() -> dict[str, Any]:
    """Install/start the published 9Router image with persistent data."""
    current = await router_status()
    if current["running"] and current.get("ready"):
        return {**current, "ok": True, "message": "9Router is already running and ready."}

    data_dir = _data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return {**current, "ok": False, "message": f"Could not create {data_dir}: {error}"}

    password, reveal_password = await _router_password()
    await _run_docker(["rm", "-f", NINE_ROUTER_CONTAINER_NAME], timeout=30.0)
    code, output = await _run_docker([
        "run", "-d",
        "--name", NINE_ROUTER_CONTAINER_NAME,
        "-p", f"127.0.0.1:{NINE_ROUTER_HOST_PORT}:{NINE_ROUTER_CONTAINER_PORT}",
        "-v", f"{data_dir}:/app/data",
        "-e", "DATA_DIR=/app/data",
        "-e", f"PORT={NINE_ROUTER_CONTAINER_PORT}",
        "-e", "HOSTNAME=0.0.0.0",
        "-e", "NODE_ENV=production",
        "-e", f"BASE_URL={NINE_ROUTER_INTERNAL_BASE_URL}",
        "-e", f"NEXT_PUBLIC_BASE_URL=https://{NINE_ROUTER_PUBLIC_HOST}",
        "-e", "AUTH_COOKIE_SECURE=true",
        "-e", "REQUIRE_API_KEY=true",
        "-e", f"INITIAL_PASSWORD={password}",
        "--restart", "unless-stopped",
        NINE_ROUTER_IMAGE,
    ], timeout=120.0)
    if code != 0:
        return {**current, "ok": False, "message": f"Failed to start 9Router: {output or 'unknown Docker error'}"}

    deadline = asyncio.get_running_loop().time() + NINE_ROUTER_READINESS_TIMEOUT
    status = await router_status()
    while status["running"] and not status.get("ready"):
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(NINE_ROUTER_READINESS_POLL_SECONDS)
        status = await router_status()
    if not status["running"]:
        return {**status, "ok": False, "message": f"9Router container started but is not running: {status['message']}"}
    if not status.get("ready"):
        return {
            **status,
            "ok": False,
            "message": (
                f"9Router container is running but did not become ready within "
                f"{NINE_ROUTER_READINESS_TIMEOUT:.0f}s: {status['message']}"
            ),
        }
    result = {
        **status,
        "ok": True,
        "message": (
            f"9Router dashboard is ready at {NINE_ROUTER_DASHBOARD_URL}; "
            f"the public OpenAI-compatible API is {NINE_ROUTER_PUBLIC_API_URL}."
        ),
    }
    # Reveal the generated/admin password only after the first successful
    # deployment. Subsequent restarts still need the persisted value in Docker
    # environment metadata, but never send it back to the browser again.
    if reveal_password:
        await set_setting(NINE_ROUTER_PASSWORD_REVEALED_SETTING, "1")
        result["initial_password"] = password
    return result


async def stop_router() -> dict[str, Any]:
    """Stop the local 9Router container without deleting its data."""
    current = await router_status()
    if not current["running"]:
        return {**current, "ok": True, "message": "9Router is not running."}
    code, output = await _run_docker(["stop", NINE_ROUTER_CONTAINER_NAME], timeout=60.0)
    if code != 0:
        return {**current, "ok": False, "message": f"Failed to stop 9Router: {output or 'unknown Docker error'}"}
    return {**current, "ok": True, "running": False, "healthy": False, "message": "9Router stopped; its data was preserved."}


async def restart_router() -> dict[str, Any]:
    """Restart 9Router using the same persistent data directory and settings."""
    stopped = await stop_router()
    if not stopped.get("ok"):
        return stopped
    return await start_router()


async def router_logs(lines: int = 100) -> dict[str, Any]:
    """Return recent container output for the Router tab."""
    code, output = await _run_docker([
        "logs", "--tail", str(max(1, min(int(lines), 500))), NINE_ROUTER_CONTAINER_NAME,
    ], timeout=30.0)
    return {"ok": code == 0, "logs": output, "message": "" if code == 0 else output}
