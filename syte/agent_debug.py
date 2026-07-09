"""Detailed AI agent connectivity diagnostics for the GUI."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS, profile_provider
from syte.continue_agent import (
    agent_home,
    agent_log_path,
    bridge_settings,
    build_serve_command,
    continue_command,
    continue_installed,
    get_agent_logs,
    get_agent_status,
    is_agent_running,
    write_agent_config,
)
from syte.continue_agent import agent_config_path as resolve_agent_config_path
from syte.database import get_project, update_project
from syte.workspace import run_cmd

SECRET_REF_RE = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}")
BROKEN_SECRET_REF_RE = re.compile(r"\$\{\s*secrets\.([A-Z0-9_]+)\s*\}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_api_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}…{key[-4:]}"


def continue_cli_info() -> dict[str, Any]:
    path = continue_command()
    installed = continue_installed()
    version = ""
    if installed:
        code, out = run_cmd([path, "--version"])
        if code == 0:
            version = out.strip().splitlines()[0] if out.strip() else ""
    return {
        "installed": installed,
        "path": path,
        "version": version,
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
    from syte.continue_agent import agent_config_path

    path = agent_config_path(project_id)
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "secret_syntax_ok": False,
            "broken_secret_refs": [],
            "valid_secret_refs": [],
            "models_in_config": [],
            "snippet": "",
        }

    text = path.read_text(errors="replace")
    broken = BROKEN_SECRET_REF_RE.findall(text)
    valid = SECRET_REF_RE.findall(text)
    models = re.findall(r'name:\s*"([^"]+)"', text)
    snippet_lines = []
    for line in text.splitlines()[:40]:
        if "apiKey" in line:
            snippet_lines.append(line.split("apiKey:")[0] + 'apiKey: "<redacted>"')
        else:
            snippet_lines.append(line)

    return {
        "path": str(path),
        "exists": True,
        "secret_syntax_ok": bool(valid) and not broken,
        "broken_secret_refs": broken,
        "valid_secret_refs": valid,
        "models_in_config": models,
        "snippet": "\n".join(snippet_lines),
    }


def inspect_agent_secrets(project_id: str) -> dict[str, Any]:
    env_path = agent_home(project_id) / ".continue" / ".env"
    if not env_path.exists():
        return {
            "path": str(env_path),
            "exists": False,
            "vars_set": [],
        }
    vars_set = []
    for line in env_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        vars_set.append(line.split("=", 1)[0].strip())
    return {
        "path": str(env_path),
        "exists": True,
        "vars_set": vars_set,
    }


def build_debug_hints(report: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    cli = report.get("continue_cli") or {}
    if not cli.get("installed"):
        hints.append("Install Continue CLI: npm install -g @continuedev/cli")

    config = report.get("config") or {}
    if config.get("broken_secret_refs"):
        hints.append(
            "config.yaml has broken secret placeholders (single braces). "
            "Update Syte and run Test again to regenerate config."
        )

    for profile in report.get("profiles") or []:
        hints.extend(profile.get("hints") or [])
        if not profile.get("api_key_set"):
            hints.append(f"Add API key for {profile['profile']} ({profile['label']}) in AI settings.")

    agent = report.get("agent") or {}
    logs = report.get("logs_tail") or ""
    if "unknown option '--host'" in logs:
        hints.append(
            "Continue CLI rejected --host (fixed in newer Syte). Update Syte and run Test again."
        )
    if agent.get("agent_last_error"):
        hints.append("Agent last error logged — see agent logs below.")
    if agent.get("agent_status") == "error":
        hints.append("Continue agent is in error state — check serve.log tail.")

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
    cli_info = continue_cli_info()

    active_probe = next((p for p in profiles if p["profile"] == active_profile), None)
    steps = [
        {
            "id": "continue_cli",
            "label": "Continue CLI installed",
            "ok": cli_info["installed"],
            "detail": cli_info["version"] or cli_info["path"],
        },
        {
            "id": "active_profile_key",
            "label": f"API key saved ({active_profile})",
            "ok": bool(active_probe and active_probe.get("api_key_set")),
            "detail": (active_probe or {}).get("api_key_hint") or "missing",
        },
        {
            "id": "config_secrets",
            "label": "config.yaml secret syntax",
            "ok": config_info.get("secret_syntax_ok", False),
            "detail": (
                f"valid: {', '.join(config_info.get('valid_secret_refs') or []) or 'none'}"
                + (f"; broken: {', '.join(config_info['broken_secret_refs'])}" if config_info.get("broken_secret_refs") else "")
            ),
        },
        {
            "id": "secrets_env",
            "label": "Agent .continue/.env",
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
            "label": "Continue agent process",
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
            "continue_cli", "active_profile_key", "config_secrets", "provider_reachable"
        }),
        "generated_at": _now(),
        "project_id": project_id,
        "active_profile": active_profile,
        "continue_cli": cli_info,
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
            "serve_command": build_serve_command(
                resolve_agent_config_path(project_id),
                int(agent_status.get("agent_port") or 5200),
            ),
            "continue_command": continue_command(),
        },
        "steps": steps,
        "logs_tail": get_agent_logs(project_id, log_lines) if include_logs else "",
        "config_write_error": config_write_error,
    }
    report["hints"] = build_debug_hints(report)
    return report
