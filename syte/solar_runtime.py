"""Remove the legacy Solar/Ollama model from an existing Syte VM.

Solar is no longer an available provider.  This small compatibility module is
kept so installations created by older releases can clean up the downloaded
model without reinstalling or starting Ollama.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from syte.config import settings

OLLAMA_URL = "http://127.0.0.1:11434"
SOLAR_MODEL = "qwen2.5-coder:3b"
_ollama_process: subprocess.Popen[str] | None = None
_state: dict[str, Any] = {"status": "not_configured", "message": "Legacy Solar is not installed."}


def _log_path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "solar-cleanup.log"


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
    """Return the legacy Solar model state for the cleanup card."""
    installed, reachable, model_available = await _local_snapshot()
    if _state.get("status") == "error":
        status = "error"
    elif model_available:
        status = "installed"
    elif installed:
        status = "ollama_only"
    else:
        status = "not_configured"
    if status == "installed":
        message = "Legacy Solar is installed on this VM."
    elif status == "ollama_only":
        message = (
            _state.get("message")
            if _state.get("message") and _state.get("message") != "Legacy Solar is not installed."
            else "Ollama is installed, but the legacy Solar model is not present."
        )
    else:
        message = _state.get("message") or "Legacy Solar is not installed."
    return {
        "ok": True,
        "profile": "legacy-solar",
        "model": SOLAR_MODEL,
        "display_model": "Qwen 2.5 Coder 3B",
        "installed": installed,
        "reachable": reachable,
        "model_available": model_available,
        "running": False,
        "status": status,
        "message": message,
        "log_path": str(_log_path()),
    }


def _run_logged(argv: list[str]) -> tuple[int, str]:
    with _log_path().open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(argv)}\n")
        result = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=300,
        )
        output = result.stdout or ""
        log.write(output)
    return result.returncode, output


def _stop_managed_ollama() -> None:
    global _ollama_process
    if _ollama_process is not None and _ollama_process.poll() is None:
        _ollama_process.terminate()
        try:
            _ollama_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _ollama_process.kill()
    _ollama_process = None


async def delete_solar() -> dict[str, Any]:
    """Delete the legacy Solar model without touching other Ollama models."""
    try:
        _stop_managed_ollama()
        if shutil.which("ollama") is None:
            _state.update({"status": "not_configured", "message": "Legacy Solar is not installed."})
            return await solar_status()
        code, output = await asyncio.to_thread(_run_logged, ["ollama", "rm", SOLAR_MODEL])
        if code != 0 and "not found" not in output.lower():
            raise RuntimeError(output.strip()[-700:] or f"Could not remove {SOLAR_MODEL}.")
        _state.update({"status": "not_configured", "message": "Legacy Solar was removed from this VM."})
    except Exception as exc:  # surfaced through the status endpoint
        _state.update({"status": "error", "message": str(exc) or "Solar cleanup failed."})
    return await solar_status()
