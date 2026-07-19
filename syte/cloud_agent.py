"""VM-native cloud coding agent inspired by Kilo's durable session model.

The runtime is part of the Syte service process. It stores admitted requests and
conversation messages in SQLite, calls the configured Syra provider directly,
and executes tools through Syte's workspace APIs. No per-project CLI server,
port allocation, or WebSocket transport is required.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from syte.agent_activity import record_agent_event
from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS, profile_provider
from syte.cloud_agent_store import (
    append_message,
    begin_turn_session,
    clear_conversation,
    conversation_messages,
    current_session_number,
    current_turso_session_id,
    ensure_session,
    mark_message_synced,
    session_sync_status,
    set_turso_session_id,
)
from syte.config import settings
from syte.database import get_project, get_setting, update_project
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.thinking_levels import deepseek_thinking_payload, resolve_thinking_config
from syte.turso_store import close_session as close_turso_session
from syte.turso_store import open_session as open_turso_session
from syte.turso_store import record_message as record_turso_message
from syte.workspace import ensure_workspace, workspace_path

CLOUD_RUNTIME = "kilo-cloud"
# Compatibility for older API consumers that imported this symbol.
OPENHANDS_RUNTIME = CLOUD_RUNTIME
AGENT_INSTRUCTION_VERSION = 7
MAX_HISTORY_MESSAGES = 160
PROVIDER_TIMEOUT_S = 600.0
MAX_SUBAGENT_STEPS = 12
QUESTION_WAIT_TIMEOUT_S = 1800.0
# Cap inline vision payloads so provider requests stay bounded.
MAX_VISION_IMAGE_BYTES = 700_000

logger = logging.getLogger(__name__)
_lifecycle_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_active_turns: dict[str, asyncio.Task[Any]] = {}
_provider_client: httpx.AsyncClient | None = None
_instruction_cache: dict[tuple[str, int, str], str] = {}

TokenEmitter = Callable[[str], Awaitable[None]]


def _get_provider_client() -> httpx.AsyncClient:
    """Shared HTTP/2 client so TLS + connections are reused across provider calls."""
    global _provider_client
    if _provider_client is None or _provider_client.is_closed:
        timeout = httpx.Timeout(PROVIDER_TIMEOUT_S, connect=15.0)
        limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
        try:
            _provider_client = httpx.AsyncClient(timeout=timeout, limits=limits, http2=True)
        except Exception:
            # http2 extras missing — fall back to HTTP/1.1 keepalives.
            _provider_client = httpx.AsyncClient(timeout=timeout, limits=limits, http2=False)
    return _provider_client


async def close_provider_client() -> None:
    """Close the shared provider client (tests / shutdown)."""
    global _provider_client
    if _provider_client is not None and not _provider_client.is_closed:
        await _provider_client.aclose()
    _provider_client = None


def invalidate_instruction_cache(project_id: str | None = None) -> None:
    """Drop cached system instructions (one project or all)."""
    if project_id is None:
        _instruction_cache.clear()
        return
    for key in [k for k in _instruction_cache if k[0] == project_id]:
        del _instruction_cache[key]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _persist_message(
    project_id: str,
    request_id: str,
    role: str,
    content: str,
    *,
    session_number: int,
    turso_session_id: str | None,
    tool_call_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
) -> int:
    """Append one message locally, then mirror it to Turso in real time.

    Every message the cloud agent produces (user / assistant / tool) is
    written to the local durable store first (never fails the turn), and —
    when a durable Turso session is open for this turn (``turso_session_id``
    set, i.e. Turso is configured) — immediately mirrored into the shared
    ``agent_message`` Turso table (see :mod:`syte.turso_store`). This is what
    makes message persistence "live" for sessions started directly from the
    API (see :mod:`syte.agent_jobs`): the Turso session is opened before the
    first message is even admitted, so each subsequent message — including
    every assistant reply and tool result while the turn is still running —
    is synced to Turso as soon as it is produced, not just at the end.

    The local row is flagged ``turso_synced`` only after a successful Turso
    write, so :func:`syte.cloud_agent_store.session_sync_status` can report
    an accurate green/red "all messages saved" status for the GUI's brain
    indicator even if a particular write failed or Turso is unreachable.
    """
    local_id = await append_message(
        project_id,
        request_id,
        role,
        content,
        session_number=session_number,
        tool_call_id=tool_call_id,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
    )
    if turso_session_id:
        try:
            saved = await record_turso_message(
                turso_session_id,
                project_id,
                role,
                content,
                session_number=session_number,
                local_message_id=local_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
                tool_calls=tool_calls,
                reasoning_content=reasoning_content,
            )
        except Exception:
            saved = None
            logger.exception(
                "Failed to mirror agent message %s to Turso session %s",
                local_id,
                turso_session_id,
            )
        if saved:
            await mark_message_synced(local_id, synced=True)
    return local_id


def agent_root(project_id: str) -> Path:
    path = ensure_workspace(project_id) / "data" / "cloud-agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_config_path(project_id: str) -> Path:
    return agent_root(project_id) / "runtime.json"


def agent_runtime_path(project_id: str) -> Path:
    return agent_config_path(project_id)


def agent_log_path(project_id: str) -> Path:
    return agent_root(project_id) / "agent.log"


def agent_instruction_path(project_id: str) -> Path:
    return agent_root(project_id) / "SYTE_AGENT.md"


def cloud_agent_installed() -> bool:
    return True




def cloud_agent_command() -> str:
    return "embedded Syte cloud agent"



async def profile_api_key(profile: str) -> str:
    spec = profile_provider(profile)
    return (await get_setting(spec["setting_key"], "")).strip()


async def bridge_settings() -> dict[str, Any]:
    default_profile = (
        await get_setting("agent_default_model_profile", "syra-base")
    ).strip() or "syra-base"
    if default_profile not in PROFILE_PROVIDERS:
        default_profile = "syra-base"
    profiles: dict[str, dict[str, str]] = {}
    for name in PROFILE_ORDER:
        spec = PROFILE_PROVIDERS[name]
        profiles[name] = {
            **spec,
            "api_key": await profile_api_key(name),
        }
    active = profiles[default_profile]
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


async def selected_model_metadata(project: dict[str, Any]) -> dict[str, str]:
    bridge = await bridge_settings()
    profile = str(project.get("agent_model_profile") or bridge["default_profile"])
    spec = bridge["profiles"].get(profile, bridge["profiles"]["syra-base"])
    return {
        "profile": profile if profile in PROFILE_PROVIDERS else "syra-base",
        "provider": spec["provider"],
        "provider_label": spec["label"],
        "model": spec["model"],
        "api_base": spec["api_base"],
        "api_key": spec["api_key"],
    }


async def ensure_agent_runtime(project: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if not project.get("agent_status"):
        updates["agent_status"] = "stopped"
    if project.get("agent_runtime") != CLOUD_RUNTIME:
        updates.update({"agent_runtime": CLOUD_RUNTIME, "agent_port": None})
    if not project.get("agent_model_profile"):
        updates["agent_model_profile"] = (await bridge_settings())["default_profile"]
    if not project.get("agent_conversation_id"):
        updates["agent_conversation_id"] = f"cloud-{project['id']}"
    if updates:
        await update_project(project["id"], updates)
        project = await get_project(project["id"]) or {**project, **updates}
    return project


async def backend_health(project: dict[str, Any]) -> dict[str, Any]:
    from syte.agent_debug import probe_profile_provider

    model = await selected_model_metadata(project)
    if not model["api_key"]:
        return {
            "ok": False,
            "error": f"{model['provider_label']} API key not configured for {model['profile']}",
            "url": model["api_base"],
            "profile": model["profile"],
            "provider": model["provider_label"],
            "probes": [],
        }
    return await probe_profile_provider(model["profile"], model["api_key"])


def _write_log(project_id: str, line: str) -> None:
    path = agent_log_path(project_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_now()}] {line}\n")


def get_agent_logs(project_id: str, lines: int = 200) -> str:
    path = agent_log_path(project_id)
    if not path.exists():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-max(1, lines):])


async def _build_syte_instruction(project_id: str, *, force_refresh: bool = False) -> str:
    from syte.agent_skills import (
        SKILL_REGISTRY,
        build_agent_rules,
        get_project_skills,
        read_access_config,
        write_agent_skills,
    )
    from syte.design_contract import (
        DESIGN_CONTRACT_MARKDOWN,
        DESIGN_CONTRACT_VERSION,
        shadcn_catalog_json,
        themes_prompt_block,
    )

    root = agent_root(project_id)
    access = await read_access_config(project_id, root)
    rules = [item for item in build_agent_rules(project_id, access) if item.get("rule")]
    rule_lines = "\n".join(f"- {item['name']}: {item['rule']}" for item in rules)
    project_skills = await get_project_skills(project_id)
    active_skill_blocks = [
        SKILL_REGISTRY[skill["id"]]["content"].strip()
        for skill in project_skills
        if skill.get("active") and skill.get("id") in SKILL_REGISTRY
    ]
    active_skills = "\n\n".join(active_skill_blocks)
    active_skills_block = f"## Active Skills\n{active_skills or 'No project skills are enabled.'}"
    rules_hash = hashlib.sha256(
        f"{DESIGN_CONTRACT_VERSION}\n{rule_lines}\n{active_skills_block}\n{workspace_path(project_id)}".encode()
    ).hexdigest()[:16]
    cache_key = (project_id, AGENT_INSTRUCTION_VERSION, rules_hash)
    if not force_refresh and cache_key in _instruction_cache:
        return _instruction_cache[cache_key]

    write_agent_skills(project_id, root)
    instruction = (
        "You are Syte's cloud coding agent running persistently on the project's VM. "
        "Work only in this Syte project and optimize for correct, fast, reliable delivery. "
        "Inspect relevant files before edits, use tools instead of guessing, keep changes focused, "
        "and run the smallest useful verification after edits. Never expose credentials. "
        "Do not discuss or configure unrelated model providers.\n\n"
        "You build ANY kind of code the user asks for — libraries, CLIs, APIs, scripts, backends, "
        "mobile, data jobs, infra, tests, or websites. Do NOT assume every request is a website. "
        "Match the stack to the request and existing files. Only when the work is a website / web UI "
        "(Next.js, React, marketing pages, dashboards) you MUST follow the Sycord Design Contract: "
        "shadcn/ui components under components/ui/*, Lucide icons, theme fonts via next/font, Tailwind tokens, "
        "and a complete styled home page. Never ship a bare unstyled web scaffold.\n\n"
        "Tools: list/read/write/delete files; run_command; update_plan (persisted); screenshot_preview "
        "(desktop + phone screenshots of a route — inspect images with vision); ask_question "
        "(interactive user input: answer/input/slider/choice/multi_choice); env_get/env_set/request_env "
        "(project env vars — request_env asks the user when a secret/value is missing); "
        "list_mcp_addons/connect_mcp/call_mcp (available MCP addons); service (preview status/start/stop/"
        "logs); delegate_task for bounded sub-work.\n\n"
        "Use update_plan for multi-step work so the plan is visible in chat and saved. Use ask_question "
        "whenever you need a preference, secret, numeric setting, or choice before continuing. Use "
        "screenshot_preview after UI changes and check BOTH phone and desktop layouts. Continue using "
        "tools until the request is actually complete; the user can interrupt a long turn.\n\n"
        "Never deploy, start, stop, update, or build the production service for testing, and never run "
        "production build commands such as npm run build or next build. Prefer the isolated preview for "
        "visual checks and workspace commands for lint/tests.\n\n"
        "Paths: write_file paths are relative to the workspace root; application source lives in app/. "
        "For Next.js App Router, routes live under app/app/ (e.g. app/app/login/page.tsx). write_file "
        "overwrites the whole file — always send the complete body. After batches of writes, verify with "
        "list_files/read_file. Preview caching: after fixing a compile error, preview_stop then "
        "preview_start before judging the result.\n\n"
        "Website / web UI design contract (mandatory when building websites):\n"
        f"{DESIGN_CONTRACT_MARKDOWN}\n\n"
        f"{themes_prompt_block()}\n\n"
        "shadcn/ui component catalog (import only these — never invent names):\n"
        f"{shadcn_catalog_json()}\n\n"
        f"Syte workspace rules:\n{rule_lines}\n\n"
        f"{active_skills_block}\n\n"
        f"Project workspace root: {workspace_path(project_id)}\n"
        f"Application source: {workspace_path(project_id) / 'app'}\n"
        f"Agent tools and durable data: {root}"
    )
    # Drop older hashes for this project so the cache stays bounded.
    invalidate_instruction_cache(project_id)
    _instruction_cache[cache_key] = instruction
    return instruction



async def write_agent_config(project: dict[str, Any]) -> Path:
    project = await ensure_agent_runtime(project)
    model = await selected_model_metadata(project)
    if not model["api_key"]:
        raise RuntimeError(
            f"No API key configured for active profile {model['profile']}. "
            f"Open AI settings and add the {model['provider_label']} key."
        )
    instruction = await _build_syte_instruction(project["id"])
    agent_instruction_path(project["id"]).write_text(instruction + "\n")
    payload = {
        "runtime": CLOUD_RUNTIME,
        "instruction_version": AGENT_INSTRUCTION_VERSION,
        "model_profile": model["profile"],
        "model": model["model"],
        "provider": model["provider"],
        "workspace_path": str(workspace_path(project["id"]) / "app"),
        "transport": "direct-provider",
        "streaming": True,
    }
    path = agent_config_path(project["id"])
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    await ensure_session(project["id"], model["profile"])
    await update_project(project["id"], {"agent_config_path": str(path)})
    return path


async def start_agent(project_id: str) -> tuple[bool, str, dict[str, Any]]:
    async with _lifecycle_locks[project_id]:
        project = await get_project(project_id)
        if not project:
            return False, "Project not found", {}
        already_running = project.get("agent_status") == "running"
        try:
            await write_agent_config(project)
        except RuntimeError as exc:
            await update_project(project_id, {"agent_status": "error", "agent_last_error": str(exc)})
            return False, str(exc), await get_agent_status(project_id, check_backend=False)
        if already_running:
            return True, "Syte cloud agent is already ready.", await get_agent_status(
                project_id, check_backend=False
            )
        await update_project(
            project_id,
            {"agent_status": "running", "agent_last_started_at": _now(), "agent_last_error": ""},
        )
        _write_log(project_id, "cloud runtime ready")
        await record_agent_event(
            project_id,
            "agent_started",
            title="Cloud agent ready",
            detail="VM-native Syte cloud runtime is ready",
            payload={"runtime": CLOUD_RUNTIME},
            source=CLOUD_RUNTIME,
        )
        status = await get_agent_status(project_id, check_backend=False)
        return True, "Syte cloud agent is ready.", status


async def warm_agent(project_id: str, *, source: str = "api") -> dict[str, Any]:
    ok, message, status = await start_agent(project_id)
    return {"ok": ok, "status": status.get("agent_status", "error"), "message": message,
            "project_id": project_id, "already_warming": False, "source": source}


async def stop_agent(project_id: str) -> tuple[bool, str]:
    from syte.agent_artifacts import cancel_pending_questions, mark_session_stopped

    task = _active_turns.get(project_id)
    if task and not task.done():
        task.cancel()
    if not await get_project(project_id):
        return False, "Project not found"
    await cancel_pending_questions(project_id)
    session_number = await current_session_number(project_id)
    turso_session_id = await current_turso_session_id(project_id)
    stop = await mark_session_stopped(
        project_id,
        reason="stopped",
        source="api",
        session_number=session_number,
        turso_session_id=turso_session_id,
    )
    await update_project(project_id, {"agent_status": "stopped"})
    await record_agent_event(
        project_id,
        "agent_stopped",
        title="Cloud agent stopped",
        detail=f"Session stopped at {stop['stopped_at']}",
        payload={
            "stopped_at": stop["stopped_at"],
            "reason": "stopped",
            "session": session_number,
            "turso_session_id": turso_session_id,
            "stop_id": stop["id"],
        },
        source=CLOUD_RUNTIME,
        turso_session_id=turso_session_id,
    )
    if turso_session_id:
        await close_turso_session(turso_session_id, status="stopped")
    return True, "Syte cloud agent stopped."


async def restart_agent(project_id: str) -> tuple[bool, str, dict[str, Any]]:
    await stop_agent(project_id)
    await clear_conversation(project_id)
    ok, message, status = await start_agent(project_id)
    if ok:
        await record_agent_event(project_id, "agent_restarted", title="Cloud session restarted", source=CLOUD_RUNTIME)
    return ok, message, status


async def interrupt_agent(project_id: str) -> tuple[bool, str]:
    from syte.agent_artifacts import cancel_pending_questions, mark_session_stopped

    task = _active_turns.get(project_id)
    if task and not task.done():
        task.cancel()
        session_number = await current_session_number(project_id)
        turso_session_id = await current_turso_session_id(project_id)
        await cancel_pending_questions(project_id)
        stop = await mark_session_stopped(
            project_id,
            reason="interrupted",
            source="api",
            session_number=session_number,
            turso_session_id=turso_session_id,
        )
        await record_agent_event(
            project_id,
            "agent_stopped",
            title="Cloud agent turn interrupted",
            detail=f"Turn interrupted at {stop['stopped_at']}",
            payload={
                "stopped_at": stop["stopped_at"],
                "reason": "interrupted",
                "session": session_number,
                "turso_session_id": turso_session_id,
                "stop_id": stop["id"],
            },
            source=CLOUD_RUNTIME,
            turso_session_id=turso_session_id,
        )
        if turso_session_id:
            await close_turso_session(turso_session_id, status="cancelled")
        return True, "Active cloud-agent turn interrupted."
    return True, "No active cloud-agent turn."

async def turso_message_sync_status(project_id: str) -> dict[str, Any]:
    """Aggregate "all messages saved to Turso" status for a project's latest session.

    Backs the GUI's green/red "brain" indicator:

    - ``green`` (``all_saved: true``) — every message appended locally for the
      project's most recent chat session has been durably mirrored into the
      shared Turso ``agent_message`` table (see :mod:`syte.turso_store`).
    - ``red`` (``all_saved: false``) — at least one message in the current
      session has not (yet, or ever) been written to Turso — e.g. Turso is
      unreachable, not configured, or a mirror write failed.

    Returns ``turso_configured: false`` (and ``all_saved: true`` — nothing to
    report as unsaved) when Turso itself is not configured, since local
    persistence remains fully intact either way.
    """
    from syte.turso_store import turso_configured

    session_number = await current_session_number(project_id)
    turso_session_id = await current_turso_session_id(project_id)
    configured = await turso_configured()
    status = await session_sync_status(project_id, session_number)
    return {
        "turso_configured": configured,
        "session": session_number,
        "turso_session_id": turso_session_id,
        "total_messages": status["total"],
        "synced_messages": status["synced"],
        "all_saved": status["all_saved"] if configured else True,
    }


async def get_agent_status(
    project_id: str, *, request_base: str = "", check_backend: bool = True
) -> dict[str, Any]:
    project = await get_project(project_id)
    if not project:
        return {}
    project = await ensure_agent_runtime(project)
    model = await selected_model_metadata(project)
    backend = await backend_health(project) if check_backend else {
        "ok": bool(model["api_key"]), "url": model["api_base"], "profile": model["profile"],
        "provider": model["provider_label"], "error": "" if model["api_key"] else "API key missing",
        "probes": [],
    }
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    base_url = request_base.rstrip("/") or (
        build_https_url(gui_domain) if gui_domain
        else build_direct_url(settings.resolved_public_ip, settings.port)
    )
    agent_status_value = project.get("agent_status") or "stopped"
    active = bool(_active_turns.get(project_id) and not _active_turns[project_id].done())
    turso_sync = await turso_message_sync_status(project_id)
    return {
        "agent_runtime": CLOUD_RUNTIME,
        "agent_runtime_type": "cloud",
        "agent_status": "processing" if active else agent_status_value,
        "agent_turso_sync": turso_sync,
        "agent_running": agent_status_value != "stopped",
        "agent_healthy": agent_status_value == "running" and bool(model["api_key"]),
        "agent_warming": False,
        "agent_port": None,
        "agent_local_url": "",
        "agent_proxy_path": "",
        "agent_proxy_url": base_url,
        "agent_workspace_path": str(workspace_path(project_id)),
        "agent_log_path": str(agent_log_path(project_id)),
        "agent_config_path": str(agent_config_path(project_id)),
        "agent_last_started_at": project.get("agent_last_started_at"),
        "agent_last_error": project.get("agent_last_error") or "",
        "agent_backend": backend,
        "agent_model": model,
        "agent_command": cloud_agent_command(),
        "agent_install_ok": True,
        "agent_no_hub_required": True,
        "agent_conversation_id": project.get("agent_conversation_id") or f"cloud-{project_id}",
        "agent_capabilities": [
            "durable_sessions", "restartable_requests", "background_jobs",
            "turso_session_storage", "turso_message_persistence", "last_session_history",
            "terminal", "file_editor", "preview_control", "skills", "provider_retries",
            "planning", "plan_persistence", "subagents", "visible_thinking",
            "screenshot_preview", "vision_screenshots", "interactive_questions",
            "env_access", "mcp_addons", "session_stop_markers", "any_code_type",
            "shadcn_websites",
        ],
    }


async def update_agent_settings(
    project_id: str, *, model_profile: str | None = None, include_status: bool = True
) -> dict[str, Any]:
    if model_profile is not None:
        profile = model_profile.strip() or "syra-base"
        if profile not in PROFILE_PROVIDERS:
            raise ValueError(f"Unknown model profile: {profile}")
        await update_project(project_id, {"agent_model_profile": profile})
        project = await get_project(project_id)
        if project:
            await write_agent_config(project)
    return await get_agent_status(project_id) if include_status else (await get_project(project_id) or {})


TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "list_files", "description": "List files in a workspace directory.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 workspace file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_file", "description": (
        "Create or fully replace a UTF-8 workspace file. This OVERWRITES the whole file, so always send "
        "the complete final body — never a fragment and never an empty string unless you truly mean to "
        "blank the file. Paths are relative to the workspace root. For websites, Next.js App Router routes "
        "live under app/app/. The result reports the verified on-disk size."),
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete a workspace file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "run_command", "description": (
        "Run a shell command in the project workspace (default cwd app/). Use it for install, lint, tests, "
        "grep/ls, and inspection. Do NOT use it to hand-write files with heredocs — use write_file. "
        "Production build commands (npm run build, next build) are blocked."),
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "service", "description": "Inspect the project or control its isolated dev preview. Production lifecycle actions are unavailable to the agent.",
     "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["status", "preview_start", "preview_stop", "run", "logs", "preview_logs"]}, "command": {"type": "string"}, "cwd": {"type": "string"}, "lines": {"type": "integer"}, "timeout": {"type": "integer"}}, "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "update_plan", "description": "Publish or revise a concise execution plan. Persisted to the database and shown in chat as thinking.",
     "parameters": {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}}, "required": ["steps"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "screenshot_preview", "description": (
        "Capture desktop (1280x800) and phone (390x844) screenshots of a preview route. Images are "
        "saved to the DB/disk, shown in chat, and returned for vision inspection. Start the preview first."),
     "parameters": {"type": "object", "properties": {
         "route": {"type": "string", "description": "Path on the preview origin, e.g. / or /login"},
         "url": {"type": "string", "description": "Optional full URL override (must be an allowed preview URL)"},
         "viewports": {"type": "array", "items": {"type": "string", "enum": ["desktop", "phone"]}, "description": "Defaults to both desktop and phone"},
     }, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "ask_question", "description": (
        "Ask the user an interactive question and wait for their answer. Types: answer (free text), "
        "input (short text), slider (numeric), choice (single select), multi_choice (multi select). "
        "Use while planning or mid-work when you need a preference, secret, or setting."),
     "parameters": {"type": "object", "properties": {
         "prompt": {"type": "string"},
         "question_type": {"type": "string", "enum": ["answer", "input", "slider", "choice", "multi_choice"]},
         "options": {"type": "array", "items": {"type": "string"}, "description": "Required for choice / multi_choice"},
         "min_value": {"type": "number"},
         "max_value": {"type": "number"},
         "step_value": {"type": "number"},
         "default_value": {"type": "string"},
     }, "required": ["prompt", "question_type"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "env_get", "description": "Read project environment variables (values are returned; never echo secrets into user-facing replies).",
     "parameters": {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}, "description": "Optional subset of keys; omit to list key names only"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "env_set", "description": "Set or merge project environment variables into the workspace .env and project record.",
     "parameters": {"type": "object", "properties": {
         "env_vars": {"type": "object", "additionalProperties": {"type": "string"}},
         "merge": {"type": "boolean", "description": "Default true — merge with existing vars"},
     }, "required": ["env_vars"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "request_env", "description": (
        "Ask the user (via interactive question) to provide one or more env var values the agent needs. "
        "On answer, values are written into the project env."),
     "parameters": {"type": "object", "properties": {
         "keys": {"type": "array", "items": {"type": "string"}},
         "prompt": {"type": "string", "description": "Optional custom prompt shown to the user"},
     }, "required": ["keys"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_mcp_addons", "description": "List available MCP addons (built-in syte + any registered) and their connection status/tools.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "connect_mcp", "description": "Connect an MCP addon by id or name so its tools become available via call_mcp.",
     "parameters": {"type": "object", "properties": {"addon": {"type": "string"}}, "required": ["addon"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "call_mcp", "description": "Call a tool on a connected MCP addon.",
     "parameters": {"type": "object", "properties": {
         "addon": {"type": "string"},
         "tool": {"type": "string"},
         "arguments": {"type": "object"},
     }, "required": ["addon", "tool"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delegate_task", "description": "Delegate one bounded independent research, review, or implementation task to a subagent sharing this workspace.",
     "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"], "additionalProperties": False}}},
]

SUBAGENT_TOOLS = [
    tool for tool in TOOLS
    if tool["function"]["name"] not in {"delegate_task", "ask_question", "request_env"}
]


async def _execute_tool(
    project_id: str,
    name: str,
    args: dict[str, Any],
    *,
    model: dict[str, str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a tool and always return a JSON-serializable result.

    Workspace helpers may raise (e.g. ValueError("Path not found")). Those must
    become tool results — never abort the provider turn — otherwise the stored
    assistant tool_calls message is left without matching tool responses and
    DeepSeek rejects the next /chat/completions call with HTTP 400.
    """
    from syte.agent_service import run_service_action
    from syte.workspace_api import delete_file, execute_command, list_workspace_files, read_file, write_file

    ctx = context or {}
    try:
        if name == "list_files":
            return {"ok": True, "files": await list_workspace_files(project_id, str(args.get("path") or "app"))}
        if name == "read_file":
            ok, content, mime = await read_file(project_id, str(args["path"]))
            return {"ok": ok, "content": content if isinstance(content, str) else "Binary file", "mime": mime}
        if name == "write_file":
            ok, message = await write_file(project_id, str(args["path"]), str(args["content"]))
            return {"ok": ok, "message": message}
        if name == "delete_file":
            ok, message = await delete_file(project_id, str(args["path"]))
            return {"ok": ok, "message": message}
        if name == "run_command":
            code, output = await execute_command(
                project_id, str(args["command"]), cwd=str(args.get("cwd") or "app"),
                timeout=max(1, min(int(args.get("timeout") or 300), 900)), source="agent",
            )
            return {"ok": code == 0, "exit_code": code, "output": output[-16000:]}
        if name == "service":
            return await run_service_action(
                project_id, str(args["action"]), command=args.get("command"),
                cwd=str(args.get("cwd") or "app"), lines=int(args.get("lines") or 200),
                timeout=int(args.get("timeout") or 300), source="agent",
            )
        if name == "update_plan":
            return await _tool_update_plan(project_id, args, ctx)
        if name == "screenshot_preview":
            return await _tool_screenshot_preview(project_id, args, ctx)
        if name == "ask_question":
            return await _tool_ask_question(project_id, args, ctx)
        if name == "env_get":
            return await _tool_env_get(project_id, args)
        if name == "env_set":
            return await _tool_env_set(project_id, args)
        if name == "request_env":
            return await _tool_request_env(project_id, args, ctx)
        if name == "list_mcp_addons":
            from syte.agent_artifacts import list_mcp_addons

            return {"ok": True, "addons": await list_mcp_addons(project_id)}
        if name == "connect_mcp":
            from syte.agent_artifacts import connect_mcp_addon

            return await connect_mcp_addon(project_id, str(args.get("addon") or ""))
        if name == "call_mcp":
            from syte.agent_artifacts import call_mcp_addon

            return await call_mcp_addon(
                project_id,
                str(args.get("addon") or ""),
                str(args.get("tool") or ""),
                args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
            )
        if name == "delegate_task":
            task = str(args.get("task") or "").strip()
            if not task:
                return {"ok": False, "error": "empty_task", "message": "Provide a delegated task."}
            if not model:
                return {"ok": False, "error": "model_unavailable", "message": "Subagent model is unavailable."}
            return await _run_subagent(project_id, task, model)
        return {"ok": False, "error": "unknown_tool", "message": name}
    except Exception as exc:
        return {
            "ok": False,
            "error": "tool_failed",
            "message": str(exc) or type(exc).__name__,
        }


async def _tool_update_plan(
    project_id: str, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from syte.agent_artifacts import save_plan

    steps = [str(step).strip() for step in (args.get("steps") or []) if str(step).strip()]
    if not steps:
        return {"ok": False, "error": "empty_plan", "message": "Provide at least one plan step."}
    note = str(args.get("note") or "")
    plan = await save_plan(
        project_id,
        steps,
        note=note,
        request_id=str(ctx.get("request_id") or ""),
        session_number=int(ctx.get("session_number") or 0),
        turso_session_id=ctx.get("turso_session_id"),
    )
    return {"ok": True, "plan_id": plan["id"], "steps": steps, "note": note}


async def _tool_screenshot_preview(
    project_id: str, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from syte.agent_artifacts import (
        optimize_png_for_chat,
        save_screenshot_record,
    )
    from syte.preview_access import capture_preview_screenshots, run_access_action

    route = str(args.get("route") or "/").strip() or "/"
    if not route.startswith("/"):
        route = "/" + route
    explicit_url = str(args.get("url") or "").strip()
    viewport_names = args.get("viewports") or ["desktop", "phone"]
    if not isinstance(viewport_names, list) or not viewport_names:
        viewport_names = ["desktop", "phone"]
    viewport_names = [str(v) for v in viewport_names if str(v) in {"desktop", "phone"}]
    if not viewport_names:
        viewport_names = ["desktop", "phone"]

    status = await run_access_action(project_id, "status")
    base = str(status.get("preview_url") or status.get("preview_direct_url") or "")
    target = explicit_url or (base.rstrip("/") + route if base else "")
    if not target:
        return {
            "ok": False,
            "error": "no_preview",
            "message": "Preview URL unavailable — call service preview_start first.",
        }

    raw = await capture_preview_screenshots(target, viewports=tuple(viewport_names))
    # Capture a small thumb from desktop (or first ok viewport) for chat inline.
    thumb_source = None
    for name in ("desktop", "phone"):
        if (raw.get(name) or {}).get("ok") and (raw.get(name) or {}).get("png_bytes"):
            thumb_source = raw[name]["png_bytes"]
            break
    thumb_shot = None
    if thumb_source is not None:
        from syte.preview_access import _capture_screenshot

        # Re-capture at thumb size for a compact chat preview.
        thumb_shot = await _capture_screenshot(target, width=480, height=300, viewport="thumb")

    shots_out: list[dict[str, Any]] = []
    vision_parts: list[dict[str, Any]] = []
    for name in viewport_names:
        shot = raw.get(name) or {}
        if not shot.get("ok") or not shot.get("png_bytes"):
            shots_out.append({
                "viewport": name,
                "ok": False,
                "error": shot.get("error"),
                "message": shot.get("message"),
            })
            continue
        png = shot["png_bytes"]
        thumb_bytes = (thumb_shot or {}).get("png_bytes") if name == "desktop" else None
        record = await save_screenshot_record(
            project_id,
            viewport=name,
            width=int(shot.get("width") or 0),
            height=int(shot.get("height") or 0),
            png_bytes=png,
            route=route,
            url=target,
            request_id=str(ctx.get("request_id") or ""),
            session_number=int(ctx.get("session_number") or 0),
            turso_session_id=ctx.get("turso_session_id"),
            thumb_bytes=thumb_bytes if isinstance(thumb_bytes, (bytes, bytearray)) else None,
        )
        chat_b64 = optimize_png_for_chat(
            thumb_bytes if isinstance(thumb_bytes, (bytes, bytearray)) else png,
            max_bytes=90_000,
        )
        entry = {
            "ok": True,
            "id": record["id"],
            "viewport": name,
            "width": record["width"],
            "height": record["height"],
            "route": route,
            "url": target,
            "bytes": record["bytes"],
            "image_url": f"/api/projects/{project_id}/agent/screenshots/{record['id']}",
            "thumb_url": f"/api/projects/{project_id}/agent/screenshots/{record['id']}?variant=thumb",
            "chat_image_base64": chat_b64,
        }
        shots_out.append(entry)
        if len(png) <= MAX_VISION_IMAGE_BYTES:
            vision_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{record['image_base64']}"},
            })
            vision_parts.append({
                "type": "text",
                "text": f"[{name} {record['width']}x{record['height']}] {route}",
            })

    ok_any = any(s.get("ok") for s in shots_out)
    fail_msgs = [
        str(s.get("message") or "")
        for s in shots_out
        if not s.get("ok") and s.get("message")
    ]
    return {
        "ok": ok_any,
        "action": "screenshot_preview",
        "route": route,
        "url": target,
        "screenshots": [{k: v for k, v in s.items() if k != "chat_image_base64"} for s in shots_out],
        "_chat_screenshots": shots_out,
        "_vision_parts": vision_parts,
        "message": (
            f"Captured {sum(1 for s in shots_out if s.get('ok'))} viewport(s) of {route}. "
            "Inspect the attached images for layout issues."
            if ok_any
            else (
                fail_msgs[0]
                if fail_msgs
                else (
                    "Screenshot capture failed — check preview is running and chromium is installed "
                    "(apt install chromium-browser, or set SYTE_CHROMIUM_PATH)."
                )
            )
        ),
    }


async def _tool_ask_question(
    project_id: str, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from syte.agent_artifacts import create_question, wait_for_answer

    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "empty_prompt", "message": "Provide a question prompt."}
    question = await create_question(
        project_id,
        prompt,
        str(args.get("question_type") or "answer"),
        options=args.get("options") if isinstance(args.get("options"), list) else None,
        min_value=args.get("min_value") if args.get("min_value") is not None else None,
        max_value=args.get("max_value") if args.get("max_value") is not None else None,
        step_value=args.get("step_value") if args.get("step_value") is not None else None,
        default_value=str(args["default_value"]) if args.get("default_value") is not None else None,
        request_id=str(ctx.get("request_id") or ""),
        session_number=int(ctx.get("session_number") or 0),
        turso_session_id=ctx.get("turso_session_id"),
    )
    # Emit event before waiting so the UI can render the widget immediately.
    emit = ctx.get("emit_question")
    if callable(emit):
        await emit(question)

    answer = await wait_for_answer(question["id"], timeout_s=QUESTION_WAIT_TIMEOUT_S)
    if answer is None:
        return {
            "ok": False,
            "error": "question_timeout",
            "question_id": question["id"],
            "message": "User did not answer the question in time.",
        }
    return {
        "ok": True,
        "question_id": question["id"],
        "question_type": question["question_type"],
        "prompt": prompt,
        "answer": answer,
    }


async def _tool_env_get(project_id: str, args: dict[str, Any]) -> dict[str, Any]:
    from syte.workspace import read_env_vars

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}
    env = read_env_vars(project.get("env_vars", "{}"))
    keys = args.get("keys")
    if keys is None:
        return {"ok": True, "keys": sorted(env.keys()), "count": len(env)}
    if not isinstance(keys, list):
        return {"ok": False, "error": "invalid_keys", "message": "keys must be an array of strings"}
    selected = {str(k): env.get(str(k), "") for k in keys}
    return {"ok": True, "env": selected}


async def _tool_env_set(project_id: str, args: dict[str, Any]) -> dict[str, Any]:
    from syte.workspace_api import set_env_vars

    env_vars = args.get("env_vars")
    if not isinstance(env_vars, dict) or not env_vars:
        return {"ok": False, "error": "empty_env", "message": "Provide env_vars object."}
    clean = {str(k): str(v) for k, v in env_vars.items()}
    merge = bool(args["merge"]) if args.get("merge") is not None else True
    ok, message = await set_env_vars(project_id, clean, merge=merge)
    return {"ok": ok, "message": message, "keys": sorted(clean.keys()), "merge": merge}


async def _tool_request_env(
    project_id: str, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    keys = [str(k).strip() for k in (args.get("keys") or []) if str(k).strip()]
    if not keys:
        return {"ok": False, "error": "empty_keys", "message": "Provide at least one env key."}
    prompt = str(args.get("prompt") or "").strip() or (
        "Provide values for these environment variables (KEY=value per line or JSON object): "
        + ", ".join(keys)
    )
    result = await _tool_ask_question(
        project_id,
        {
            "prompt": prompt,
            "question_type": "input",
            "default_value": "\n".join(f"{k}=" for k in keys),
        },
        ctx,
    )
    if not result.get("ok"):
        return result
    raw = str(result.get("answer") or "").strip()
    parsed: dict[str, str] = {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                parsed = {str(k): str(v) for k, v in data.items() if str(k) in keys or not keys}
        except json.JSONDecodeError:
            parsed = {}
    if not parsed:
        for line in raw.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in keys:
                parsed[k] = v.strip()
    if not parsed:
        return {
            "ok": False,
            "error": "parse_failed",
            "message": "Could not parse env values from the answer.",
            "answer": raw[:2000],
            "question_id": result.get("question_id"),
        }
    set_result = await _tool_env_set(project_id, {"env_vars": parsed, "merge": True})
    return {
        "ok": bool(set_result.get("ok")),
        "question_id": result.get("question_id"),
        "keys_set": sorted(parsed.keys()),
        "message": set_result.get("message"),
    }


def _merge_stream_tool_calls(
    acc: dict[int, dict[str, Any]], deltas: list[dict[str, Any]] | None
) -> None:
    """Accumulate OpenAI-style streamed tool_call deltas by index."""
    if not deltas:
        return
    for delta in deltas:
        try:
            index = int(delta.get("index", 0))
        except (TypeError, ValueError):
            index = 0
        slot = acc.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if delta.get("id"):
            slot["id"] = str(delta["id"])
        if delta.get("type"):
            slot["type"] = str(delta["type"])
        fn = delta.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = str(fn["name"])
        if fn.get("arguments"):
            slot["function"]["arguments"] = str(slot["function"].get("arguments") or "") + str(
                fn["arguments"]
            )


async def _parse_sse_completion(
    response: httpx.Response,
    *,
    on_token: TokenEmitter | None = None,
) -> dict[str, Any]:
    """Parse an OpenAI-compatible SSE chat.completion.chunk stream into one message."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_acc: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None

    async for line in response.aiter_lines():
        if not line:
            continue
        if line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            delta = {}
        piece = delta.get("content")
        if piece:
            text = str(piece)
            content_parts.append(text)
            if on_token:
                await on_token(text)
        reason_piece = delta.get("reasoning_content") or delta.get("reasoning")
        if reason_piece:
            reasoning_parts.append(str(reason_piece))
        _merge_stream_tool_calls(tool_acc, delta.get("tool_calls"))
        if choice.get("finish_reason"):
            finish_reason = str(choice["finish_reason"])

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts),
    }
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_acc:
        message["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
    if finish_reason:
        message["_finish_reason"] = finish_reason
    return message


async def _provider_completion(
    model: dict[str, str],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    thinking_config: dict[str, Any] | None = None,
    stream: bool = False,
    on_token: TokenEmitter | None = None,
) -> dict[str, Any]:
    from syte.cloud_agent_store import sanitize_provider_messages

    use_tools = TOOLS if tools is None else tools
    payload: dict[str, Any] = {
        "model": model["model"],
        "messages": sanitize_provider_messages(list(messages)),
        "temperature": float(temperature),
        "stream": bool(stream and on_token is not None),
    }
    if use_tools:
        payload["tools"] = use_tools
        payload["tool_choice"] = "auto"
    # DeepSeek thinking mode requires reasoning_content round-trips after tool
    # calls. Enable only when the thinking slider (or explicit config) asks for it.
    if "deepseek.com" in (model.get("api_base") or ""):
        cfg = thinking_config or {"thinking_enabled": False}
        payload["thinking"] = deepseek_thinking_payload(cfg)
    headers = {"Authorization": f"Bearer {model['api_key']}", "Content-Type": "application/json"}
    url = model["api_base"].rstrip("/") + "/chat/completions"
    error = "Provider request failed"
    client = _get_provider_client()
    for attempt in range(3):
        try:
            if payload["stream"]:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                        await response.aread()
                        await asyncio.sleep(1.5 * (2 ** attempt))
                        continue
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode(errors="replace").strip()[:800]
                        raise RuntimeError(
                            f"Client error '{response.status_code} {response.reason_phrase}' "
                            f"for url '{response.request.url}'"
                            + (f": {detail}" if detail else "")
                        )
                    return await _parse_sse_completion(response, on_token=on_token)

            response = await client.post(url, headers=headers, json=payload)
            if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(1.5 * (2 ** attempt))
                continue
            if response.status_code >= 400:
                detail = (response.text or "").strip()[:800]
                raise RuntimeError(
                    f"Client error '{response.status_code} {response.reason_phrase}' "
                    f"for url '{response.request.url}'"
                    + (f": {detail}" if detail else "")
                )
            data = response.json()
            choices = data.get("choices") or []
            if not choices or not isinstance(choices[0].get("message"), dict):
                raise RuntimeError("Provider returned no assistant message")
            return choices[0]["message"]
        except httpx.HTTPError as exc:
            error = str(exc)
            if attempt < 2:
                await asyncio.sleep(1.5 * (2 ** attempt))
                continue
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
            # Streaming failures fall back once to a non-stream request.
            if payload.get("stream") and attempt < 2:
                payload["stream"] = False
                await asyncio.sleep(0.2)
                continue
            break
    raise RuntimeError(error)


async def _run_subagent(
    project_id: str, task: str, model: dict[str, str]
) -> dict[str, Any]:
    """Run a bounded secondary tool loop and return its findings to the parent."""
    instruction = (
        "You are a focused Syte subagent sharing the parent's project workspace. Complete only the "
        "delegated task. Inspect files and use workspace tools as needed. Do not deploy, start, stop, "
        "update, or build the production service. Prefer the isolated preview for visual checks. "
        "Return concise findings, changes, and verification for the parent agent.\n\n"
        f"Application source: {workspace_path(project_id) / 'app'}"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": task},
    ]
    for step in range(MAX_SUBAGENT_STEPS):
        assistant = await _provider_completion(model, messages, tools=SUBAGENT_TOOLS)
        content = str(assistant.get("content") or "")
        calls = assistant.get("tool_calls") or []
        stored_calls = calls if isinstance(calls, list) else []
        next_assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if stored_calls:
            next_assistant["tool_calls"] = stored_calls
        messages.append(next_assistant)
        if not stored_calls:
            return {"ok": True, "task": task, "result": content.strip() or "Task completed."}
        for call in stored_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            try:
                args = json.loads(function.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            result = await _execute_tool(project_id, name, args, model=model, context={})
            public = {k: v for k, v in result.items() if not str(k).startswith("_")}
            messages.append({
                "role": "tool",
                "tool_call_id": str(call.get("id") or f"subagent-{step}"),
                "content": json.dumps(public, ensure_ascii=False),
            })
    return {
        "ok": False,
        "error": "subagent_step_limit",
        "message": f"Subagent did not finish within {MAX_SUBAGENT_STEPS} steps.",
    }


async def communicate_with_agent(
    project_id: str, message: str, *, model_profile: str | None = None,
    thinking_level: int | str | None = None,
    source: str = "api", auto_start: bool = True, background: bool = False,
) -> dict[str, Any]:
    if background:
        from syte.agent_jobs import submit_agent_request
        return await submit_agent_request(
            project_id,
            message,
            model_profile=model_profile,
            thinking_level=thinking_level,
            source=source,
            auto_start=auto_start,
        )
    from syte.agent_jobs import new_request_id, project_agent_lock
    request_id = new_request_id()
    async with project_agent_lock(project_id):
        return await _communicate_with_agent_impl(
            project_id, message, model_profile=model_profile,
            thinking_level=thinking_level, source=source,
            auto_start=auto_start, request_id=request_id,
        )


async def _communicate_with_agent_impl(
    project_id: str, message: str, *, model_profile: str | None = None,
    thinking_level: int | str | None = None,
    source: str = "api", auto_start: bool = True, emit_request_started: bool = True,
    request_id: str | None = None,
    session_number: int | None = None,
    message_index_start: int = 0,
    turso_session_id: str | None = None,
) -> dict[str, Any]:
    request_id = request_id or f"req-{int(datetime.now().timestamp() * 1000)}"
    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found", "request_id": request_id}
    # Explicit model_profile still persists; thinking_level never does.
    if model_profile and thinking_level is None:
        try:
            await update_agent_settings(project_id, model_profile=model_profile, include_status=False)
        except ValueError as exc:
            return {"ok": False, "error": "invalid_model_profile", "message": str(exc), "request_id": request_id}
    project = await get_project(project_id) or project
    try:
        gen = resolve_thinking_config(
            thinking_level,
            fallback_profile=model_profile or project.get("agent_model_profile"),
        )
    except ValueError as exc:
        return {"ok": False, "error": "invalid_thinking_level", "message": str(exc), "request_id": request_id}

    turn_profile = gen["model_profile"]
    # When the slider overrides the profile, resolve keys for that profile without
    # writing agent_model_profile on the project row.
    if gen.get("override_profile") and turn_profile != project.get("agent_model_profile"):
        project = {**project, "agent_model_profile": turn_profile}

    if auto_start and project.get("agent_status") != "running":
        ok, start_message, _ = await start_agent(project_id)
        if not ok:
            return {"ok": False, "error": "agent_start_failed", "message": start_message, "request_id": request_id}
    model = await selected_model_metadata(project)
    if not model["api_key"]:
        return {"ok": False, "error": "api_key_missing", "message": "Provider API key is not configured", "request_id": request_id}

    # One user message opens one numbered chat session. Every event produced
    # while working the turn is mirrored to a durable Turso session (see
    # syte.turso_store) so clients fetch the whole session by UUID from the
    # Turso access route instead of streaming it live.
    opened_turso_session = False
    if session_number is None:
        session_number = await begin_turn_session(project_id, model["profile"])
        message_index = 0
        turso_session_id = await open_turso_session(
            project_id, session_number=session_number, model_profile=model["profile"],
        )
        opened_turso_session = True
        if turso_session_id:
            await set_turso_session_id(project_id, turso_session_id)
    else:
        message_index = max(0, int(message_index_start or 0))

    def _mark_payload(
        *,
        status: str,
        kind: str,
        base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal message_index
        message_index += 1
        payload = dict(base or {})
        payload.update({
            "request_id": request_id,
            "session": session_number,
            "message_index": message_index,
            "mark": f"S{session_number}{message_index:03d}({status})",
            "mark_status": status,
            "mark_kind": kind,
        })
        return payload

    if emit_request_started:
        await record_agent_event(
            project_id,
            "request_started",
            role="user",
            title="Request",
            detail=message[:4000],
            payload=_mark_payload(
                status="d",
                kind="user",
                base={
                    "session_started": True,
                    "thinking_level": gen.get("thinking_level"),
                    "temperature": gen.get("temperature"),
                },
            ),
            source=source,
            turso_session_id=turso_session_id,
        )
    await _persist_message(
        project_id, request_id, "user", message,
        session_number=session_number, turso_session_id=turso_session_id,
    )
    await record_agent_event(
        project_id,
        "processing",
        title="Processing",
        detail="Cloud agent accepted the durable request",
        payload=_mark_payload(
            status="g",
            kind="status",
            base={
                "thinking_level": gen.get("thinking_level"),
                "thinking_label": gen.get("label"),
                "max_tool_steps": gen.get("max_tool_steps"),
            },
        ),
        source=source,
        turso_session_id=turso_session_id,
    )
    instruction = await _build_syte_instruction(project_id)
    if gen.get("mandatory_plan"):
        instruction = (
            instruction
            + "\n\nThinking mode: Deep. Before the first tool call, call update_plan with a "
            "concrete ordered plan for this request."
        )
    # Only the latest session — prior sessions stay in the activity stream for
    # clients, but are not re-sent to the provider on every turn.
    messages = [{"role": "system", "content": instruction}, *(await conversation_messages(
        project_id, limit=MAX_HISTORY_MESSAGES, last_session_only=True,
    ))]
    current = asyncio.current_task()
    if current:
        _active_turns[project_id] = current

    async def _emit_question(question: dict[str, Any]) -> None:
        await record_agent_event(
            project_id,
            "question",
            role="assistant",
            title="Question",
            detail=str(question.get("prompt") or "")[:4000],
            payload=_mark_payload(
                status="g",
                kind="question",
                base={
                    "question_id": question.get("id"),
                    "question_type": question.get("question_type"),
                    "options": question.get("options") or [],
                    "min_value": question.get("min_value"),
                    "max_value": question.get("max_value"),
                    "step_value": question.get("step_value"),
                    "default_value": question.get("default_value"),
                    "status": "pending",
                },
            ),
            source=source,
            turso_session_id=turso_session_id,
        )

    async def _emit_token(delta: str) -> None:
        if not delta:
            return
        await record_agent_event(
            project_id,
            "token_delta",
            role="assistant",
            title="Stream",
            detail=delta[:2000],
            payload={
                "request_id": request_id,
                "session": session_number,
                "delta": delta,
                "mark_kind": "stream",
            },
            source=source,
            turso_session_id=turso_session_id,
        )

    tool_context: dict[str, Any] = {
        "request_id": request_id,
        "session_number": session_number,
        "turso_session_id": turso_session_id,
        "emit_question": _emit_question,
        "thinking_level": gen.get("thinking_level"),
    }

    max_tool_steps = int(gen.get("max_tool_steps") or 48)
    temperature = float(gen.get("temperature") or 0.2)
    want_stream = bool(gen.get("stream"))

    try:
        for step in itertools.count():
            allow_tools = step < max_tool_steps
            assistant = await _provider_completion(
                model,
                messages,
                tools=TOOLS if allow_tools else [],
                temperature=temperature,
                thinking_config=gen,
                stream=want_stream,
                on_token=_emit_token if want_stream else None,
            )
            content = str(assistant.get("content") or "")
            reasoning = assistant.get("reasoning_content")
            tool_calls = assistant.get("tool_calls") or []
            stored_calls = tool_calls if isinstance(tool_calls, list) else []
            # Cap: ignore tool calls past the thinking-level budget.
            if not allow_tools:
                stored_calls = []
            await _persist_message(
                project_id,
                request_id,
                "assistant",
                content,
                session_number=session_number,
                turso_session_id=turso_session_id,
                tool_calls=stored_calls or None,
                reasoning_content=str(reasoning) if reasoning is not None else None,
            )
            next_assistant: dict[str, Any] = {"role": "assistant", "content": content}
            if reasoning is not None and str(reasoning):
                next_assistant["reasoning_content"] = str(reasoning)
            if stored_calls:
                next_assistant["tool_calls"] = stored_calls
            messages.append(next_assistant)
            visible_thought = str(reasoning or content).strip()
            if stored_calls and visible_thought:
                # Persist thinking text as a plan-shaped artifact when it looks like steps.
                from syte.agent_artifacts import save_plan

                thought_steps = [
                    line.lstrip("0123456789.-) ").strip()
                    for line in visible_thought.splitlines()
                    if line.strip()
                ][:20]
                plan_row = None
                if len(thought_steps) >= 2:
                    plan_row = await save_plan(
                        project_id,
                        thought_steps,
                        note="thinking",
                        request_id=request_id,
                        session_number=session_number,
                        turso_session_id=turso_session_id,
                    )
                await record_agent_event(
                    project_id, "thinking", role="assistant", title="Thinking",
                    detail=visible_thought[:4000],
                    payload=_mark_payload(
                        status="g",
                        kind="plan",
                        base={"plan_id": (plan_row or {}).get("id")},
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
            if not stored_calls:
                reply = content.strip() or "Completed."
                await record_agent_event(
                    project_id, "request_completed", role="assistant", title="Completed",
                    detail=reply[:4000],
                    payload=_mark_payload(
                        status="d",
                        kind="message",
                        base={"reply": reply},
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
                if opened_turso_session:
                    await close_turso_session(turso_session_id, status="completed")
                _write_log(project_id, f"request {request_id} completed in {step + 1} step(s)")
                return {"ok": True, "uuid": project_id, "request_id": request_id,
                        "session": session_number,
                        "turso_session_id": turso_session_id,
                        "conversation_id": f"cloud-{project_id}", "model_profile": model["profile"],
                        "thinking_level": gen.get("thinking_level"),
                        "model": model["model"], "provider": model["provider"], "message": reply,
                        "reply": reply, "state": {"execution_status": "finished", "runtime": CLOUD_RUNTIME}}
            for call in stored_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                try:
                    args = json.loads(function.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                call_id = str(call.get("id") or f"tool-{step}")
                await record_agent_event(
                    project_id, "tool_call_started", title=name, detail=json.dumps(args)[:1000],
                    payload=_mark_payload(
                        status="g",
                        kind="tool",
                        base={"tool": name, "arguments": args, "phase": "started"},
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
                result = await _execute_tool(
                    project_id, name, args, model=model, context=tool_context,
                )
                chat_shots = result.pop("_chat_screenshots", None)
                vision_parts = result.pop("_vision_parts", None)
                if name == "update_plan" and result.get("ok"):
                    plan_steps = [str(item) for item in (result.get("steps") or [])]
                    plan_detail = "\n".join(
                        f"{index}. {item}" for index, item in enumerate(plan_steps, 1)
                    )
                    if result.get("note"):
                        plan_detail = f"{plan_detail}\n\n{result.get('note')}"
                    await record_agent_event(
                        project_id, "thinking", role="assistant", title="Plan",
                        detail=plan_detail[:4000],
                        payload=_mark_payload(
                            status="d",
                            kind="plan",
                            base={"plan_id": result.get("plan_id"), "steps": plan_steps},
                        ),
                        source=source,
                        turso_session_id=turso_session_id,
                    )
                if name == "screenshot_preview" and chat_shots:
                    await record_agent_event(
                        project_id,
                        "screenshot",
                        role="assistant",
                        title=f"Screenshot {args.get('route') or '/'}",
                        detail=str(result.get("message") or "")[:1000],
                        payload=_mark_payload(
                            status="d",
                            kind="screenshot",
                            base={
                                "route": result.get("route"),
                                "url": result.get("url"),
                                "screenshots": [
                                    {
                                        "id": s.get("id"),
                                        "viewport": s.get("viewport"),
                                        "width": s.get("width"),
                                        "height": s.get("height"),
                                        "image_url": s.get("image_url"),
                                        "thumb_url": s.get("thumb_url"),
                                        "chat_image_base64": s.get("chat_image_base64") or "",
                                        "ok": s.get("ok"),
                                    }
                                    for s in chat_shots
                                ],
                            },
                        ),
                        source=source,
                        turso_session_id=turso_session_id,
                    )
                if name in {"ask_question", "request_env"} and result.get("ok"):
                    await record_agent_event(
                        project_id,
                        "question_answered",
                        role="user",
                        title="Answer",
                        detail=str(result.get("answer") or "")[:4000],
                        payload=_mark_payload(
                            status="d",
                            kind="question",
                            base={
                                "question_id": result.get("question_id"),
                                "answer": result.get("answer"),
                            },
                        ),
                        source=source,
                        turso_session_id=turso_session_id,
                    )
                public_result = {k: v for k, v in result.items() if not str(k).startswith("_")}
                encoded = json.dumps(public_result, ensure_ascii=False)
                await _persist_message(
                    project_id, request_id, "tool", encoded,
                    session_number=session_number, turso_session_id=turso_session_id,
                    tool_call_id=call_id,
                )
                messages.append({"role": "tool", "tool_call_id": call_id, "content": encoded})
                # Inject vision parts when the active provider can consume image_url parts.
                supports_vision = "deepseek.com" not in (model.get("api_base") or "")
                if vision_parts and supports_vision:
                    vision_message = {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Screenshot preview images for vision inspection "
                                    f"(route {result.get('route') or '/'}):"
                                ),
                            },
                            *vision_parts,
                        ],
                    }
                    messages.append(vision_message)
                    # Persist a compact text marker (not the raw images) for history.
                    await _persist_message(
                        project_id,
                        request_id,
                        "user",
                        f"[screenshot vision] route={result.get('route')} "
                        f"viewports={[p.get('text') for p in vision_parts if p.get('type') == 'text']}",
                        session_number=session_number,
                        turso_session_id=turso_session_id,
                    )
                elif vision_parts:
                    await _persist_message(
                        project_id,
                        request_id,
                        "user",
                        f"[screenshot saved] route={result.get('route')} "
                        f"(provider has no vision; use screenshot ids/urls from tool result)",
                        session_number=session_number,
                        turso_session_id=turso_session_id,
                    )
                await record_agent_event(
                    project_id, "tool_call_finished", title=name,
                    detail=encoded[:4000],
                    payload=_mark_payload(
                        status="d",
                        kind="tool",
                        base={
                            "tool": name,
                            "ok": bool(result.get("ok")),
                            "phase": "finished",
                        },
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
    except asyncio.CancelledError:
        from syte.agent_artifacts import cancel_pending_questions, mark_session_stopped

        await cancel_pending_questions(project_id)
        stop = await mark_session_stopped(
            project_id,
            reason="cancelled",
            source=source,
            session_number=session_number or 0,
            turso_session_id=turso_session_id,
        )
        await record_agent_event(
            project_id,
            "agent_stopped",
            title="Turn cancelled",
            detail=f"Cancelled at {stop['stopped_at']}",
            payload=_mark_payload(
                status="d",
                kind="status",
                base={
                    "stopped_at": stop["stopped_at"],
                    "reason": "cancelled",
                    "stop_id": stop["id"],
                },
            ),
            source=source,
            turso_session_id=turso_session_id,
        )
        if opened_turso_session:
            await close_turso_session(turso_session_id, status="cancelled")
        raise
    except Exception as exc:
        error = str(exc) or "Cloud agent request failed"
        _write_log(project_id, f"request {request_id} failed: {error}")
        await update_project(project_id, {"agent_last_error": error[:4000]})
        await record_agent_event(
            project_id, "request_failed", title="Request failed", detail=error[:4000],
            payload=_mark_payload(
                status="d",
                kind="error",
                base={
                    "error": "cloud_agent_failed",
                    "retry_message": message[:4000],
                },
            ),
            source=source,
            turso_session_id=turso_session_id,
        )
        if opened_turso_session:
            await close_turso_session(turso_session_id, status="failed")
        return {"ok": False, "request_id": request_id, "session": session_number,
                "turso_session_id": turso_session_id,
                "error": "cloud_agent_failed", "message": error}
    finally:
        if _active_turns.get(project_id) is current:
            _active_turns.pop(project_id, None)


async def test_agent(project_id: str, *, source: str = "api", model_profile: str | None = None) -> dict[str, Any]:
    result = await communicate_with_agent(
        project_id, "Reply with exactly the word 'ok' and nothing else.",
        source=source, model_profile=model_profile,
    )
    passed = bool(result.get("ok") and "ok" in str(result.get("reply") or "").lower())
    return {**result, "ok": passed, "message": "Syte cloud agent test passed" if passed
            else result.get("message", "Agent did not return expected reply"),
            "checks": {"cloud_runtime": True, "backend": passed, "communicate": passed}}
