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

from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS, profile_provider
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


def build_serve_command(config_path: Path | str, port: int, *, timeout_s: int = 3600) -> str:
    """Build a Continue CLI serve command compatible with current cn flags."""
    config = str(config_path)
    return (
        f'{continue_command()} serve --config "{config}" '
        f"--port {int(port)} --timeout {int(timeout_s)} --auto"
    )


def write_agent_permissions(project_id: str) -> Path:
    """Write headless tool permissions so cn serve can run without prompts."""
    env_dir = agent_home(project_id) / ".continue"
    env_dir.mkdir(parents=True, exist_ok=True)
    permissions_path = env_dir / "permissions.yaml"
    permissions_path.write_text(
        "\n".join([
            "# Syte-managed permissions for headless cn serve",
            "allow:",
            '  - "*"',
            "ask: []",
            "exclude: []",
            "",
        ])
    )
    return permissions_path


async def next_agent_port() -> int:
    projects = await list_projects()
    used = {p.get("agent_port") for p in projects if p.get("agent_port")}
    for port in range(settings.continue_port_start, settings.continue_port_end + 1):
        if port not in used:
            return port
    raise RuntimeError(
        f"No Continue agent ports available ({settings.continue_port_start}-{settings.continue_port_end} exhausted)"
    )


async def profile_api_key(profile: str) -> str:
    spec = profile_provider(profile)
    return (await get_setting(spec["setting_key"], "")).strip()


async def bridge_settings() -> dict[str, Any]:
    default_profile = (await get_setting("continue_default_model_profile", "syra-base")).strip() or "syra-base"
    profiles: dict[str, dict[str, str]] = {}
    for name in PROFILE_ORDER:
        spec = PROFILE_PROVIDERS[name]
        profiles[name] = {
            "label": spec["label"],
            "provider": spec["provider"],
            "api_base": spec["api_base"],
            "model": spec["model"],
            "api_key": await profile_api_key(name),
            "secret_env": spec["secret_env"],
            "setting_key": spec["setting_key"],
        }
    active = profiles.get(default_profile, profiles["syra-base"])
    return {
        "default_profile": default_profile,
        "profiles": profiles,
        "api_base": active["api_base"],
        "api_key": active["api_key"],
        "provider": active["provider"],
        "syra_nano_model": profiles["syra-nano"]["model"],
        "syra_base_model": profiles["syra-base"]["model"],
        "syra_havy_model": profiles["syra-havy"]["model"],
        "syra_nano_api_key": profiles["syra-nano"]["api_key"],
        "syra_base_api_key": profiles["syra-base"]["api_key"],
        "syra_havy_api_key": profiles["syra-havy"]["api_key"],
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
    spec = bridge["profiles"].get(profile, bridge["profiles"]["syra-base"])
    return {
        "profile": profile,
        "provider": spec["provider"],
        "provider_label": spec["label"],
        "model": spec["model"],
        "api_base": spec["api_base"],
        "api_key": spec["api_key"],
    }


def _secret_ref(env_name: str) -> str:
    return "${{ secrets." + env_name + " }}"


async def write_agent_config(project: dict) -> Path:
    from syte.agent_skills import build_agent_rules, read_access_config, write_agent_skills

    project = await ensure_agent_runtime(project)
    bridge = await bridge_settings()
    config_path = agent_config_path(project["id"])
    model_data = await selected_model_metadata(project)
    active_profile = model_data["profile"]
    root = agent_root(project["id"])

    configured = [name for name in PROFILE_ORDER if bridge["profiles"][name]["api_key"]]
    if active_profile not in configured:
        raise RuntimeError(
            f"No API key configured for active profile {active_profile}. "
            f"Open AI settings and add the {bridge['profiles'][active_profile]['label']} key."
        )
    if not configured:
        raise RuntimeError("No model API keys configured. Open AI settings and add provider keys.")

    access_config = await read_access_config(project["id"], root)
    write_agent_skills(project["id"], root)

    ordered = sorted(configured, key=lambda name: name != active_profile)
    lines = [
        "name: Syte Continue",
        "version: 1.0.0",
        "schema: v1",
        "models:",
    ]
    for alias in ordered:
        spec = bridge["profiles"][alias]
        lines.extend([
            f"  - name: {_yaml_quote(alias)}",
            f"    provider: {_yaml_quote(spec['provider'])}",
            f"    model: {_yaml_quote(spec['model'])}",
            f"    apiBase: {_yaml_quote(spec['api_base'])}",
            f"    apiKey: {_yaml_quote(_secret_ref(spec['secret_env']))}",
            "    roles:",
            "      - chat",
            "      - edit",
            "      - apply",
        ])
    lines.append("rules:")
    for rule in build_agent_rules(project["id"], access_config):
        lines.append(f"  - name: {_yaml_quote(rule['name'])}")
        rule_text = rule["rule"].replace('"', '\\"')
        lines.append(f"    rule: {_yaml_quote(rule_text)}")

    enable_mcp = (await get_setting("continue_enable_mcp", "0")).strip().lower() in ("1", "true", "yes")
    if enable_mcp:
        from syte.agent_skills import mcp_server_config

        mcp = mcp_server_config(project["id"], root)
        mcp_bin = root / "bin" / "syte-mcp"
        lines.append("mcpServers:")
        lines.append(f"  - name: {_yaml_quote(mcp['name'])}")
        lines.append("    type: stdio")
        lines.append(f"    command: {_yaml_quote(str(mcp_bin))}")
        lines.append("    args: []")
        if mcp.get("env"):
            lines.append("    env:")
            for key, value in mcp["env"].items():
                lines.append(f"      {_yaml_quote(key)}: {_yaml_quote(str(value))}")

    config_path.write_text("\n".join(lines) + "\n")
    return config_path


def write_agent_secrets(project_id: str, bridge: dict[str, Any]) -> Path:
    env_dir = agent_home(project_id) / ".continue"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_path = env_dir / ".env"
    lines = []
    for name in PROFILE_ORDER:
        spec = bridge["profiles"][name]
        if spec["api_key"]:
            lines.append(f"{spec['secret_env']}={spec['api_key']}")
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""))
    return env_path


async def backend_health(project: dict) -> dict[str, Any]:
    from syte.agent_debug import probe_profile_provider

    model = await selected_model_metadata(project)
    api_key = model["api_key"]
    if not api_key:
        return {
            "ok": False,
            "error": f"{model.get('provider_label', 'Provider')} API key not configured for {model['profile']}",
            "url": None,
            "profile": model["profile"],
        }

    probe = await probe_profile_provider(model["profile"], api_key)
    chat_probe = next((p for p in probe.get("probes") or [] if p["step"] == "chat_completion"), None)
    return {
        "ok": probe.get("ok", False),
        "status_code": (chat_probe or {}).get("status_code"),
        "url": (chat_probe or {}).get("url") or model["api_base"],
        "profile": model["profile"],
        "provider": model.get("provider_label"),
        "error": probe.get("error") or "",
        "probes": probe.get("probes") or [],
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


async def wait_for_agent_ready(port: int, *, timeout_s: float = 45.0) -> tuple[bool, str]:
    """Wait until Continue agent HTTP responds or the port is listening."""
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        if _port_listening(int(port)):
            probe = await probe_agent_http(int(port))
            if probe.get("ok"):
                return True, ""
            last_error = "Port is open but agent HTTP is not responding yet"
        else:
            last_error = f"Port {port} is not listening yet"
        await asyncio.sleep(0.5)
    return False, last_error or f"Continue agent did not become ready within {int(timeout_s)}s"


async def stop_agent(project_id: str) -> tuple[bool, str]:
    from syte.agent_activity import record_agent_event

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
    await record_agent_event(project_id, "agent_stopped", title="Agent stopped", detail="Continue agent stopped")
    return True, "Continue agent stopped."


async def start_agent(project_id: str) -> tuple[bool, str, dict]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found", {}
    project = await ensure_agent_runtime(project)
    port = int(project["agent_port"])

    if is_agent_running(project_id) and _port_listening(port):
        healthy = await probe_agent_http(port)
        if healthy.get("ok"):
            status = await get_agent_status(project_id)
            return True, "Continue agent already running.", status

    if not continue_installed():
        message = 'Continue CLI not installed. Install with: npm install -g @continuedev/cli'
        await update_project(project_id, {"agent_status": "error", "agent_last_error": message})
        return False, message, {}

    await stop_agent(project_id)
    bridge = await bridge_settings()
    try:
        config_path = await write_agent_config(project)
    except RuntimeError as exc:
        message = str(exc)
        await update_project(project_id, {"agent_status": "error", "agent_last_error": message})
        return False, message, {}
    home = agent_home(project_id)
    write_agent_secrets(project_id, bridge)
    write_agent_permissions(project_id)
    log_path = agent_log_path(project_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log:
        log.write(f"\n=== Continue serve session {_now()} ===\n")
        log.write(f"Config: {config_path}\n")
        log.write(f"Port: {port}\n")

    env = {
        **os.environ,
        **read_env_vars(project.get("env_vars", "{}")),
        "HOME": str(home),
        "CONTINUE_GLOBAL_DIR": str(home / ".continue"),
        "CONTINUE_DISABLE_HUB": "1",
    }
    from syte.agent_skills import agent_path_env

    env.update(agent_path_env(project_id, agent_root(project_id)))
    for name in PROFILE_ORDER:
        spec = bridge["profiles"][name]
        if spec["api_key"]:
            env[spec["secret_env"]] = spec["api_key"]
    command = build_serve_command(config_path, port)

    repo = workspace_path(project_id) / "app"
    repo.mkdir(parents=True, exist_ok=True)

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
    for _ in range(180):
        if proc.poll() is not None:
            log_file.close()
            error = get_agent_logs(project_id, 80)
            tail = error[-2000:] if error else "No log output"
            await update_project(
                project_id,
                {"agent_status": "error", "agent_last_error": tail},
            )
            return False, f"Continue agent exited during startup.\n{tail}", {}
        if _port_listening(port):
            ready = True
            break
        time.sleep(0.25)

    if not ready:
        log_file.close()
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        agent_pid_file(project_id).unlink(missing_ok=True)
        error = get_agent_logs(project_id, 80)
        tail = error[-2000:] if error else "Port never opened"
        await update_project(project_id, {"agent_status": "error", "agent_last_error": tail})
        return False, f"Continue agent did not become ready on port {port}.\n{tail}", {}

    agent_pid_file(project_id).write_text(str(proc.pid))
    log_file.close()
    await update_project(
        project_id,
        {
            "agent_status": "running",
            "agent_last_started_at": _now(),
            "agent_last_error": "",
            "agent_config_path": str(config_path),
        },
    )
    status = await get_agent_status(project_id)
    from syte.agent_activity import record_agent_event, reset_history_tracker

    reset_history_tracker(project_id)
    await record_agent_event(
        project_id,
        "agent_started",
        title="Agent started",
        detail=f"Continue agent started on port {port}",
        payload={"port": port},
    )
    return True, f"Continue agent started on port {port}.", status


async def _post_agent_message(port: int, message: str) -> tuple[int, str]:
    base = agent_local_url(int(port)).rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{base}/message",
            json={"message": message},
            headers={"Content-Type": "application/json"},
        )
        return response.status_code, response.text


async def restart_agent(project_id: str) -> tuple[bool, str, dict]:
    from syte.agent_activity import record_agent_event

    await stop_agent(project_id)
    ok, message, status = await start_agent(project_id)
    if ok:
        await record_agent_event(project_id, "agent_restarted", title="Agent restarted", detail=message)
    return ok, message, status


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


async def _poll_agent_state(
    port: int,
    *,
    timeout_s: float = 90.0,
    project_id: str | None = None,
    source: str = "agent",
) -> dict:
    from syte.agent_activity import ingest_agent_state

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
                if project_id:
                    await ingest_agent_state(project_id, last_state, source=source)
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
    from syte.agent_activity import record_agent_event

    try:
        return await _communicate_with_agent_impl(
            project_id,
            message,
            model_profile=model_profile,
            source=source,
            auto_start=auto_start,
        )
    except Exception as exc:
        err = str(exc) or "Agent request failed"
        try:
            await log_agent_request(
                project_id,
                source=source,
                model_profile=model_profile,
                message=message,
                status="error",
                error=err,
            )
            await record_agent_event(
                project_id,
                "request_failed",
                title="Failed",
                detail=err[:4000],
                payload={"error": err},
                source=source,
            )
        except Exception:
            pass
        return {"ok": False, "error": "agent_communicate_failed", "message": err}


async def _communicate_with_agent_impl(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    source: str = "api",
    auto_start: bool = True,
) -> dict:
    from syte.agent_metrics import log_agent_request
    from syte.agent_activity import record_agent_event

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    if not continue_installed():
        message = "Continue CLI not installed. Install with: npm install -g @continuedev/cli"
        return {"ok": False, "error": "cli_not_installed", "message": message}

    if model_profile:
        await update_agent_settings(project_id, model_profile=model_profile)

    project = await ensure_agent_runtime(project)
    try:
        await write_agent_config(project)
    except Exception as exc:
        message_text = str(exc)
        await update_project(project_id, {"agent_status": "error", "agent_last_error": message_text})
        return {"ok": False, "error": "api_key_missing", "message": message_text}

    status = await get_agent_status(project_id)
    if not status.get("agent_running") or not status.get("agent_healthy"):
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

    ready, ready_msg = await wait_for_agent_ready(int(port))
    if not ready:
        err = ready_msg or "Continue agent is not ready"
        await log_agent_request(project_id, source=source, model_profile=model_profile, message=message, status="error", error=err)
        return {"ok": False, "error": "agent_not_ready", "message": err}

    model = status.get("agent_model") or {}
    await record_agent_event(
        project_id,
        "request_started",
        role="user",
        title="Request",
        detail=message[:4000],
        payload={"message": message, "model_profile": model.get("profile")},
        source=source,
    )
    try:
        status_code, response_text = await _post_agent_message(int(port), message)
        if status_code >= 400:
            err = f"Agent returned HTTP {status_code}"
            if response_text:
                err += f": {response_text[:1000]}"
            await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="error", error=err)
            await record_agent_event(
                project_id,
                "request_failed",
                title="Failed",
                detail=err[:4000],
                payload={"error": err, "status_code": status_code},
                source=source,
            )
            return {"ok": False, "error": "agent_http_error", "message": err, "status_code": status_code}

        state = await _poll_agent_state(int(port), project_id=project_id, source="agent")
        reply = _extract_assistant_reply(state)
        await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="ok")
        await record_agent_event(
            project_id,
            "request_completed",
            role="assistant",
            title="Completed",
            detail=(reply or "Request finished")[:4000],
            payload={"reply": reply, "model_profile": model.get("profile")},
            source=source,
        )
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
    except httpx.HTTPError as exc:
        err = f"Could not reach Continue agent: {exc}"
        await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="error", error=err)
        await record_agent_event(
            project_id,
            "request_failed",
            title="Failed",
            detail=err[:4000],
            payload={"error": err},
            source=source,
        )
        return {"ok": False, "error": "agent_communicate_failed", "message": err}
    except Exception as exc:
        err = str(exc)
        await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="error", error=err)
        await record_agent_event(
            project_id,
            "request_failed",
            title="Failed",
            detail=err[:4000],
            payload={"error": err},
            source=source,
        )
        return {"ok": False, "error": "agent_communicate_failed", "message": err}


async def test_agent(project_id: str, *, source: str = "api", model_profile: str | None = None) -> dict:
    from syte.agent_debug import build_ai_debug_report
    from syte.agent_metrics import log_agent_request

    async def fail(**payload: Any) -> dict:
        if not payload.get("ok", False):
            payload["debug"] = await build_ai_debug_report(project_id, model_profile=model_profile)
        return payload

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    if model_profile:
        await update_agent_settings(project_id, model_profile=model_profile)

    project = await get_project(project_id) or project
    try:
        await write_agent_config(project)
    except RuntimeError as exc:
        return await fail(
            ok=False,
            error="api_key_missing",
            message=str(exc),
            checks={"cli": continue_installed(), "backend": False, "agent": False},
        )

    status = await get_agent_status(project_id)
    backend = status.get("agent_backend") or {}
    install_ok = status.get("agent_install_ok", continue_installed())

    if not install_ok:
        return await fail(
            ok=False,
            error="cli_not_installed",
            message="Continue CLI not installed. Install: npm install -g @continuedev/cli",
            checks={"cli": False, "backend": backend.get("ok", False), "agent": False},
        )

    if not backend.get("ok"):
        await log_agent_request(project_id, source=source, status="error", error=backend.get("error") or "backend_unreachable")
        return await fail(
            ok=False,
            error="backend_unreachable",
            message=backend.get("error") or "Provider API unreachable",
            checks={"cli": True, "backend": False, "agent": status.get("agent_running", False)},
            backend=backend,
        )

    if status.get("agent_running"):
        await restart_agent(project_id)
        status = await get_agent_status(project_id)
    else:
        ok, start_msg, status = await start_agent(project_id)
        if not ok:
            await log_agent_request(project_id, source=source, status="error", error=start_msg)
            return await fail(
                ok=False,
                error="agent_start_failed",
                message=start_msg,
                checks={"cli": True, "backend": True, "agent": False},
            )

    result = await communicate_with_agent(
        project_id,
        "Reply with exactly the word 'ok' and nothing else.",
        source=source,
        auto_start=False,
    )
    passed = result.get("ok") and "ok" in (result.get("reply") or "").lower()
    if passed:
        return {
            "ok": True,
            "error": None,
            "message": "Agent test passed",
            "checks": {"cli": True, "backend": True, "agent": True, "communicate": True},
            "reply": result.get("reply", ""),
            "model": result.get("model"),
            "provider": result.get("provider"),
        }
    return await fail(
        ok=False,
        error=result.get("error") or "test_reply_invalid",
        message=result.get("message") or "Agent did not return expected reply",
        checks={"cli": True, "backend": True, "agent": True, "communicate": result.get("ok", False)},
        reply=result.get("reply", ""),
        model=result.get("model"),
        provider=result.get("provider"),
    )

