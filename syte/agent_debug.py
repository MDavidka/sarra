"""Detailed AI agent connectivity diagnostics for the GUI."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS, profile_provider
from syte.cloud_agent import (
    agent_log_path,
    bridge_settings,
    get_agent_logs,
    get_agent_status,
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
    if spec["label"] in ("Vertex AI", "Verted") and models_probe and models_probe.get("status_code") == 401:
        hints.append(
            "Vertex AI / Gemini often returns HTTP 401 on GET /models even with a valid key — "
            "check chat_completion instead."
        )
    if chat_probe and chat_probe.get("status_code") == 404:
        hints.append(f"Model {spec['model']} not found at provider — name may be outdated.")
    if chat_probe and chat_probe.get("status_code") in (401, 403):
        hints.append(
            f"This key was rejected by {spec['label']}. "
            f"Ensure it is a {spec['label']} key for profile {profile}."
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


def cloud_agent_runtime_info() -> dict[str, Any]:
    return {
        "installed": True,
        "path": "embedded in the Syte VM service",
        "version": "native",
    }


def inspect_agent_config(project_id: str) -> dict[str, Any]:
    from syte.cloud_agent import agent_config_path

    path = agent_config_path(project_id)
    if not path.exists():
        return {"path": str(path), "exists": False, "runtime": "", "snippet": ""}
    text = path.read_text(errors="replace")
    try:
        config = json.loads(text)
    except json.JSONDecodeError:
        return {
            "path": str(path),
            "exists": True,
            "runtime": "",
            "snippet": "Invalid Syte cloud runtime JSON configuration",
        }
    return {
        "path": str(path),
        "exists": True,
        "runtime": str(config.get("runtime") or ""),
        "transport": str(config.get("transport") or ""),
        "workspace_path": str(config.get("workspace_path") or ""),
        "snippet": json.dumps(config, indent=2)[:4000],
    }


def inspect_agent_secrets(project_id: str) -> dict[str, Any]:
    del project_id
    return {
        "path": "system_settings",
        "exists": True,
        "vars_set": [],
        "detail": "Provider keys stay in Syte settings and are not copied into project files.",
    }


def build_debug_hints(report: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for profile in report.get("profiles") or []:
        hints.extend(profile.get("hints") or [])
        if not profile.get("api_key_set"):
            hints.append(f"Add API key for {profile['profile']} ({profile['label']}) in AI settings.")
    agent = report.get("agent") or {}
    if agent.get("agent_last_error"):
        hints.append("The last cloud-agent error is available in the log tail below.")
    active = report.get("active_profile")
    active_row = next((p for p in report.get("profiles") or [] if p["profile"] == active), None)
    if active_row and active_row.get("api_key_set") and not active_row.get("ok"):
        hints.append(f"Provider probe failed for {active}; verify the configured key and model endpoint.")
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
    active_profile = (
        model_profile or project.get("agent_model_profile") or bridge["default_profile"] or "syra-base"
    ).strip()
    if model_profile and model_profile != project.get("agent_model_profile"):
        await update_project(project_id, {"agent_model_profile": model_profile})

    profiles = [
        await probe_profile_provider(name, bridge["profiles"][name]["api_key"])
        for name in PROFILE_ORDER
    ]
    config_write_error = ""
    try:
        await write_agent_config(await get_project(project_id) or project)
    except RuntimeError as exc:
        config_write_error = str(exc)

    status = await get_agent_status(project_id, check_backend=False)
    config = inspect_agent_config(project_id)
    runtime = cloud_agent_runtime_info()
    active_probe = next((p for p in profiles if p["profile"] == active_profile), None)
    steps = [
        {"id": "cloud_runtime", "label": "Syte cloud runtime", "ok": True, "detail": runtime["path"]},
        {"id": "durable_session", "label": "Durable session store", "ok": config.get("exists", False),
         "detail": config.get("runtime") or config_write_error or "not initialized"},
        {"id": "active_profile_key", "label": f"API key saved ({active_profile})",
         "ok": bool(active_probe and active_probe.get("api_key_set")),
         "detail": (active_probe or {}).get("api_key_hint") or "missing"},
        {"id": "provider_reachable", "label": f"Provider probe ({active_profile})",
         "ok": bool(active_probe and active_probe.get("ok")),
         "detail": (active_probe or {}).get("error") or "ok"},
        {"id": "agent_ready", "label": "Cloud agent ready",
         "ok": bool(status.get("agent_healthy")), "detail": status.get("agent_status") or "unknown"},
    ]
    if config_write_error:
        steps.append({"id": "config_write", "label": "Initialize cloud runtime", "ok": False,
                      "detail": config_write_error})
    report = {
        "ok": all(step["ok"] for step in steps if step["id"] in {
            "cloud_runtime", "durable_session", "active_profile_key", "provider_reachable"
        }),
        "generated_at": _now(),
        "project_id": project_id,
        "active_profile": active_profile,
        "cloud_agent_runtime": runtime,
        "profiles": profiles,
        "config": config,
        "secrets": inspect_agent_secrets(project_id),
        "agent": status,
        "steps": steps,
        "logs_tail": get_agent_logs(project_id, log_lines) if include_logs else "",
    }
    report["hints"] = build_debug_hints(report)
    return report
