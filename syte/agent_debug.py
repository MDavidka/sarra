"""Detailed AI agent connectivity diagnostics for the GUI."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS, profile_provider
from syte.openhands_agent import (
    agent_log_path,
    bridge_settings,
    build_agent_server_command,
    get_agent_logs,
    get_agent_status,
    is_agent_running,
    openhands_command,
    openhands_installed,
    write_agent_config,
)
from syte.database import get_project, update_project


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_api_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}…{key[-4:]}"


def openhands_agent_server_info() -> dict[str, Any]:
    installed = openhands_installed()
    package_version = ""
    if installed:
        try:
            package_version = version("openhands-agent-server")
        except PackageNotFoundError:
            package_version = "installed"
    return {
        "installed": installed,
        "path": openhands_command(),
        "version": package_version,
    }


async def _http_probe(
    *,
    step: str,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {
        "step": step,
        "method": method,
        "url": url,
        "ok": False,
        "status_code": None,
        "latency_ms": None,
        "error": "",
        "body_preview": "",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if method == "GET":
                response = await client.get(url, headers=headers)
            else:
                response = await client.post(url, headers=headers, json=json_body or {})
        elapsed = int((time.perf_counter() - started) * 1000)
        body = response.text[:500]
        result.update({
            "status_code": response.status_code,
            "latency_ms": elapsed,
            "body_preview": body,
        })
        if response.status_code in (401, 403):
            result["error"] = f"Authentication failed (HTTP {response.status_code})"
        elif response.status_code >= 400:
            result["error"] = f"HTTP {response.status_code}"
        else:
            result["ok"] = True
    except Exception as exc:
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        result["error"] = str(exc)
    return result


async def probe_profile_provider(profile: str, api_key: str) -> dict[str, Any]:
    spec = profile_provider(profile)
    base = spec["api_base"].rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    probes: list[dict[str, Any]] = []

    if not api_key:
        return {
            "profile": profile,
            "label": spec["label"],
            "api_base": spec["api_base"],
            "model": spec["model"],
            "secret_env": spec["secret_env"],
            "api_key_set": False,
            "api_key_hint": "",
            "probes": [],
            "ok": False,
            "error": "API key not saved for this profile",
        }

    probes.append(await _http_probe(
        step="models_list",
        method="GET",
        url=f"{base}/models",
        headers=headers,
    ))
    probes.append(await _http_probe(
        step="chat_completion",
        method="POST",
        url=f"{base}/chat/completions",
        headers=headers,
        json_body={
            "model": spec["model"],
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
            "max_tokens": 16,
        },
    ))

    chat_probe = next((p for p in probes if p["step"] == "chat_completion"), None)
    models_probe = next((p for p in probes if p["step"] == "models_list"), None)
    ok = bool(chat_probe and chat_probe.get("ok"))
    if not ok and models_probe and models_probe.get("ok"):
        ok = True

    error = ""
    if not ok:
        if chat_probe and chat_probe.get("error"):
            error = chat_probe["error"]
        elif models_probe and models_probe.get("error"):
            error = models_probe["error"]
        else:
            error = "Provider probes failed"

    hints: list[str] = []
    if spec["label"] == "Verted" and models_probe and models_probe.get("status_code") == 401:
        hints.append(
            "Verted (Gemini) often returns HTTP 401 on GET /models even with a valid key — "
            "check chat_completion instead."
        )
    if chat_probe and chat_probe.get("status_code") == 404:
        hints.append(f"Model {spec['model']} not found at provider — name may be outdated.")
    if chat_probe and chat_probe.get("status_code") in (401, 403):
        hints.append(
            f"This key was rejected by {spec['label']}. "
            f"Ensure it is a {spec['label']} key (DeepSeek keys only work on syra-base)."
        )

    return {
        "profile": profile,
        "label": spec["label"],
        "api_base": spec["api_base"],
        "model": spec["model"],
        "secret_env": spec["secret_env"],
        "api_key_set": True,
        "api_key_hint": mask_api_key(api_key),
        "probes": probes,
        "ok": ok,
        "error": error,
        "hints": hints,
    }


def inspect_agent_config(project_id: str) -> dict[str, Any]:
    from syte.openhands_agent import agent_config_path

    path = agent_config_path(project_id)
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "session_key_configured": False,
            "runtime": "",
            "conversations_path": "",
            "snippet": "",
        }

    text = path.read_text(errors="replace")
    try:
        config = json.loads(text)
    except json.JSONDecodeError:
        return {
            "path": str(path),
            "exists": True,
            "session_key_configured": False,
            "runtime": "",
            "conversations_path": "",
            "snippet": "Invalid OpenHands Agent Server JSON configuration",
        }
    keys = config.get("session_api_keys") if isinstance(config, dict) else []
    redacted = dict(config) if isinstance(config, dict) else {}
    if redacted.get("secret_key"):
        redacted["secret_key"] = "<redacted>"
    if isinstance(redacted.get("session_api_keys"), list):
        redacted["session_api_keys"] = [
            "<redacted>" for _key in redacted["session_api_keys"]
        ]

    return {
        "path": str(path),
        "exists": True,
        "session_key_configured": bool(keys),
        "runtime": "openhands",
        "conversations_path": str(config.get("conversations_path") or ""),
        "snippet": json.dumps(redacted, indent=2)[:4000],
    }


def inspect_agent_secrets(project_id: str) -> dict[str, Any]:
    config = inspect_agent_config(project_id)
    return {
        "path": config["path"],
        "exists": config["exists"],
        "vars_set": ["session_api_key"] if config.get("session_key_configured") else [],
    }


def build_debug_hints(report: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    agent_server = report.get("openhands_agent_server") or {}
    if not agent_server.get("installed"):
        hints.append("Install OpenHands Agent Server with the project's Python dependencies.")

    config = report.get("config") or {}
    if config.get("exists") and not config.get("session_key_configured"):
        hints.append("OpenHands Agent Server config has no session key; regenerate it by starting the agent.")

    for profile in report.get("profiles") or []:
        hints.extend(profile.get("hints") or [])
        if not profile.get("api_key_set"):
            hints.append(f"Add API key for {profile['profile']} ({profile['label']}) in AI settings.")

    agent = report.get("agent") or {}
    logs = report.get("logs_tail") or ""
    if agent.get("agent_last_error"):
        hints.append("Agent last error logged — see agent logs below.")
    if agent.get("agent_status") == "error":
        hints.append("OpenHands agent is in error state — check agent-server.log tail.")

    active = report.get("active_profile")
    active_row = next((p for p in report.get("profiles") or [] if p["profile"] == active), None)
    if active_row and active_row.get("api_key_set") and not active_row.get("ok"):
        hints.append(
            f"Provider reachable check failed for active profile {active}. "
            "Verify the key matches the provider shown in settings."
        )

    return list(dict.fromkeys(hints))


async def build_ai_debug_report(
    project_id: str,
    *,
    model_profile: str | None = None,
    include_logs: bool = True,
    log_lines: int = 80,
) -> dict[str, Any]:
    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    bridge = await bridge_settings()
    active_profile = (model_profile or project.get("agent_model_profile") or bridge["default_profile"] or "syra-base").strip()

    if model_profile and model_profile != project.get("agent_model_profile"):
        await update_project(project_id, {"agent_model_profile": model_profile})

    profiles: list[dict[str, Any]] = []
    for name in PROFILE_ORDER:
        spec = bridge["profiles"][name]
        profiles.append(await probe_profile_provider(name, spec["api_key"]))

    config_write_error = ""
    try:
        await write_agent_config(await get_project(project_id) or project)
    except RuntimeError as exc:
        config_write_error = str(exc)

    agent_status = await get_agent_status(project_id)
    config_info = inspect_agent_config(project_id)
    secrets_info = inspect_agent_secrets(project_id)
    agent_server_info = openhands_agent_server_info()

    active_probe = next((p for p in profiles if p["profile"] == active_profile), None)
    steps = [
        {
            "id": "openhands_agent_server",
            "label": "OpenHands Agent Server installed",
            "ok": agent_server_info["installed"],
            "detail": agent_server_info["version"] or agent_server_info["path"],
        },
        {
            "id": "active_profile_key",
            "label": f"API key saved ({active_profile})",
            "ok": bool(active_probe and active_probe.get("api_key_set")),
            "detail": (active_probe or {}).get("api_key_hint") or "missing",
        },
        {
            "id": "agent_session_key",
            "label": "Agent Server session key",
            "ok": config_info.get("session_key_configured", False),
            "detail": "configured" if config_info.get("session_key_configured") else "missing",
        },
        {
            "id": "secrets_env",
            "label": "Managed Agent Server credentials",
            "ok": secrets_info.get("exists", False),
            "detail": ", ".join(secrets_info.get("vars_set") or []) or "not written yet",
        },
        {
            "id": "provider_reachable",
            "label": f"Provider probe ({active_profile})",
            "ok": bool(active_probe and active_probe.get("ok")),
            "detail": (active_probe or {}).get("error") or "ok",
        },
        {
            "id": "agent_running",
            "label": "OpenHands agent process",
            "ok": bool(agent_status.get("agent_running")),
            "detail": agent_status.get("agent_status") or "unknown",
        },
        {
            "id": "agent_http",
            "label": "Agent HTTP health",
            "ok": bool(agent_status.get("agent_healthy")),
            "detail": agent_status.get("agent_local_url") or "",
        },
    ]

    if config_write_error:
        steps.insert(3, {
            "id": "config_write",
            "label": "Write agent config",
            "ok": False,
            "detail": config_write_error,
        })

    report = {
        "ok": all(step["ok"] for step in steps if step["id"] in {
            "openhands_agent_server", "active_profile_key", "agent_session_key", "provider_reachable"
        }),
        "generated_at": _now(),
        "project_id": project_id,
        "active_profile": active_profile,
        "openhands_agent_server": agent_server_info,
        "profiles": profiles,
        "config": config_info,
        "secrets": secrets_info,
        "agent": {
            "agent_status": agent_status.get("agent_status"),
            "agent_running": agent_status.get("agent_running"),
            "agent_healthy": agent_status.get("agent_healthy"),
            "agent_port": agent_status.get("agent_port"),
            "agent_local_url": agent_status.get("agent_local_url"),
            "agent_config_path": agent_status.get("agent_config_path"),
            "agent_log_path": agent_status.get("agent_log_path"),
            "agent_last_error": agent_status.get("agent_last_error"),
            "agent_model": agent_status.get("agent_model"),
            "agent_backend": agent_status.get("agent_backend"),
            "agent_install_ok": agent_status.get("agent_install_ok"),
            "is_agent_running_pid": is_agent_running(project_id),
            "serve_command": build_agent_server_command(
                config_info["path"],
                int(agent_status.get("agent_port") or 5200),
            ),
            "openhands_command": openhands_command(),
        },
        "steps": steps,
        "logs_tail": get_agent_logs(project_id, log_lines) if include_logs else "",
        "config_write_error": config_write_error,
    }
    report["hints"] = build_debug_hints(report)
    return report
