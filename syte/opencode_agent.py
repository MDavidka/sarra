"""OpenCode agent runtime management for Syte workspaces."""

from __future__ import annotations

import asyncio
import json
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
    return path / f"{project_id}.opencode.pid"


def agent_root(project_id: str) -> Path:
    root = ensure_workspace(project_id) / "data" / "opencode"
    root.mkdir(parents=True, exist_ok=True)
    return root


def agent_home(project_id: str) -> Path:
    home = agent_root(project_id) / "home"
    (home / ".config" / "opencode").mkdir(parents=True, exist_ok=True)
    return home


def agent_config_path(project_id: str) -> Path:
    return agent_root(project_id) / "opencode.json"


def agent_log_path(project_id: str) -> Path:
    return agent_root(project_id) / "serve.log"


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def opencode_command() -> str:
    return shutil.which("opencode") or "opencode"


def opencode_installed() -> bool:
    return shutil.which("opencode") is not None


def build_serve_command(port: int, *, hostname: str = "127.0.0.1") -> str:
    """Build an OpenCode headless serve command."""
    return f"{opencode_command()} serve --port {int(port)} --hostname {hostname}"


async def next_agent_port() -> int:
    projects = await list_projects()
    used = {p.get("agent_port") for p in projects if p.get("agent_port")}
    for port in range(settings.continue_port_start, settings.continue_port_end + 1):
        if port not in used:
            return port
    raise RuntimeError(
        f"No OpenCode agent ports available ({settings.continue_port_start}-{settings.continue_port_end} exhausted)"
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


def agent_http_auth() -> httpx.BasicAuth | None:
    password = (os.environ.get("OPENCODE_SERVER_PASSWORD") or "").strip()
    if not password:
        return None
    username = (os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode").strip() or "opencode"
    return httpx.BasicAuth(username, password)


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
        "opencode_provider_id": profile,
        "opencode_model_id": spec["model"],
    }


async def write_agent_config(project: dict) -> Path:
    from syte.opencode_extras import (
        ensure_skills_directories,
        load_agent_rules,
        load_mcp_servers,
        render_mcp_servers_dict,
    )

    project = await ensure_agent_runtime(project)
    bridge = await bridge_settings()
    config_path = agent_config_path(project["id"])
    model_data = await selected_model_metadata(project)
    active_profile = model_data["profile"]

    configured = [name for name in PROFILE_ORDER if bridge["profiles"][name]["api_key"]]
    if active_profile not in configured:
        raise RuntimeError(
            f"No API key configured for active profile {active_profile}. "
            f"Open AI settings and add the {bridge['profiles'][active_profile]['label']} key."
        )
    if not configured:
        raise RuntimeError("No model API keys configured. Open AI settings and add provider keys.")

    active_spec = bridge["profiles"][active_profile]
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{active_profile}/{active_spec['model']}",
        "autoupdate": False,
        "share": "disabled",
        "disabled_providers": ["opencode"],
        "provider": {},
    }

    for alias in configured:
        spec = bridge["profiles"][alias]
        config["provider"][alias] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": spec["label"],
            "options": {
                "baseURL": spec["api_base"],
                "apiKey": f"{{env:{spec['secret_env']}}}",
            },
            "models": {
                spec["model"]: {"name": spec["model"]},
            },
        }

    ensure_skills_directories(project["id"])
    mcp_servers = await load_mcp_servers()
    mcp_block = render_mcp_servers_dict(mcp_servers)
    if mcp_block:
        config["mcp"] = mcp_block

    rules = await load_agent_rules()
    if rules:
        config["instructions"] = rules

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return config_path


def write_agent_secrets(project_id: str, bridge: dict[str, Any]) -> Path:
    """Write provider API keys into the agent home .env for OpenCode {env:VAR} substitution."""
    env_dir = agent_home(project_id) / ".config" / "opencode"
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
    auth = agent_http_auth()
    async with httpx.AsyncClient(timeout=3.0) as client:
        for path in ("/global/health", "/doc", "/"):
            try:
                response = await client.get(base + path, auth=auth)
                if response.status_code < 500:
                    return {"ok": True, "url": base + path, "status_code": response.status_code}
            except Exception:
                continue
    return {"ok": _port_listening(int(port)), "url": base, "status_code": None}


def get_agent_logs(project_id: str, lines: int = 200) -> str:
    log_path = agent_log_path(project_id)
    if not log_path.exists():
        return "No OpenCode agent logs yet."
    content = log_path.read_text(errors="replace").splitlines()
    return "\n".join(content[-lines:])


async def stop_agent(project_id: str) -> tuple[bool, str]:
    from syte.agent_activity import record_agent_event

    pf = agent_pid_file(project_id)
    if not pf.exists():
        await update_project(project_id, {"agent_status": "stopped", "agent_session_id": ""})
        return True, "OpenCode agent already stopped."
    try:
        pid = int(pf.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    pf.unlink(missing_ok=True)
    await update_project(project_id, {"agent_status": "stopped", "agent_session_id": ""})
    await record_agent_event(project_id, "agent_stopped", title="Agent stopped", detail="OpenCode agent stopped")
    return True, "OpenCode agent stopped."


async def start_agent(project_id: str) -> tuple[bool, str, dict]:
    from syte.agent_metrics import agents_online_count, max_agents_allowed

    project = await get_project(project_id)
    if not project:
        return False, "Project not found", {}
    project = await ensure_agent_runtime(project)
    port = int(project["agent_port"])

    if is_agent_running(project_id) and _port_listening(port):
        status = await get_agent_status(project_id)
        return True, "OpenCode agent already running.", status

    if not is_agent_running(project_id):
        online = await agents_online_count()
        max_allowed = await max_agents_allowed()
        if online >= max_allowed:
            return (
                False,
                f"AI agent limit reached ({online}/{max_allowed}). "
                "Increase max agents (AI) in AI settings.",
                {},
            )

    if not opencode_installed():
        message = "OpenCode CLI not installed. Install with: npm install -g opencode-ai"
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
    log_path = agent_log_path(project_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log:
        log.write(f"\n=== OpenCode serve session {_now()} ===\n")
        log.write(f"Config: {config_path}\n")
        log.write(f"Port: {port}\n")

    env = {
        **os.environ,
        **read_env_vars(project.get("env_vars", "{}")),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "OPENCODE_CONFIG": str(config_path),
    }
    for name in PROFILE_ORDER:
        spec = bridge["profiles"][name]
        if spec["api_key"]:
            env[spec["secret_env"]] = spec["api_key"]
    command = build_serve_command(port)

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
    time.sleep(2.0)
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
        return False, "OpenCode agent exited during startup.", {}

    agent_pid_file(project_id).write_text(str(proc.pid))
    log_file.close()
    await update_project(
        project_id,
        {
            "agent_status": "running" if _port_listening(port) else "starting",
            "agent_last_started_at": _now(),
            "agent_last_error": "",
            "agent_config_path": str(config_path),
            "agent_session_id": "",
        },
    )
    status = await get_agent_status(project_id)
    from syte.agent_activity import record_agent_event, reset_history_tracker

    reset_history_tracker(project_id)
    await record_agent_event(
        project_id,
        "agent_started",
        title="Agent started",
        detail=f"OpenCode agent started on port {port}",
        payload={"port": port},
    )
    return True, f"OpenCode agent started on port {port}.", status


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
        "agent_engine": "opencode",
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
        "agent_session_id": project.get("agent_session_id") or "",
        "agent_last_started_at": project.get("agent_last_started_at"),
        "agent_last_error": project.get("agent_last_error") or "",
        "agent_backend": backend,
        "agent_model": model,
        "agent_command": opencode_command(),
        "agent_install_ok": opencode_installed(),
        "agent_no_hub_required": True,
        "agent_openapi_doc": (runtime_url + "/doc") if port else "",
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


def _extract_assistant_reply(message_result: dict) -> str:
    parts = message_result.get("parts") or []
    texts = [str(part.get("text") or "") for part in parts if part.get("type") == "text"]
    return "\n".join(text for text in texts if text).strip()


async def _ensure_agent_session(project_id: str, port: int) -> str:
    project = await get_project(project_id) or {}
    session_id = (project.get("agent_session_id") or "").strip()
    base = agent_local_url(port).rstrip("/")
    auth = agent_http_auth()
    async with httpx.AsyncClient(timeout=30.0) as client:
        if session_id:
            try:
                response = await client.get(f"{base}/session/{session_id}", auth=auth)
                if response.status_code == 200:
                    return session_id
            except Exception:
                pass
        response = await client.post(
            f"{base}/session",
            json={"title": f"Syte {project_id[:8]}"},
            auth=auth,
        )
        response.raise_for_status()
        session_id = str(response.json().get("id") or "")
        if not session_id:
            raise RuntimeError("OpenCode did not return a session id")
        await update_project(project_id, {"agent_session_id": session_id})
        return session_id


async def _poll_session_messages(
    port: int,
    session_id: str,
    *,
    timeout_s: float = 90.0,
    project_id: str | None = None,
    source: str = "agent",
) -> list[dict[str, Any]]:
    from syte.agent_activity import ingest_opencode_messages

    base = agent_local_url(port).rstrip("/")
    auth = agent_http_auth()
    deadline = time.time() + timeout_s
    last_messages: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        while time.time() < deadline:
            try:
                status_response = await client.get(f"{base}/session/status", auth=auth)
                busy = False
                if status_response.status_code < 400:
                    statuses = status_response.json() or {}
                    session_status = statuses.get(session_id) or {}
                    busy = str(session_status.get("type") or "").lower() in {"busy", "running", "processing"}

                messages_response = await client.get(f"{base}/session/{session_id}/message", auth=auth)
                if messages_response.status_code < 400:
                    last_messages = messages_response.json() or []
                    if project_id:
                        await ingest_opencode_messages(project_id, last_messages, source=source)
                if not busy:
                    return last_messages
            except Exception:
                pass
            await asyncio.sleep(0.5)
    return last_messages


async def communicate_with_agent(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    source: str = "api",
    auto_start: bool = True,
) -> dict:
    from syte.agent_activity import ingest_opencode_messages, record_agent_event, reset_history_tracker
    from syte.agent_metrics import log_agent_request

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    if model_profile:
        await update_agent_settings(project_id, model_profile=model_profile)

    status = await get_agent_status(project_id)
    if not status.get("agent_running"):
        if not auto_start:
            err = "OpenCode agent is not running"
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
    auth = agent_http_auth()
    reset_history_tracker(project_id, source=source)
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
        session_id = await _ensure_agent_session(project_id, int(port))
        body = {
            "parts": [{"type": "text", "text": message}],
            "model": {
                "providerID": model.get("opencode_provider_id") or model.get("profile"),
                "modelID": model.get("opencode_model_id") or model.get("model"),
            },
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base}/session/{session_id}/message",
                json=body,
                auth=auth,
            )
            if response.status_code >= 400:
                err = f"Agent returned HTTP {response.status_code}"
                await log_agent_request(project_id, source=source, model_profile=model.get("profile"), message=message, status="error", error=err)
                return {"ok": False, "error": "agent_http_error", "message": err, "status_code": response.status_code}

            result = response.json()
            reply = _extract_assistant_reply(result)
            messages = await _poll_session_messages(
                int(port),
                session_id,
                project_id=project_id,
                source=source,
                timeout_s=5.0,
            )
            if not reply and messages:
                last_assistant = next(
                    (item for item in reversed(messages) if (item.get("info") or {}).get("role") == "assistant"),
                    None,
                )
                if last_assistant:
                    reply = _extract_assistant_reply(last_assistant)
            await ingest_opencode_messages(project_id, messages or [result], source=source)
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
            "session_id": session_id,
            "result": result,
        }
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
            checks={"cli": opencode_installed(), "backend": False, "agent": False},
        )

    status = await get_agent_status(project_id)
    backend = status.get("agent_backend") or {}
    install_ok = status.get("agent_install_ok", opencode_installed())

    if not install_ok:
        return await fail(
            ok=False,
            error="cli_not_installed",
            message="OpenCode CLI not installed. Install: npm install -g opencode-ai",
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
