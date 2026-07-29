"""Install and supervise the local Solar-compatible coding model.

Solar is exposed through Ollama's OpenAI-compatible API so the normal agent
completion path can use it without a second chat implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from syte.ai_providers import OLLAMA_API_BASE, SOLAR_MODEL
from syte.config import settings

OLLAMA_URL = OLLAMA_API_BASE.removesuffix("/v1")
_setup_task: asyncio.Task[None] | None = None
_ollama_process: subprocess.Popen[str] | None = None
_state: dict[str, Any] = {"status": "not_configured", "message": "Solar is not installed."}


def _log_path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "solar-setup.log"


def _set_state(status: str, message: str = "") -> None:
    _state.update({"status": status, "message": message})


def _request_json(path: str, *, timeout: float = 1.5) -> dict[str, Any] | None:
    request = Request(f"{OLLAMA_URL}{path}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost only
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (OSError, URLError, ValueError):
        return None


async def _local_snapshot() -> tuple[bool, bool, bool]:
    installed = shutil.which("ollama") is not None
    if not installed:
        return False, False, False
    tags = await asyncio.to_thread(_request_json, "/api/tags")
    reachable = tags is not None
    models = tags.get("models", []) if tags else []
    available = any(
        isinstance(model, dict)
        and str(model.get("name") or model.get("model") or "").split("@")[0] == SOLAR_MODEL
        for model in models
    )
    return installed, reachable, available


async def solar_status() -> dict[str, Any]:
    """Return the user-facing installation, process, and model state."""
    installed, reachable, model_available = await _local_snapshot()
    setup_running = _setup_task is not None and not _setup_task.done()
    if setup_running:
        status = _state.get("status") or "installing"
    elif reachable and model_available:
        status = "running"
    elif _state.get("status") == "error":
        status = "error"
    elif installed and reachable:
        status = "installed"
    elif installed:
        status = "installed"
    else:
        status = "not_configured"
    if status == "running":
        message = "Solar is running and accepting AI chat requests."
    elif status == "installed":
        message = "Ollama is installed; the Solar model still needs to be started or downloaded."
    elif setup_running:
        message = _state.get("message") or "Installing Solar model…"
    else:
        message = _state.get("message") or "Solar is not installed."
    return {
        "ok": True,
        "profile": "syra-solar",
        "model": SOLAR_MODEL,
        "display_model": "Qwen 2.5 Coder 3B",
        "api_base": OLLAMA_API_BASE,
        "installed": installed,
        "reachable": reachable,
        "model_available": model_available,
        "running": status == "running",
        "status": status,
        "message": message,
        "log_path": str(_log_path()),
    }


def _run_logged(argv: list[str], *, input_text: str | None = None) -> tuple[int, str]:
    log_path = _log_path()
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(argv)}\n")
        result = subprocess.run(
            argv,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=1800,
        )
        output = result.stdout or ""
        log.write(output)
    return result.returncode, output


def _start_ollama() -> None:
    global _ollama_process
    if _ollama_process is not None and _ollama_process.poll() is None:
        return
    log = _log_path().open("a", encoding="utf-8")
    env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11434"}
    _ollama_process = subprocess.Popen(
        ["ollama", "serve"],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
        text=True,
    )


async def _install_and_start() -> None:
    try:
        _set_state("installing", "Installing Ollama on the VM…")
        if shutil.which("ollama") is None:
            if shutil.which("curl") is None:
                raise RuntimeError("curl is required to install Ollama on this VM.")
            code, script = await asyncio.to_thread(
                _run_logged, ["curl", "-fsSL", "https://ollama.com/install.sh"]
            )
            if code != 0 or not script.strip():
                raise RuntimeError("Could not download the Ollama installer. See the Solar setup log.")
            code, output = await asyncio.to_thread(_run_logged, ["sh"], input_text=script)
            if code != 0 or shutil.which("ollama") is None:
                raise RuntimeError(
                    "Ollama installation failed. " + (output.strip()[-500:] or "See the Solar setup log.")
                )

        _set_state("starting", "Starting the local Solar API…")
        await asyncio.to_thread(_start_ollama)
        for _ in range(60):
            _, reachable, _ = await _local_snapshot()
            if reachable:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Ollama did not start on http://127.0.0.1:11434.")

        _set_state("downloading", f"Downloading {SOLAR_MODEL} (this can take a few minutes)…")
        code, output = await asyncio.to_thread(_run_logged, ["ollama", "pull", SOLAR_MODEL])
        if code != 0:
            raise RuntimeError(output.strip()[-700:] or f"Could not download {SOLAR_MODEL}.")
        _set_state("running", "Solar is running and accepting AI chat requests.")
    except Exception as exc:  # surfaced through the status endpoint
        _set_state("error", str(exc) or "Solar setup failed.")


async def start_solar_setup() -> dict[str, Any]:
    """Start idempotent background setup and return its current state."""
    global _setup_task
    if _setup_task is None or _setup_task.done():
        current = await solar_status()
        if current["running"]:
            return current
        _setup_task = asyncio.create_task(_install_and_start())
    return await solar_status()
