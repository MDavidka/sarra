import os
import signal
import subprocess
from pathlib import Path

from syte.config import settings
from syte.docker_deploy import (
    container_name,
    deploy_docker,
    find_dockerfile,
    is_docker_running,
    rebuild_docker,
    stop_docker,
)
from syte.workspace import ensure_workspace, read_env_vars, workspace_path

PID_DIR = settings.data_dir / "pids"
PID_DIR.mkdir(parents=True, exist_ok=True)


def pid_file(project_id: str) -> Path:
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
    pid_file(project_id).write_text(str(proc.pid))
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
    if deploy_type == "docker":
        from syte.workspace import run_cmd
        name = container_name(project_id)
        code, out = run_cmd(["docker", "logs", "--tail", str(lines), name])
        return out if code == 0 else "No docker logs yet."
    log_path = workspace_path(project_id) / "app.log"
    if not log_path.exists():
        return "No logs yet."
    content = log_path.read_text().splitlines()
    return "\n".join(content[-lines:])
