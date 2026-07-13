import os
import signal
import subprocess
import time
import shutil
from pathlib import Path

from syte.config import settings
from syte.docker_deploy import (
    container_name,
    deploy_docker,
    docker_container_exists,
    find_dockerfile,
    is_docker_running,
    rebuild_docker,
    stop_docker,
)
from syte.runtime import ensure_runtime_for_command
from syte.workspace import ensure_workspace, read_env_vars, workspace_path

PID_DIR = settings.data_dir / "pids"


def _ensure_pid_dir() -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)


def validate_shell_command(start_command: str) -> str | None:
    """Return error message if command cannot run on this host."""
    if not start_command or not start_command.strip():
        return "No start command configured."
    cmd = start_command.lower()
    if "npm" in cmd and not shutil.which("npm"):
        return (
            "npm is not installed on this server. "
            "Run: sudo apt install -y nodejs npm — or deploy a repo with a Dockerfile."
        )
    if "yarn" in cmd and not shutil.which("yarn"):
        return "yarn is not installed on this server."
    if "pnpm" in cmd and not shutil.which("pnpm"):
        return "pnpm is not installed on this server."
    return None


def pid_file(project_id: str) -> Path:
    _ensure_pid_dir()
    return PID_DIR / f"{project_id}.pid"


def is_running(project_id: str, deploy_type: str = "shell") -> bool:
    if deploy_type == "docker":
        return is_docker_running(project_id)
    pf = pid_file(project_id)
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        pf.unlink(missing_ok=True)
        return False


def stop_project(project_id: str, deploy_type: str = "shell") -> tuple[bool, str]:
    if deploy_type == "docker":
        return stop_docker(project_id)
    pf = pid_file(project_id)
    if not pf.exists():
        return True, "Already stopped."
    try:
        pid = int(pf.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        pf.unlink(missing_ok=True)
        return True, f"Stopped process {pid}."
    except (OSError, ValueError) as e:
        pf.unlink(missing_ok=True)
        return True, f"Process not running ({e})."


def start_project(
    project_id: str,
    port: int,
    start_command: str,
    env_vars_raw: str | dict,
    deploy_type: str = "shell",
    dockerfile_path: str | None = None,
) -> tuple[bool, str]:
    if is_running(project_id, deploy_type):
        stop_project(project_id, deploy_type)

    if deploy_type == "docker":
        repo = workspace_path(project_id) / "app"
        dockerfile = find_dockerfile(project_id)
        if dockerfile_path:
            candidate = repo / dockerfile_path
            if candidate.is_file():
                dockerfile = candidate
        if not dockerfile:
            return False, "Dockerfile not found in workspace."
        return deploy_docker(project_id, port, dockerfile, env_vars_raw)

    err = validate_shell_command(start_command)
    if err:
        return False, err

    ok, install_msg = ensure_runtime_for_command(start_command)
    if not ok:
        return False, install_msg

    ws = ensure_workspace(project_id)
    repo = ws / "app"
    if not repo.exists():
        repo.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, **read_env_vars(env_vars_raw)}
    env["PORT"] = str(port)
    env["SYTE_DATA_DIR"] = str(ws / "data")

    log_path = ws / "app.log"
    log_file = open(log_path, "a")

    proc = subprocess.Popen(
        start_command,
        cwd=repo,
        shell=True,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    time.sleep(1)
    if proc.poll() is not None:
        log_file.close()
        pid_file(project_id).unlink(missing_ok=True)
        tail = ""
        if log_path.exists():
            lines = log_path.read_text().splitlines()
            tail = "\n".join(lines[-8:])
        return False, f"Process exited immediately.\n{tail}"

    pid_file(project_id).write_text(str(proc.pid))
    log_file.close()
    return True, f"Started on port {port} (PID {proc.pid}). Logs: {log_path}"


def restart_docker_project(
    project_id: str,
    port: int,
    env_vars_raw: str | dict,
    dockerfile_path: str | None = None,
) -> tuple[bool, str]:
    repo = workspace_path(project_id) / "app"
    dockerfile = find_dockerfile(project_id)
    if dockerfile_path:
        candidate = repo / dockerfile_path
        if candidate.is_file():
            dockerfile = candidate
    if not dockerfile:
        return False, "Dockerfile not found after git pull."
    return rebuild_docker(project_id, port, dockerfile, env_vars_raw)


def get_logs(project_id: str, lines: int = 100, deploy_type: str = "shell") -> str:
    from syte.workspace import deploy_log_path

    parts: list[str] = []
    deploy_log = deploy_log_path(project_id)
    if deploy_log.exists():
        content = deploy_log.read_text(errors="replace").splitlines()
        if content:
            tail = content[-max(lines * 3, 300):]
            parts.append("=== Deploy log ===\n" + "\n".join(tail))

    if deploy_type == "docker":
        from syte.docker_deploy import _build_log_path
        from syte.workspace import run_cmd

        build_log = _build_log_path(project_id)
        if build_log.exists():
            content = build_log.read_text(errors="replace").splitlines()
            if content:
                tail = content[-max(lines * 5, 500):]
                parts.append("=== Build log ===\n" + "\n".join(tail))

        if docker_container_exists(project_id):
            name = container_name(project_id)
            code, out = run_cmd(["docker", "logs", "--tail", str(lines), name])
            if code == 0 and out.strip():
                parts.append("=== Container log ===\n" + out.strip())
        elif build_log.exists():
            parts.append(
                "=== Container ===\n"
                "No container yet — docker build may still be running or failed. "
                "Check the build log above for npm/next errors (container is only created after a successful build)."
            )

        return "\n\n".join(parts) if parts else "No logs yet."

    log_path = workspace_path(project_id) / "app.log"
    if log_path.exists():
        content = log_path.read_text(errors="replace").splitlines()
        if content:
            parts.append("=== App log ===\n" + "\n".join(content[-lines:]))

    return "\n\n".join(parts) if parts else "No logs yet."
