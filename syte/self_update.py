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
from syte.update_source import UpdateTarget, resolve_update_target
from syte.workspace import run_cmd

INSTALL_DIR = Path(__file__).resolve().parent.parent
UPDATE_LOG = settings.data_dir / "update.log"
UPDATE_WORK_BRANCH = "syte-update"


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in re.split(r"[.\-]", version.strip().lstrip("v")):
        if piece.isdigit():
            parts.append(int(piece))
    return tuple(parts) if parts else (0,)


def _version_lt(left: str, right: str) -> bool:
    return _version_tuple(left) < _version_tuple(right)


def _allow_downgrade() -> bool:
    return (os.environ.get("SYTE_UPDATE_ALLOW_DOWNGRADE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _venv_python() -> str:
    venv_python = INSTALL_DIR / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _venv_pip() -> str | None:
    venv_pip = INSTALL_DIR / ".venv" / "bin" / "pip"
    return str(venv_pip) if venv_pip.exists() else None


def _update_target() -> UpdateTarget:
    return resolve_update_target(INSTALL_DIR)


def get_update_info() -> dict:
    target = _update_target()
    installed = _read_installed_version()
    return {
        "installed_version": installed,
        "running_version": __version__,
        "work_branch": UPDATE_WORK_BRANCH,
        "bootstrap_commands": bootstrap_update_commands(target),
        **target.as_dict(),
    }


def _current_branch() -> str:
    code, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=INSTALL_DIR)
    branch = out.strip() if code == 0 else ""
    if branch in ("", "HEAD"):
        return ""
    return branch


def _working_tree_dirty() -> bool:
    code, out = run_cmd(["git", "status", "--porcelain"], cwd=INSTALL_DIR)
    return code == 0 and bool(out.strip())


def _read_version_at_ref(ref: str) -> str:
    code, out = run_cmd(["git", "show", f"{ref}:syte/__init__.py"], cwd=INSTALL_DIR)
    if code != 0:
        return ""
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', out)
    return match.group(1) if match else ""


def _read_installed_version() -> str:
    init_py = INSTALL_DIR / "syte" / "__init__.py"
    if not init_py.exists():
        return "unknown"
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_py.read_text())
    return match.group(1) if match else "unknown"


def _append_update_log(text: str) -> None:
    try:
        UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with UPDATE_LOG.open("a") as log_file:
            log_file.write(text.rstrip() + "\n")
    except OSError:
        pass


def _git_checkout_ref(local_branch: str, start_point: str) -> tuple[bool, str]:
    code, out = run_cmd(["git", "checkout", "-B", local_branch, start_point], cwd=INSTALL_DIR)
    if code == 0:
        return True, out or f"Checked out {local_branch} from {start_point}."
    return False, out or f"Could not checkout {local_branch} from {start_point}."


def _git_fetch_branch(branch: str) -> tuple[bool, str]:
    messages: list[str] = []
    code, out = run_cmd(
        ["git", "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
        cwd=INSTALL_DIR,
    )
    messages.append(out or f"Fetched refs/heads/{branch}.")
    if code == 0:
        return True, "\n".join(messages)

    code, out = run_cmd(["git", "fetch", "origin"], cwd=INSTALL_DIR)
    messages.append(out or "Fetched all refs from origin.")
    if code != 0:
        return False, "\n".join(messages)

    code, out = run_cmd(["git", "fetch", "origin", branch], cwd=INSTALL_DIR)
    messages.append(out or f"Fetched origin {branch}.")
    return code == 0, "\n".join(messages)


def _git_ref_exists(ref: str) -> bool:
    code, _ = run_cmd(["git", "rev-parse", "--verify", ref], cwd=INSTALL_DIR)
    return code == 0


def _git_fetch_pr(pr_number: int) -> tuple[bool, str, str]:
    local_ref = f"syte-pr-{pr_number}"
    messages: list[str] = []

    code, out = run_cmd(
        ["git", "fetch", "origin", f"pull/{pr_number}/head:{local_ref}"],
        cwd=INSTALL_DIR,
    )
    messages.append(out or f"git fetch origin pull/{pr_number}/head:{local_ref}")
    if code == 0 and _git_ref_exists(local_ref):
        return True, "\n".join(messages), local_ref

    code, out = run_cmd(["git", "fetch", "origin", f"pull/{pr_number}/head"], cwd=INSTALL_DIR)
    messages.append(out or f"git fetch origin pull/{pr_number}/head")
    if code == 0 and _git_ref_exists("FETCH_HEAD"):
        pin_code, pin_out = run_cmd(
            ["git", "branch", "-f", local_ref, "FETCH_HEAD"],
            cwd=INSTALL_DIR,
        )
        messages.append(pin_out or f"Pinned FETCH_HEAD to {local_ref}.")
        if pin_code == 0 and _git_ref_exists(local_ref):
            return True, "\n".join(messages), local_ref

    return False, "\n".join(messages), local_ref


def bootstrap_update_commands(target: UpdateTarget) -> list[str]:
    install = str(INSTALL_DIR)
    if target.pr_number:
        ref = f"syte-pr-{target.pr_number}"
        return [
            f"cd {install}",
            f"git fetch origin pull/{target.pr_number}/head:{ref}",
            f"git checkout -B {UPDATE_WORK_BRANCH} {ref}",
            f"{install}/.venv/bin/pip install -r {install}/requirements.txt -q",
            "sudo systemctl restart syte",
        ]
    return [
        f"cd {install}",
        f"git fetch origin +refs/heads/{target.branch}:refs/remotes/origin/{target.branch}",
        f"git checkout -B {UPDATE_WORK_BRANCH} origin/{target.branch}",
        f"{install}/.venv/bin/pip install -r {install}/requirements.txt -q",
        "sudo systemctl restart syte",
    ]


def _git_sync_update_target(target: UpdateTarget) -> tuple[bool, str, str]:
    messages: list[str] = []

    if _working_tree_dirty():
        code, out = run_cmd(
            ["git", "stash", "push", "-u", "-m", "syte-auto-stash-before-update"],
            cwd=INSTALL_DIR,
        )
        if code == 0:
            messages.append(out or "Stashed local changes before update.")
        else:
            messages.append(out or "Warning: could not stash local changes.")

    checkout_point = ""
    if target.source_type == "pr" and target.pr_number:
        ok, fetch_msg, local_ref = _git_fetch_pr(target.pr_number)
        messages.append(fetch_msg)
        if ok:
            checkout_point = local_ref
        elif target.branch:
            messages.append(f"PR fetch failed — trying branch {target.branch}.")
            ok, fetch_msg = _git_fetch_branch(target.branch)
            messages.append(fetch_msg)
            if not ok:
                return False, "\n".join(messages), ""
            checkout_point = f"origin/{target.branch}"
        else:
            return False, "\n".join(messages), ""
    else:
        ok, fetch_msg = _git_fetch_branch(target.branch)
        messages.append(fetch_msg)
        if not ok:
            return False, "\n".join(messages), ""
        checkout_point = f"origin/{target.branch}"

    target_version = _read_version_at_ref(checkout_point)
    if target_version and not _allow_downgrade():
        installed = _read_installed_version()
        if installed != "unknown" and _version_lt(target_version, installed):
            return (
                False,
                "\n".join(
                    messages
                    + [
                        f"Refusing downgrade: installed {installed}, target {target_version} ({target.label}).",
                        "Close older open PRs or set SYTE_UPDATE_PR to the correct PR number.",
                    ]
                ),
                checkout_point,
            )

    ok, checkout_msg = _git_checkout_ref(UPDATE_WORK_BRANCH, checkout_point)
    messages.append(checkout_msg)
    if not ok:
        return False, "\n".join(messages), checkout_point

    if checkout_point.startswith("origin/"):
        code, out = run_cmd(["git", "reset", "--hard", checkout_point], cwd=INSTALL_DIR)
        if code != 0:
            messages.append(out or "git reset --hard failed.")
            return False, "\n".join(messages), checkout_point
        messages.append(out or f"Reset to {checkout_point}.")

    return True, "\n".join(messages), checkout_point


def _git_pull_latest(branch: str) -> tuple[bool, str]:
    """Backward-compatible wrapper for branch-only updates."""
    target = UpdateTarget(source_type="branch", branch=branch, label=f"branch {branch}")
    ok, msg, _ref = _git_sync_update_target(target)
    return ok, msg


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
    _append_update_log("==> apply_and_restart worker started")
    time.sleep(2)
    ok, msg = restart_syte()
    _append_update_log(msg if ok else f"RESTART FAILED: {msg}")


def _schedule_restart() -> None:
    """Spawn a detached Python worker — no bash scripts required."""
    env = os.environ.copy()
    env["SYTE_DATA_DIR"] = str(settings.data_dir)
    env["SYTE_WORKSPACES_DIR"] = str(settings.resolved_workspaces_dir)
    env["SYTE_DB_PATH"] = str(settings.resolved_db_path)
    env["PYTHONPATH"] = str(INSTALL_DIR)

    log_handle = open(UPDATE_LOG, "a")
    log_handle.write("\n==> scheduling restart worker\n")
    log_handle.flush()

    subprocess.Popen(
        [_venv_python(), "-m", "syte.self_update", "--apply-and-restart"],
        cwd=INSTALL_DIR,
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )


def _ensure_requirements_installed() -> tuple[bool, str]:
    """Install requirements into .venv before importing heavy deps (manual update.sh safety)."""
    req = INSTALL_DIR / "requirements.txt"
    if not req.exists():
        return True, ""
    pip = _venv_pip()
    if pip:
        code, out = run_cmd([pip, "install", "-r", str(req), "-q"], cwd=INSTALL_DIR)
        return code == 0, out or "Dependencies updated."
    code, out = run_cmd(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
        cwd=INSTALL_DIR,
    )
    return code == 0, out or "Dependencies updated."


def update_syte() -> tuple[bool, str]:
    """Pull newest Syte from git (latest open PR by default), refresh deps, and schedule restart."""
    target = _update_target()
    branch = target.branch
    before_version = _read_installed_version()
    messages = [
        f"Current version: {__version__}",
        f"Update source: {target.label}",
        f"Update branch: {branch}",
    ]
    _append_update_log(
        f"\n=== Syte update requested (running {__version__}, source {target.label}, branch {branch}) ==="
    )

    if not (INSTALL_DIR / ".git").exists():
        return False, "Syte install is not a git repository. Cannot pull updates."

    current = _current_branch()
    if current and current != UPDATE_WORK_BRANCH:
        messages.append(f"Note: was on {current}, switching to {UPDATE_WORK_BRANCH} for update.")

    ok, pull_msg, _checkout_ref = _git_sync_update_target(target)
    messages.append(pull_msg)
    if not ok:
        _append_update_log(pull_msg)
        messages.append("Automatic update failed. Run these commands on the server (SSH):")
        messages.extend(bootstrap_update_commands(target))
        return False, "\n".join(messages)

    after_version = _read_installed_version()
    messages.append(f"Code on disk: {after_version}")
    if after_version == before_version:
        messages.append(f"Already up to date (no new commits on {target.label}).")
    else:
        messages.append(f"Updated {before_version} → {after_version}")

    req = INSTALL_DIR / "requirements.txt"
    if req.exists():
        ok_deps, dep_msg = _ensure_requirements_installed()
        messages.append(dep_msg)
        if not ok_deps:
            return False, "\n".join(messages)

    _schedule_restart()
    messages.append("Syte will restart automatically to apply changes.")
    messages.append(f"Update log: {UPDATE_LOG}")
    result = "\n".join(messages)
    _append_update_log(result)
    return True, result


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--apply-and-restart":
        apply_and_restart()
