"""VM-native cloud coding agent inspired by Kilo's durable session model.

The runtime is part of the Syte service process. It stores admitted requests and
conversation messages in SQLite, calls the configured Syra provider directly,
and executes tools through Syte's workspace APIs. No per-project CLI server,
port allocation, or WebSocket transport is required.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from syte.agent_activity import record_agent_event
from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS, profile_provider
from syte.cloud_agent_store import (
    append_message,
    clear_conversation,
    conversation_messages,
    ensure_session,
)
from syte.config import settings
from syte.database import get_project, get_setting, update_project
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.workspace import ensure_workspace, workspace_path

CLOUD_RUNTIME = "kilo-cloud"
# Compatibility for older API consumers that imported this symbol.
OPENHANDS_RUNTIME = CLOUD_RUNTIME
AGENT_INSTRUCTION_VERSION = 5
MAX_HISTORY_MESSAGES = 160
PROVIDER_TIMEOUT_S = 600.0
MAX_SUBAGENT_STEPS = 12

logger = logging.getLogger(__name__)
_lifecycle_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_active_turns: dict[str, asyncio.Task[Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


async def _build_syte_instruction(project_id: str) -> str:
    from syte.agent_skills import build_agent_rules, read_access_config, write_agent_skills

    root = agent_root(project_id)
    access = await read_access_config(project_id, root)
    write_agent_skills(project_id, root)
    rule_lines = "\n".join(
        f"- {item['name']}: {item['rule']}"
        for item in build_agent_rules(project_id, access)
        if item.get("rule")
    )
    return (
        "You are Syte's cloud coding agent running persistently on the project's VM. "
        "Work only in this Syte project and optimize for correct, fast, reliable delivery. "
        "Inspect relevant files before edits, use tools instead of guessing, keep changes focused, "
        "and run the smallest useful verification after edits. Never expose credentials. "
        "Do not discuss or configure unrelated model providers.\n\n"
        "You can list, read, write, and delete files; run workspace commands; maintain a visible "
        "plan; delegate a bounded research or implementation task to a subagent; and inspect, start, "
        "or stop the isolated development preview. The project source is in app/. The preview is a "
        "separate dev server with hot reload and its URL, rendered HTML, screenshot, and logs are "
        "available through the preview tools. Use update_plan for multi-step work and delegate_task "
        "when an independent focused task benefits from a second pass.\n\n"
        "Use preview_start and the preview access tools to test changes. Never deploy, start, stop, "
        "update, or build the production service for testing, and never run production build commands "
        "such as npm run build or next build. Use workspace commands for linting and tests. Continue "
        "using tools until the request is actually complete; there is no short turn deadline, and the "
        "user can explicitly interrupt a long-running turn. Then return a concise result with "
        "verification and any real blocker.\n\n"
        "For website creation or redesign work, make the home page a complete, styled experience. "
        "Integrate it with the project's existing typography, colors, spacing, components, and responsive "
        "behavior; do not leave a bare scaffold or an unstyled utility page. Verify the home page in the "
        "development preview at desktop and mobile sizes.\n\n"
        "Paths and file writes: write_file paths are relative to the workspace root, and the Next.js "
        "project root is the workspace app/ folder. App Router routes therefore live under app/app/ "
        "(for example app/app/login/page.tsx and app/app/dashboard/page.tsx); config files sit at "
        "app/tsconfig.json, app/tailwind.config.js, and app/app/globals.css. write_file overwrites the "
        "entire file and reports the verified on-disk size, so always send the complete body and never an "
        "empty string unless you mean to blank a file. After a batch of writes, list_files or read_file to "
        "confirm they persisted before you rely on them.\n\n"
        "Next.js App Router correctness: never create _document.tsx or _app.tsx (those are Pages Router "
        "and are ignored by the App Router — use app/app/layout.tsx for the root layout and providers). "
        "For the @/ import alias, ensure tsconfig.json has \"baseUrl\": \".\" and \"paths\": {\"@/*\": [\"./*\"]}. "
        "Keep app/app/globals.css with the @tailwind base/components/utilities directives plus any CSS "
        "variables, and import it once in app/app/layout.tsx. In tailwind.config.js set content to scan the "
        "real source globs, e.g. ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'], and define theme "
        "colors that match the CSS variables. Verify with the dev preview and lint, not a production build.\n\n"
        "Preview caching: the dev server caches failed compilations, so after fixing a build or module "
        "error that previously 500'd, restart it with preview_stop then preview_start to force a clean "
        "recompile before judging the result.\n\n"
        f"Syte workspace rules:\n{rule_lines}\n\n"
        f"Project workspace root: {workspace_path(project_id)}\n"
        f"Application source: {workspace_path(project_id) / 'app'}\n"
        f"Agent tools and durable data: {root}"
    )


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
        "streaming": False,
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
    task = _active_turns.get(project_id)
    if task and not task.done():
        task.cancel()
    if not await get_project(project_id):
        return False, "Project not found"
    await update_project(project_id, {"agent_status": "stopped"})
    await record_agent_event(project_id, "agent_stopped", title="Cloud agent stopped", source=CLOUD_RUNTIME)
    return True, "Syte cloud agent stopped."


async def restart_agent(project_id: str) -> tuple[bool, str, dict[str, Any]]:
    await stop_agent(project_id)
    await clear_conversation(project_id)
    ok, message, status = await start_agent(project_id)
    if ok:
        await record_agent_event(project_id, "agent_restarted", title="Cloud session restarted", source=CLOUD_RUNTIME)
    return ok, message, status


async def interrupt_agent(project_id: str) -> tuple[bool, str]:
    task = _active_turns.get(project_id)
    if task and not task.done():
        task.cancel()
        return True, "Active cloud-agent turn interrupted."
    return True, "No active cloud-agent turn."


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
    status = project.get("agent_status") or "stopped"
    active = bool(_active_turns.get(project_id) and not _active_turns[project_id].done())
    return {
        "agent_runtime": CLOUD_RUNTIME,
        "agent_runtime_type": "cloud",
        "agent_status": "processing" if active else status,
        "agent_running": status != "stopped",
        "agent_healthy": status == "running" and bool(model["api_key"]),
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
            "durable_sessions", "restartable_requests", "background_jobs", "tagged_activity_stream",
            "terminal", "file_editor", "preview_control", "skills", "provider_retries",
            "planning", "subagents", "visible_thinking",
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
        "blank the file. Paths are relative to the workspace root; the Next.js project root is the app/ "
        "folder, so App Router routes live under app/app/ (e.g. app/app/login/page.tsx), not app/login/page.tsx. "
        "The result reports the verified on-disk size; treat ok=false or an empty-file warning as a real "
        "failure and re-read the file to confirm before moving on."),
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete a workspace file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "run_command", "description": (
        "Run a shell command in the project workspace (default cwd app/). Use it for install, lint, tests, "
        "grep/ls, and inspection. Do NOT use it to hand-write files with heredocs — use write_file, which "
        "verifies the result. Production build commands (npm run build, next build) are intentionally "
        "blocked; verify with the dev preview and lint instead."),
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "service", "description": "Inspect the project or control its isolated dev preview. Production lifecycle actions are unavailable to the agent.",
     "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["status", "preview_start", "preview_stop", "run", "logs", "preview_logs"]}, "command": {"type": "string"}, "cwd": {"type": "string"}, "lines": {"type": "integer"}, "timeout": {"type": "integer"}}, "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "update_plan", "description": "Publish or revise a concise execution plan for multi-step work.",
     "parameters": {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}}, "required": ["steps"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delegate_task", "description": "Delegate one bounded independent research, review, or implementation task to a subagent sharing this workspace.",
     "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"], "additionalProperties": False}}},
]

SUBAGENT_TOOLS = [tool for tool in TOOLS if tool["function"]["name"] != "delegate_task"]


async def _execute_tool(
    project_id: str,
    name: str,
    args: dict[str, Any],
    *,
    model: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a tool and always return a JSON-serializable result.

    Workspace helpers may raise (e.g. ValueError("Path not found")). Those must
    become tool results — never abort the provider turn — otherwise the stored
    assistant tool_calls message is left without matching tool responses and
    DeepSeek rejects the next /chat/completions call with HTTP 400.
    """
    from syte.agent_service import run_service_action
    from syte.workspace_api import delete_file, execute_command, list_workspace_files, read_file, write_file

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
            steps = [str(step).strip() for step in (args.get("steps") or []) if str(step).strip()]
            if not steps:
                return {"ok": False, "error": "empty_plan", "message": "Provide at least one plan step."}
            return {"ok": True, "steps": steps, "note": str(args.get("note") or "")}
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


async def _provider_completion(
    model: dict[str, str],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from syte.cloud_agent_store import sanitize_provider_messages

    payload = {
        "model": model["model"],
        "messages": sanitize_provider_messages(list(messages)),
        "tools": tools or TOOLS,
        "tool_choice": "auto",
        "stream": False,
        "temperature": 0.1,
    }
    # DeepSeek thinking mode requires reasoning_content round-trips after tool
    # calls. Explicitly disable thinking for the non-reasoning syra-base model
    # so multi-turn tool loops stay OpenAI-compatible without that field.
    if "deepseek.com" in (model.get("api_base") or ""):
        payload["thinking"] = {"type": "disabled"}
    headers = {"Authorization": f"Bearer {model['api_key']}", "Content-Type": "application/json"}
    error = "Provider request failed"
    for attempt in range(3):
        try:
            timeout = httpx.Timeout(PROVIDER_TIMEOUT_S, connect=15.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    model["api_base"].rstrip("/") + "/chat/completions", headers=headers, json=payload
                )
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
            result = await _execute_tool(project_id, name, args, model=model)
            messages.append({
                "role": "tool",
                "tool_call_id": str(call.get("id") or f"subagent-{step}"),
                "content": json.dumps(result, ensure_ascii=False),
            })
    return {
        "ok": False,
        "error": "subagent_step_limit",
        "message": f"Subagent did not finish within {MAX_SUBAGENT_STEPS} steps.",
    }


async def communicate_with_agent(
    project_id: str, message: str, *, model_profile: str | None = None,
    source: str = "api", auto_start: bool = True, background: bool = False,
) -> dict[str, Any]:
    if background:
        from syte.agent_jobs import submit_agent_request
        return await submit_agent_request(project_id, message, model_profile=model_profile,
                                          source=source, auto_start=auto_start)
    from syte.agent_jobs import new_request_id, project_agent_lock
    request_id = new_request_id()
    async with project_agent_lock(project_id):
        return await _communicate_with_agent_impl(
            project_id, message, model_profile=model_profile, source=source,
            auto_start=auto_start, request_id=request_id,
        )


async def _communicate_with_agent_impl(
    project_id: str, message: str, *, model_profile: str | None = None,
    source: str = "api", auto_start: bool = True, emit_request_started: bool = True,
    request_id: str | None = None,
) -> dict[str, Any]:
    request_id = request_id or f"req-{int(datetime.now().timestamp() * 1000)}"
    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found", "request_id": request_id}
    if model_profile:
        try:
            await update_agent_settings(project_id, model_profile=model_profile, include_status=False)
        except ValueError as exc:
            return {"ok": False, "error": "invalid_model_profile", "message": str(exc), "request_id": request_id}
    project = await get_project(project_id) or project
    if auto_start and project.get("agent_status") != "running":
        ok, start_message, _ = await start_agent(project_id)
        if not ok:
            return {"ok": False, "error": "agent_start_failed", "message": start_message, "request_id": request_id}
    model = await selected_model_metadata(project)
    if not model["api_key"]:
        return {"ok": False, "error": "api_key_missing", "message": "Provider API key is not configured", "request_id": request_id}
    if emit_request_started:
        await record_agent_event(project_id, "request_started", role="user", title="Request",
                                 detail=message[:4000], payload={"request_id": request_id}, source=source)
    await append_message(project_id, request_id, "user", message)
    await record_agent_event(project_id, "processing", title="Processing",
                             detail="Cloud agent accepted the durable request",
                             payload={"request_id": request_id}, source=source)
    instruction = await _build_syte_instruction(project_id)
    messages = [{"role": "system", "content": instruction}, *(await conversation_messages(
        project_id, limit=MAX_HISTORY_MESSAGES
    ))]
    current = asyncio.current_task()
    if current:
        _active_turns[project_id] = current
    try:
        for step in itertools.count():
            assistant = await _provider_completion(model, messages)
            content = str(assistant.get("content") or "")
            reasoning = assistant.get("reasoning_content")
            tool_calls = assistant.get("tool_calls") or []
            stored_calls = tool_calls if isinstance(tool_calls, list) else []
            await append_message(
                project_id,
                request_id,
                "assistant",
                content,
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
                await record_agent_event(
                    project_id, "thinking", role="assistant", title="Plan",
                    detail=visible_thought[:4000], payload={"request_id": request_id}, source=source,
                )
            if not stored_calls:
                reply = content.strip() or "Completed."
                await record_agent_event(project_id, "request_completed", role="assistant", title="Completed",
                                         detail=reply[:4000], payload={"request_id": request_id, "reply": reply}, source=source)
                _write_log(project_id, f"request {request_id} completed in {step + 1} step(s)")
                return {"ok": True, "uuid": project_id, "request_id": request_id,
                        "conversation_id": f"cloud-{project_id}", "model_profile": model["profile"],
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
                if name == "update_plan":
                    plan_steps = [str(item).strip() for item in (args.get("steps") or []) if str(item).strip()]
                    plan_detail = "\n".join(f"{index}. {item}" for index, item in enumerate(plan_steps, 1))
                    await record_agent_event(
                        project_id, "thinking", role="assistant", title="Plan",
                        detail=plan_detail[:4000], payload={"request_id": request_id}, source=source,
                    )
                await record_agent_event(project_id, "tool_call_started", title=name, detail=json.dumps(args)[:1000],
                                         payload={"request_id": request_id, "tool": name, "arguments": args}, source=source)
                result = await _execute_tool(project_id, name, args, model=model)
                encoded = json.dumps(result, ensure_ascii=False)
                await append_message(project_id, request_id, "tool", encoded, tool_call_id=call_id)
                messages.append({"role": "tool", "tool_call_id": call_id, "content": encoded})
                await record_agent_event(project_id, "tool_call_finished", title=name,
                                         detail=encoded[:4000], payload={"request_id": request_id, "tool": name,
                                         "ok": bool(result.get("ok"))}, source=source)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = str(exc) or "Cloud agent request failed"
        _write_log(project_id, f"request {request_id} failed: {error}")
        await update_project(project_id, {"agent_last_error": error[:4000]})
        await record_agent_event(project_id, "request_failed", title="Request failed", detail=error[:4000],
                                 payload={"request_id": request_id, "error": "cloud_agent_failed",
                                          "retry_message": message[:4000]}, source=source)
        return {"ok": False, "request_id": request_id, "error": "cloud_agent_failed", "message": error}
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
