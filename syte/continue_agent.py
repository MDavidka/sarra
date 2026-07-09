"""Continue agent runtime management for Syte workspaces."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from syte.config import settings
from syte.database import get_project, get_setting, list_projects, update_project
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.workspace import ensure_workspace, read_env_vars, workspace_path

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def agent_pid_file(project_id: str) -> Path:
    path = settings.data_dir / "pids"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{project_id}.continue.pid"


def agent_root(project_id: str) -> Path:
    root = ensure_workspace(project_id) / "data" / "continue"
    root.mkdir(parents=True, exist_ok=True)
    return root


def agent_home(project_id: str) -> Path:
    home = agent_root(project_id) / "home"
    (home / ".continue").mkdir(parents=True, exist_ok=True)
    return home


def agent_config_path(project_id: str) -> Path:
    return agent_root(project_id) / "config.yaml"


def agent_log_path(project_id: str) -> Path:
    return agent_root(project_id) / "serve.log"


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def continue_command() -> str:
    return shutil.which("cn") or "cn"


def continue_installed() -> bool:
    return shutil.which("cn") is not None


async def next_agent_port() -> int:
    projects = await list_projects()
    used = {p.get("agent_port") for p in projects if p.get("agent_port")}
    for port in range(settings.continue_port_start, settings.continue_port_end + 1):
        if port not in used:
            return port
    raise RuntimeError(
        f"No Continue agent ports available ({settings.continue_port_start}-{settings.continue_port_end} exhausted)"
    )


async def bridge_settings() -> dict[str, str]:
    bridge_url = (await get_setting("continue_bridge_api_base", "")).strip()
    api_key = (await get_setting("continue_bridge_api_key", "")).strip()
    default_profile = (await get_setting("continue_default_model_profile", "syra-base")).strip() or "syra-base"
    provider = (await get_setting("continue_provider", "openai")).strip() or "openai"
    return {
        "api_base": bridge_url.rstrip("/"),
        "api_key": api_key,
        "default_profile": default_profile,
        "provider": provider,
        "syra_nano_model": (await get_setting("continue_syra_nano_model", "gemini-2.5-flash")).strip() or "gemini-2.5-flash",
        "syra_base_model": (await get_setting("continue_syra_base_model", "deepseek-chat")).strip() or "deepseek-chat",
        "syra_havy_model": (await get_setting("continue_syra_havy_model", "gemini-2.5-pro")).strip() or "gemini-2.5-pro",
    }


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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


def agent_local_url(port: int | None) -> str:
    return f"http://127.0.0.1:{int(port)}" if port else ""


async def ensure_agent_runtime(project: dict) -> dict:
    updates: dict[str, Any] = {}
    if not project.get("agent_port"):
        updates["agent_port"] = await next_agent_port()
    if not project.get("agent_status"):
        updates["agent_status"] = "stopped"
    if not project.get("agent_runtime"):
        updates["agent_runtime"] = "project"
    if not project.get("agent_model_profile"):
        bridge = await bridge_settings()
        updates["agent_model_profile"] = bridge["default_profile"]
    if updates:
        await update_project(project["id"], updates)
        project = await get_project(project["id"]) or {**project, **updates}
    return project


async def selected_model_metadata(project: dict) -> dict[str, str]:
    bridge = await bridge_settings()
    profile = (project.get("agent_model_profile") or bridge["default_profile"] or "syra-base").strip()
    model_map = {
        "syra-nano": bridge["syra_nano_model"],
        "syra-base": bridge["syra_base_model"],
        "syra-havy": bridge["syra_havy_model"],
    }
    return {
        "profile": profile,
        "provider": bridge["provider"],
        "model": model_map.get(profile, bridge["syra_base_model"]),
        "api_base": bridge["api_base"],
    }


async def write_agent_config(project: dict) -> Path:
    project = await ensure_agent_runtime(project)
    bridge = await bridge_settings()
    config_path = agent_config_path(project["id"])
    model_data = await selected_model_metadata(project)
    api_base = bridge["api_base"]

    models = [
        ("syra-nano", bridge["syra_nano_model"]),
        ("syra-base", bridge["syra_base_model"]),
        ("syra-havy", bridge["syra_havy_model"]),
    ]
    ordered = sorted(models, key=lambda item: item[0] != model_data["profile"])
    lines = [
        "name: Syte Continue",
        "version: 1.0.0",
        "schema: v1",
        "models:",
    ]
    for alias, model_name in ordered:
        lines.extend([
            f"  - name: {_yaml_quote(alias)}",
            f"    provider: {_yaml_quote(bridge['provider'])}",
            f"    model: {_yaml_quote(model_name)}",
            f"    apiBase: {_yaml_quote(api_base)}",
            '    apiKey: "${{ secrets.SYRA_BRIDGE_API_KEY }}"',
            "    roles:",
            "      - chat",
            "      - edit",
            "      - apply",
        ])
    config_path.write_text("\n".join(lines) + "\n")
    return config_path


async def backend_health(project: dict) -> dict[str, Any]:
    model = await selected_model_metadata(project)
    api_base = model["api_base"]
    if not api_base:
        return {
            "ok": False,
            "error": "continue_bridge_api_base not configured",
            "url": None,
        }
    headers = {}
    bridge = await bridge_settings()
    if bridge["api_key"]:
        headers["Authorization"] = f"Bearer {bridge['api_key']}"
    url = api_base.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers=headers)
        return {
            "ok": response.status_code < 500,
            "status_code": response.status_code,
            "url": url,
            "error": "" if response.status_code < 500 else f"Upstream returned {response.status_code}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "error": str(exc),
        }


async def probe_agent_http(port: int | None) -> dict[str, Any]:
    if not port:
        return {"ok": False, "url": None, "status_code": None}
    base = agent_local_url(port)
    async with httpx.AsyncClient(timeout=3.0) as client:
        for path in ("/health", "/", "/docs"):
            try:
                response = await client.get(base + path)
                if response.status_code < 500:
                    return {"ok": True, "url": base + path, "status_code": response.status_code}
            except Exception:
                continue
    return {"ok": _port_listening(int(port)), "url": base, "status_code": None}


def get_agent_logs(project_id: str, lines: int = 200) -> str:
    log_path = agent_log_path(project_id)
    if not log_path.exists():
        return "No Continue agent logs yet."
    content = log_path.read_text(errors="replace").splitlines()
    return "\n".join(content[-lines:])


async def stop_agent(project_id: str) -> tuple[bool, str]:
    pf = agent_pid_file(project_id)
    if not pf.exists():
        await update_project(project_id, {"agent_status": "stopped"})
        return True, "Continue agent already stopped."
    try:
        pid = int(pf.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    pf.unlink(missing_ok=True)
    await update_project(project_id, {"agent_status": "stopped"})
    return True, "Continue agent stopped."


async def start_agent(project_id: str) -> tuple[bool, str, dict]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found", {}
    project = await ensure_agent_runtime(project)
    port = int(project["agent_port"])

    if is_agent_running(project_id) and _port_listening(port):
        status = await get_agent_status(project_id)
        return True, "Continue agent already running.", status

    if not continue_installed():
        message = 'Continue CLI not installed. Install with: npm install -g @continuedev/cli'
        await update_project(project_id, {"agent_status": "error", "agent_last_error": message})
        return False, message, {}

    await stop_agent(project_id)
    config_path = await write_agent_config(project)
    home = agent_home(project_id)
    log_path = agent_log_path(project_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log:
        log.write(f"\n=== Continue serve session {_now()} ===\n")
        log.write(f"Config: {config_path}\n")
        log.write(f"Port: {port}\n")

    bridge = await bridge_settings()
    env = {
        **os.environ,
        **read_env_vars(project.get("env_vars", "{}")),
        "HOME": str(home),
        "SYRA_BRIDGE_API_KEY": bridge["api_key"],
        "CONTINUE_GLOBAL_DIR": str(home / ".continue"),
        "CONTINUE_DISABLE_HUB": "1",
    }
    command = f'{continue_command()} serve --config "{config_path}" --host 127.0.0.1 --port {port}'

    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        command,
        cwd=workspace_path(project_id) / "app",
        shell=True,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    time.sleep(1.5)
    if proc.poll() is not None:
        log_file.close()
        error = get_agent_logs(project_id, 50)
        await update_project(
            project_id,
            {
                "agent_status": "error",
                "agent_last_error": error[-2000:],
            },
        )
        return False, "Continue agent exited during startup.", {}

    agent_pid_file(project_id).write_text(str(proc.pid))
    log_file.close()
    await update_project(
        project_id,
        {
            "agent_status": "running" if _port_listening(port) else "starting",
            "agent_last_started_at": _now(),
            "agent_last_error": "",
            "agent_config_path": str(config_path),
        },
    )
    status = await get_agent_status(project_id)
    return True, f"Continue agent started on port {port}.", status


async def restart_agent(project_id: str) -> tuple[bool, str, dict]:
    await stop_agent(project_id)
    return await start_agent(project_id)


async def get_agent_status(project_id: str, *, request_base: str = "") -> dict:
    project = await get_project(project_id)
    if not project:
        return {}
    project = await ensure_agent_runtime(project)
    port = project.get("agent_port")
    runtime_url = agent_local_url(port)
    healthy = await probe_agent_http(port)
    backend = await backend_health(project)
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    base_url = request_base.rstrip("/")
    if not base_url:
        base_url = build_https_url(gui_domain) if gui_domain else build_direct_url(settings.resolved_public_ip, settings.port)
    model = await selected_model_metadata(project)
    running = is_agent_running(project_id)
    status = project.get("agent_status") or ("running" if running else "stopped")
    if running and healthy["ok"]:
        status = "running"
    elif running:
        status = "starting"
    elif status not in ("error", "stopped"):
        status = "stopped"
    proxy_path = f"/api/internal/projects/{project_id}/agent/proxy"
    return {
        "agent_runtime": project.get("agent_runtime") or "project",
        "agent_status": status,
        "agent_running": running,
        "agent_healthy": healthy["ok"],
        "agent_port": port,
        "agent_local_url": runtime_url,
        "agent_proxy_path": proxy_path,
        "agent_proxy_url": base_url + proxy_path,
        "agent_workspace_path": str(workspace_path(project_id)),
        "agent_log_path": str(agent_log_path(project_id)),
        "agent_config_path": project.get("agent_config_path") or str(agent_config_path(project_id)),
        "agent_last_started_at": project.get("agent_last_started_at"),
        "agent_last_error": project.get("agent_last_error") or "",
        "agent_backend": backend,
        "agent_model": model,
        "agent_command": continue_command(),
        "agent_install_ok": continue_installed(),
        "agent_no_hub_required": True,
    }


async def update_agent_settings(
    project_id: str,
    *,
    model_profile: str | None = None,
) -> dict:
    project = await get_project(project_id)
    if not project:
        return {}
    updates: dict[str, Any] = {}
    if model_profile is not None:
        updates["agent_model_profile"] = model_profile.strip() or "syra-base"
    if updates:
        await update_project(project_id, updates)
    return await get_agent_status(project_id)


def _extract_assistant_reply(state: dict) -> str:
    history = (state.get("session") or {}).get("history") or []
    assistant_items = [item for item in history if (item.get("message") or {}).get("role") == "assistant"]
    if not assistant_items:
        return ""
    last = assistant_items[-1].get("message") or {}
    content = last.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


async def _poll_agent_state(port: int, *, timeout_s: float = 90.0) -> dict:
    base = agent_local_url(port).rstrip("/")
    deadline = time.time() + timeout_s
    last_state: dict = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.time() < deadline:
            try:
                response = await client.get(f"{base}/state")
                if response.status_code >= 400:
                    await asyncio.sleep(0.5)
                    continue
                last_state = response.json()
                busy = last_state.get("isProcessing") or last_state.get("is_processing")
                if not busy:
                    return last_state
            except Exception:
                pass
            await asyncio.sleep(0.4)
    return last_state


async def communicate_with_agent(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    source: str = "api",
    auto_start: bool = True,
) -> dict:
    from syte.agent_metrics import log_agent_request

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    if model_profile:
        await update_agent_settings(project_id, model_profile=model_profile)

    status = await get_agent_status(project_id)
    if not status.get("agent_running"):
        if not auto_start:
            err = "Continue agent is not running"
            await log_agent_request(project_id, source=source, model_profile=model_profile, message=message, status="error", error=err)
            return {"ok": False, "error": "agent_not_running", "message": err}
        ok, start_msg, status = await start_agent(project_id)
        if not ok:
            await log_agent_request(project_id, source=source, model_profile=model_profile, message=message, status="error", error=start_msg)
            return {"ok": False, "error": "agent_start_failed", "message": start_msg}

    port = status.get("agent_port")
    if not port:
        err = "Agent has no allocated port"
        await log_agent_request(project_id, source=source, model_profile=model_profile, message=message, status="error", error=err)
        return {"ok": False, "error": "agent_no_port", "message": err}

    base = agent_local_url(int(port)).rstrip("/")
    model = status.get("agent_model") or {}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base}/message",
                json={"message": message},
                headers={"Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                err = f"Agent returned HTTP {response.status_code}"
                await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="error", error=err)
                return {"ok": False, "error": "agent_http_error", "message": err, "status_code": response.status_code}

        state = await _poll_agent_state(int(port))
        reply = _extract_assistant_reply(state)
        await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="ok")
        return {
            "ok": True,
            "uuid": project_id,
            "model_profile": model.get("profile"),
            "model": model.get("model"),
            "provider": model.get("provider"),
            "message": message,
            "reply": reply,
            "state": state,
        }
    except Exception as exc:
        err = str(exc)
        await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="error", error=err)
        return {"ok": False, "error": "agent_communicate_failed", "message": err}


async def test_agent(project_id: str, *, source: str = "api") -> dict:
    from syte.agent_metrics import log_agent_request

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    status = await get_agent_status(project_id)
    backend = status.get("agent_backend") or {}
    install_ok = status.get("agent_install_ok", continue_installed())

    if not install_ok:
        return {
            "ok": False,
            "error": "cli_not_installed",
            "message": "Continue CLI not installed. Install: npm install -g @continuedev/cli",
            "checks": {"cli": False, "backend": backend.get("ok", False), "agent": False},
        }

    if not backend.get("ok"):
        await log_agent_request(project_id, source=source, status="error", error=backend.get("error") or "backend_unreachable")
        return {
            "ok": False,
            "error": "backend_unreachable",
            "message": backend.get("error") or "Bridge API unreachable",
            "checks": {"cli": True, "backend": False, "agent": status.get("agent_running", False)},
            "backend": backend,
        }

    if not status.get("agent_running"):
        ok, start_msg, status = await start_agent(project_id)
        if not ok:
            await log_agent_request(project_id, source=source, status="error", error=start_msg)
            return {
                "ok": False,
                "error": "agent_start_failed",
                "message": start_msg,
                "checks": {"cli": True, "backend": True, "agent": False},
            }

    result = await communicate_with_agent(
        project_id,
        "Reply with exactly the word 'ok' and nothing else.",
        source=source,
        auto_start=False,
    )
    passed = result.get("ok") and "ok" in (result.get("reply") or "").lower()
    return {
        "ok": passed,
        "error": None if passed else (result.get("error") or "test_reply_invalid"),
        "message": "Agent test passed" if passed else (result.get("message") or "Agent did not return expected reply"),
        "checks": {"cli": True, "backend": True, "agent": True, "communicate": result.get("ok", False)},
        "reply": result.get("reply", ""),
        "model": result.get("model"),
        "provider": result.get("provider"),
    }

