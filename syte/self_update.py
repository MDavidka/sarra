"""Self-update Syte from git — pull, refresh deps, apply Caddy config, restart."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from syte import __version__
from syte.config import settings
from syte.workspace import run_cmd

INSTALL_DIR = Path(__file__).resolve().parent.parent


def _venv_python() -> str:
    venv_python = INSTALL_DIR / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _venv_pip() -> str | None:
    venv_pip = INSTALL_DIR / ".venv" / "bin" / "pip"
    return str(venv_pip) if venv_pip.exists() else None


def _current_branch() -> str:
    code, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=INSTALL_DIR)
    return out.strip() if code == 0 else "main"


def _apply_proxy_sync() -> tuple[bool, str]:
    """Regenerate Caddy config from DB and reload Caddy (no shell script)."""
    sys.path.insert(0, str(INSTALL_DIR))
    from syte.certificates import apply_proxy_config

    return asyncio.run(apply_proxy_config())


def _port_listener_pid(port: int) -> int | None:
    code, out = run_cmd(["ss", "-tlnp"])
    if code != 0:
        return None
    for line in out.splitlines():
        if f":{port} " not in line:
            continue
        match = re.search(r"pid=(\d+)", line)
        if match:
            return int(match.group(1))
    return None


def _stop_port_listener(port: int) -> None:
    pid = _port_listener_pid(port)
    if not pid or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    time.sleep(2)
    try:
        os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _restart_via_systemd() -> tuple[bool, str]:
    run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["systemctl", "reset-failed", "syte"])
    for unit in ("syte", "syte.service"):
        code, out = run_cmd(["systemctl", "restart", unit])
        if code == 0:
            return True, out or f"Syte restarted via systemctl ({unit})."
    return False, "systemctl restart failed."


def _restart_via_uvicorn() -> tuple[bool, str]:
    """Fallback when systemd is unavailable — spawn a new uvicorn and stop the old listener."""
    port = settings.port
    _stop_port_listener(port)

    env = os.environ.copy()
    env["SYTE_DATA_DIR"] = str(settings.data_dir)
    env["SYTE_WORKSPACES_DIR"] = str(settings.resolved_workspaces_dir)
    env["SYTE_DB_PATH"] = str(settings.resolved_db_path)

    subprocess.Popen(
        [
            _venv_python(),
            "-m",
            "uvicorn",
            "syte.main:app",
            "--host",
            settings.host,
            "--port",
            str(port),
            "--app-dir",
            str(INSTALL_DIR),
        ],
        cwd=INSTALL_DIR,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True, f"Syte restarted on port {port}."


def restart_syte() -> tuple[bool, str]:
    """Apply Caddy proxy config and restart the Syte service."""
    ok, msg = _apply_proxy_sync()
    proxy_msg = msg if ok else f"Caddy config warning: {msg}"

    from shutil import which

    if which("systemctl"):
        restarted, restart_msg = _restart_via_systemd()
        if restarted:
            return True, f"{proxy_msg}\n{restart_msg}"

    restarted, restart_msg = _restart_via_uvicorn()
    if restarted:
        return True, f"{proxy_msg}\n{restart_msg}"
    return False, f"{proxy_msg}\n{restart_msg}"


def apply_and_restart() -> None:
    """Worker entrypoint: wait for HTTP response, then apply config and restart."""
    time.sleep(2)
    restart_syte()


def _schedule_restart() -> None:
    """Spawn a detached Python worker — no bash scripts required."""
    env = os.environ.copy()
    env["SYTE_DATA_DIR"] = str(settings.data_dir)
    env["SYTE_WORKSPACES_DIR"] = str(settings.resolved_workspaces_dir)
    env["SYTE_DB_PATH"] = str(settings.resolved_db_path)
    env["PYTHONPATH"] = str(INSTALL_DIR)

    subprocess.Popen(
        [_venv_python(), "-m", "syte.self_update", "--apply-and-restart"],
        cwd=INSTALL_DIR,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def update_syte() -> tuple[bool, str]:
    """Pull newest Syte from git, refresh dependencies, and schedule restart."""
    messages = [f"Current version: {__version__}"]

    if not (INSTALL_DIR / ".git").exists():
        return False, "Syte install is not a git repository. Cannot pull updates."

    branch = _current_branch()
    messages.append(f"Branch: {branch}")

    code, out = run_cmd(["git", "fetch", "origin"], cwd=INSTALL_DIR)
    messages.append(out or "Fetched origin.")
    if code != 0:
        return False, "\n".join(messages)

    code, out = run_cmd(
        ["git", "pull", "--ff-only", "origin", branch],
        cwd=INSTALL_DIR,
    )
    if code != 0:
        code, out = run_cmd(["git", "pull", "--ff-only"], cwd=INSTALL_DIR)
    messages.append(out or "Repository updated.")
    if code != 0:
        return False, "\n".join(messages)

    req = INSTALL_DIR / "requirements.txt"
    if req.exists():
        pip = _venv_pip()
        if pip:
            code, out = run_cmd([pip, "install", "-r", str(req), "-q"], cwd=INSTALL_DIR)
        else:
            code, out = run_cmd(
                [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
                cwd=INSTALL_DIR,
            )
        messages.append(out or "Dependencies updated.")
        if code != 0:
            return False, "\n".join(messages)

    _schedule_restart()
    messages.append("Syte will restart automatically to apply changes.")
    return True, "\n".join(messages)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--apply-and-restart":
        apply_and_restart()
