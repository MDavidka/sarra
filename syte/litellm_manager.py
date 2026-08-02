"""LiteLLM proxy management for Syra.

LiteLLM runs as a Docker container providing a unified API gateway to multiple
LLM providers. This module manages the container lifecycle: start, stop, status,
and configuration.

The proxy exposes:
- Admin UI at http://localhost:4000/ui
- OpenAI-compatible API at http://localhost:4000/v1
- Virtual key management and spend tracking
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from syte.database import get_setting, set_setting

logger = logging.getLogger(__name__)

LITELLM_CONTAINER_NAME = "syte-litellm"
LITELLM_IMAGE = "ghcr.io/berriai/litellm:main-latest"
LITELLM_DEFAULT_PORT = 4000
LITELLM_DATA_DIR = "/var/lib/syte/litellm"


async def _run_docker(args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    """Run a docker command and return (exit_code, output)."""
    cmd = ["docker", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()
        return proc.returncode or 0, output
    except asyncio.TimeoutError:
        return 1, f"Docker command timed out: {' '.join(args)}"
    except FileNotFoundError:
        return 1, "Docker is not installed or not in PATH"
    except Exception as e:
        return 1, f"Docker command failed: {e}"


async def litellm_status() -> dict[str, Any]:
    """Check if LiteLLM container is running and healthy.
    
    Returns status including:
    - running: bool
    - healthy: bool
    - port: int
    - admin_url: str
    - api_url: str
    - container_id: str
    - uptime_seconds: int
    """
    exit_code, output = await _run_docker([
        "ps",
        "-a",
        "--filter", f"name={LITELLM_CONTAINER_NAME}",
        "--format", "json",
    ])
    
    result: dict[str, Any] = {
        "running": False,
        "healthy": False,
        "port": LITELLM_DEFAULT_PORT,
        "admin_url": f"http://localhost:{LITELLM_DEFAULT_PORT}/ui",
        "api_url": f"http://localhost:{LITELLM_DEFAULT_PORT}/v1",
        "container_id": "",
        "uptime_seconds": 0,
        "message": "",
    }
    
    if exit_code != 0:
        result["message"] = output or "Failed to check container status"
        return result
    
    if not output.strip():
        result["message"] = "LiteLLM container not found"
        return result
    
    try:
        # Docker ps --format json returns one JSON object per line
        containers = [json.loads(line) for line in output.strip().split("\n") if line.strip()]
        if not containers:
            result["message"] = "LiteLLM container not found"
            return result
        
        container = containers[0]
        result["container_id"] = container.get("ID", "")[:12]
        state = container.get("State", "").lower()
        status = container.get("Status", "").lower()
        
        result["running"] = state == "running"
        result["healthy"] = "healthy" in status or result["running"]
        
        # Parse uptime from status like "Up 2 hours"
        if result["running"] and "up" in status:
            result["message"] = f"Container running: {status}"
        elif state == "exited":
            result["message"] = f"Container exited: {container.get('Status', 'unknown')}"
        else:
            result["message"] = f"Container state: {state}"
            
    except json.JSONDecodeError as e:
        result["message"] = f"Failed to parse docker output: {e}"
    
    return result


async def start_litellm(
    *,
    port: int | None = None,
    master_key: str | None = None,
    salt_key: str | None = None,
) -> dict[str, Any]:
    """Start the LiteLLM Docker container.
    
    Parameters:
    - port: Port to expose (default 4000)
    - master_key: Master key for admin access (required for UI)
    - salt_key: Salt key for encrypting provider keys
    
    Returns status dict with ok, message, and container info.
    """
    # Check if already running
    status = await litellm_status()
    if status["running"]:
        return {
            "ok": True,
            "message": "LiteLLM is already running",
            **status,
        }
    
    # Get configuration from settings if not provided
    if port is None:
        port = LITELLM_DEFAULT_PORT
    if master_key is None:
        master_key = (await get_setting("litellm_master_key", "")).strip()
    if salt_key is None:
        salt_key = (await get_setting("litellm_salt_key", "")).strip()
    
    # Generate defaults if not set
    if not master_key:
        import secrets
        master_key = f"sk-{secrets.token_hex(16)}"
        await set_setting("litellm_master_key", master_key)
    
    if not salt_key:
        import secrets
        salt_key = secrets.token_hex(32)
        await set_setting("litellm_salt_key", salt_key)
    
    # Ensure data directory exists
    os.makedirs(LITELLM_DATA_DIR, exist_ok=True)
    
    # Remove old container if exists (stopped)
    await _run_docker(["rm", "-f", LITELLM_CONTAINER_NAME])
    
    # Run the container
    args = [
        "run",
        "-d",
        "--name", LITELLM_CONTAINER_NAME,
        "-p", f"{port}:4000",
        "-v", f"{LITELLM_DATA_DIR}:/app/litellm",
        "-e", f"LITELLM_MASTER_KEY={master_key}",
        "-e", f"LITELLM_SALT_KEY={salt_key}",
        "-e", "DATABASE_URL=sqlite:////app/litellm/litellm.db",
        "--restart", "unless-stopped",
        LITELLM_IMAGE,
    ]
    
    exit_code, output = await _run_docker(args, timeout=60.0)
    
    if exit_code != 0:
        return {
            "ok": False,
            "message": f"Failed to start LiteLLM: {output}",
            "running": False,
        }
    
    # Wait a moment for container to start
    await asyncio.sleep(2.0)
    
    # Verify it's running
    status = await litellm_status()
    if status["running"]:
        return {
            "ok": True,
            "message": f"LiteLLM started successfully on port {port}",
            "master_key": master_key,
            **status,
        }
    else:
        return {
            "ok": False,
            "message": f"Container started but not running: {status.get('message', 'unknown')}",
            **status,
        }


async def stop_litellm() -> dict[str, Any]:
    """Stop the LiteLLM Docker container."""
    status = await litellm_status()
    if not status["running"]:
        return {
            "ok": True,
            "message": "LiteLLM is not running",
            **status,
        }
    
    exit_code, output = await _run_docker(["stop", LITELLM_CONTAINER_NAME])
    
    if exit_code != 0:
        return {
            "ok": False,
            "message": f"Failed to stop LiteLLM: {output}",
        }
    
    return {
        "ok": True,
        "message": "LiteLLM stopped successfully",
        "running": False,
    }


async def restart_litellm() -> dict[str, Any]:
    """Restart the LiteLLM Docker container."""
    # Stop if running
    await stop_litellm()
    
    # Start fresh
    return await start_litellm()


async def litellm_logs(lines: int = 100) -> dict[str, Any]:
    """Get logs from the LiteLLM container."""
    exit_code, output = await _run_docker([
        "logs",
        "--tail", str(lines),
        LITELLM_CONTAINER_NAME,
    ])
    
    return {
        "ok": exit_code == 0,
        "logs": output,
        "message": "" if exit_code == 0 else output,
    }


async def litellm_models() -> dict[str, Any]:
    """Get list of configured models from LiteLLM.
    
    Requires LiteLLM to be running with master key configured.
    """
    import httpx
    
    master_key = (await get_setting("litellm_master_key", "")).strip()
    if not master_key:
        return {
            "ok": False,
            "message": "LiteLLM master key not configured",
            "models": [],
        }
    
    status = await litellm_status()
    if not status["running"]:
        return {
            "ok": False,
            "message": "LiteLLM is not running",
            "models": [],
        }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"http://localhost:{LITELLM_DEFAULT_PORT}/models",
                headers={"Authorization": f"Bearer {master_key}"},
            )
            
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                return {
                    "ok": True,
                    "models": models,
                    "count": len(models),
                }
            else:
                return {
                    "ok": False,
                    "message": f"Failed to fetch models: HTTP {resp.status_code}",
                    "models": [],
                }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Error fetching models: {e}",
            "models": [],
        }


async def litellm_health() -> dict[str, Any]:
    """Check LiteLLM health endpoint."""
    import httpx
    
    status = await litellm_status()
    if not status["running"]:
        return {
            "ok": False,
            "healthy": False,
            "message": "LiteLLM is not running",
        }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://localhost:{LITELLM_DEFAULT_PORT}/health")
            
            if resp.status_code == 200:
                return {
                    "ok": True,
                    "healthy": True,
                    "message": "LiteLLM is healthy",
                }
            else:
                return {
                    "ok": False,
                    "healthy": False,
                    "message": f"Health check failed: HTTP {resp.status_code}",
                }
    except Exception as e:
        return {
            "ok": False,
            "healthy": False,
            "message": f"Health check error: {e}",
        }
