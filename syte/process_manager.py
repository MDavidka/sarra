import os
import signal
import subprocess
from pathlib import Path

from syte.config import settings
from syte.workspace import ensure_workspace, read_env_vars, workspace_path

PID_DIR = settings.data_dir / "pids"
PID_DIR.mkdir(parents=True, exist_ok=True)


def pid_file(project_id: str) -> Path:
    return PID_DIR / f"{project_id}.pid"


def is_running(project_id: str) -> bool:
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


def stop_project(project_id: str) -> tuple[bool, str]:
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


def start_project(project_id: str, port: int, start_command: str, env_vars_raw: str | dict) -> tuple[bool, str]:
    if is_running(project_id):
        stop_project(project_id)

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


def get_logs(project_id: str, lines: int = 100) -> str:
    log_path = workspace_path(project_id) / "app.log"
    if not log_path.exists():
        return "No logs yet."
    content = log_path.read_text().splitlines()
    return "\n".join(content[-lines:])
