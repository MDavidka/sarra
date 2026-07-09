"""Long-lived Continue cloud agent (`cn serve`) per project workspace."""

import os
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from syte.agent_config import DEFAULT_MODEL, SYRA_MODELS, write_continue_config
from syte.config import settings
from syte.database import get_project, list_projects, update_project
from syte.workspace import command_exists, ensure_workspace, workspace_path

AGENT_PORT_START = 5000
AGENT_PORT_END = 5999
AGENT_START_GRACE_SEC = 60
AGENT_IDLE_TIMEOUT_SEC = 86400
PID_DIR = settings.data_dir / "pids"


def agent_pid_file(project_id: str) -> Path:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    return PID_DIR / f"{project_id}.agent.pid"


def agent_log_path(project_id: str) -> Path:
    return workspace_path(project_id) / "agent.log"


def agent_config_path(project_id: str) -> Path:
    return workspace_path(project_id) / ".continue" / "config.yaml"


async def next_agent_port() -> int:
    projects = await list_projects()
    used = {p.get("agent_port") for p in projects if p.get("agent_port")}
    for port in range(AGENT_PORT_START, AGENT_PORT_END + 1):
        if port not in used:
            return port
    raise RuntimeError("No agent ports available (5000-5999 exhausted)")


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _resolve_cn_binary() -> str | None:
    for name in ("cn", "continue"):
        if command_exists(name):
            return name
    return None


def is_agent_running(project_id: str) -> bool:
    pf = agent_pid_file(project_id)
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        pf.unlink(missing_ok=True)
        return False


def stop_agent(project_id: str) -> tuple[bool, str]:
    pf = agent_pid_file(project_id)
    if not pf.exists():
        return True, "Agent not running."
    try:
        pid = int(pf.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    pf.unlink(missing_ok=True)
    return True, "Agent stopped."


async def stop_agent_async(project_id: str) -> tuple[bool, str]:
    stop_agent(project_id)
    await update_project(project_id, {
        "agent_status": "stopped",
        "agent_error": None,
    })
    return True, "Agent stopped."


def get_agent_logs(project_id: str, lines: int = 200) -> str:
    log_path = agent_log_path(project_id)
    if not log_path.exists():
        return "No agent logs yet."
    content = log_path.read_text(errors="replace").splitlines()
    return "\n".join(content[-lines:])


async def check_bridge_reachable() -> tuple[bool, str]:
    """Probe Sycord OpenAI-compatible bridge (models list or health)."""
    from syte.agent_config import bridge_settings

    bridge_url, secret = await bridge_settings()
    if not bridge_url:
        return False, "sycord_ai_bridge_url not configured"
    headers = {}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
        headers["X-Sycord-Bridge-Secret"] = secret
    url = f"{bridge_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code < 500:
                return True, f"bridge reachable ({resp.status_code})"
            return False, f"bridge error HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"bridge unreachable: {exc}"


def _agent_start_grace_elapsed(project: dict) -> bool:
    raw = project.get("agent_started_at") or project.get("updated_at") or ""
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() > AGENT_START_GRACE_SEC
    except (ValueError, TypeError):
        return True


def agent_meta(project: dict, *, bridge_ok: bool | None = None, bridge_message: str = "") -> dict:
    agent_port = project.get("agent_port")
    running = is_agent_running(project["id"])
    ready = running and agent_port and _port_listening(int(agent_port))
    model = project.get("agent_model") or DEFAULT_MODEL
    pid = None
    pf = agent_pid_file(project["id"])
    if pf.exists():
        try:
            pid = int(pf.read_text().strip())
        except ValueError:
            pid = None

    proxy_base = f"/api/projects/{project['id']}/agent/proxy"
    return {
        "agent_running": running,
        "agent_ready": ready,
        "agent_port": agent_port,
        "agent_status": project.get("agent_status", "stopped"),
        "agent_model": model,
        "agent_models": list(SYRA_MODELS.keys()),
        "agent_started_at": project.get("agent_started_at"),
        "agent_error": project.get("agent_error"),
        "agent_pid": pid,
        "agent_local_url": f"http://127.0.0.1:{agent_port}" if agent_port else None,
        "agent_proxy_url": proxy_base,
        "agent_state_url": f"{proxy_base}/state",
        "agent_message_url": f"{proxy_base}/message",
        "agent_stream_url": f"/api/projects/{project['id']}/agent/logs/stream?live=1",
        "agent_config_path": str(agent_config_path(project["id"])),
        "agent_log_path": str(agent_log_path(project["id"])),
        "bridge_reachable": bridge_ok,
        "bridge_message": bridge_message,
    }


async def get_agent_status(project_id: str) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found"

    running = is_agent_running(project_id)
    status = project.get("agent_status", "stopped")
    agent_port = project.get("agent_port")

    if running and agent_port:
        port = int(agent_port)
        new_status = "running" if _port_listening(port) else "starting"
        if status != new_status:
            await update_project(project_id, {"agent_status": new_status})
            project = await get_project(project_id) or project
    elif not running and status != "stopped":
        port_up = agent_port and _port_listening(int(agent_port))
        if port_up:
            agent_pid_file(project_id).unlink(missing_ok=True)
            if status != "running":
                await update_project(project_id, {"agent_status": "running"})
                project = await get_project(project_id) or project
        elif status == "starting" and not _agent_start_grace_elapsed(project):
            pass
        else:
            err = project.get("agent_error") or "Agent process exited"
            await update_project(project_id, {
                "agent_status": "stopped",
                "agent_error": err if status != "stopped" else project.get("agent_error"),
            })
            project = await get_project(project_id) or project

    bridge_ok, bridge_msg = await check_bridge_reachable()
    return agent_meta(project, bridge_ok=bridge_ok, bridge_message=bridge_msg), "ok"


async def start_agent(
    project_id: str,
    *,
    model: str | None = None,
) -> tuple[bool, str, dict]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found", {}

    stop_agent(project_id)

    cn = _resolve_cn_binary()
    if not cn:
        return False, (
            "Continue CLI (`cn`) not found. Install: npm install -g @continuedev/cli "
            "or ensure `cn` is on PATH."
        ), {}

    ws = ensure_workspace(project_id)
    repo = ws / "app"
    repo.mkdir(parents=True, exist_ok=True)

    selected_model = model or project.get("agent_model") or DEFAULT_MODEL
    if selected_model not in SYRA_MODELS:
        selected_model = DEFAULT_MODEL

    cfg_path = await write_continue_config(project_id, ws, model=selected_model)

    agent_port = project.get("agent_port")
    if not agent_port:
        agent_port = await next_agent_port()

    log_path = agent_log_path(project_id)
    started_at = datetime.now(timezone.utc).isoformat()

    with log_path.open("a") as log_file:
        log_file.write(f"\n=== Agent session (port {agent_port}, model {selected_model}) ===\n")
        log_file.write(f"Config: {cfg_path}\n")
        log_file.write(f"Workspace: {repo}\n")

    command = (
        f"{cn} serve --port {agent_port} --config {cfg_path} "
        f"--timeout {AGENT_IDLE_TIMEOUT_SEC}"
    )

    from syte.agent_config import bridge_settings

    bridge_url, bridge_secret = await bridge_settings()
    env = {
        **os.environ,
        "SYCORD_BRIDGE_URL": bridge_url,
        "SYCORD_BRIDGE_SECRET": bridge_secret,
        "OPENAI_API_KEY": bridge_secret or "syte-local",
        "CONTINUE_GLOBAL_DIR": str(ws / ".continue"),
    }

    with log_path.open("a") as log_file:
        log_file.write(f"$ {command}\n")

    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        command,
        cwd=repo,
        shell=True,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    ready = False
    startup_error = ""
    for _ in range(120):
        time.sleep(0.25)
        if proc.poll() is not None:
            log_file.close()
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-20:])
            startup_error = f"Agent process exited.\n{tail}"
            await update_project(project_id, {
                "agent_status": "stopped",
                "agent_error": startup_error[:2000],
            })
            return False, startup_error, {}
        if _port_listening(int(agent_port)):
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"http://127.0.0.1:{agent_port}/state")
                    if resp.status_code == 200:
                        ready = True
                        break
            except httpx.HTTPError:
                pass

    agent_pid_file(project_id).write_text(str(proc.pid))
    log_file.close()

    status = "running" if ready else "starting"
    await update_project(project_id, {
        "agent_port": int(agent_port),
        "agent_status": status,
        "agent_model": selected_model,
        "agent_started_at": started_at,
        "agent_error": None,
    })

    project = await get_project(project_id) or project
    bridge_ok, bridge_msg = await check_bridge_reachable()
    meta = agent_meta(project, bridge_ok=bridge_ok, bridge_message=bridge_msg)
    msg = f"Continue agent on port {agent_port} (model {selected_model})"
    if ready:
        msg += " — ready"
    else:
        msg += " — starting (poll agent_status)"
    return True, msg, meta


async def restart_agent(project_id: str, *, model: str | None = None) -> tuple[bool, str, dict]:
    await stop_agent_async(project_id)
    return await start_agent(project_id, model=model)
