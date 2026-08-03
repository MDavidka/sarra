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
import os
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from syte.agent_activity import record_agent_event
from syte.ai_providers import (
    DEFAULT_PROFILE,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    provider_chat_completion_url,
    profile_provider,
)
from syte.cloud_agent_store import (
    append_message,
    clear_conversation,
    conversation_messages,
    current_session_number,
    current_turso_session_id,
    ensure_latest_session,
    ensure_session,
    mark_message_synced,
    session_sync_status,
    set_turso_session_id,
)
from syte.config import settings
from syte.database import get_project, get_setting, set_setting, update_project
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.thinking_levels import (
    apply_prompt_cache_markers,
    build_model_thinking_params,
    resolve_thinking_config,
)
from syte.turso_store import close_session as close_turso_session
from syte.turso_store import open_session as open_turso_session
from syte.turso_store import record_message as record_turso_message
from syte.workspace import ensure_workspace, workspace_path

CLOUD_RUNTIME = "kilo-cloud"
# Compatibility for older API consumers that imported this symbol.
OPENHANDS_RUNTIME = CLOUD_RUNTIME
AGENT_INSTRUCTION_VERSION = 19
MAX_HISTORY_MESSAGES = 160
PROVIDER_TIMEOUT_S = 600.0
# Attempts per provider call. Quota (429) retries and same-provider model
# rotation share this budget, so it is higher than a plain transport retry.
PROVIDER_MAX_ATTEMPTS = 5
MAX_SUBAGENT_STEPS = 28
MAX_SUBAGENT_RESEARCH_STEPS = 14
# Wall-clock cap for an entire subagent loop (LLM rounds + tools). Prevents
# worker leaks when a single step hangs forever despite the step limit (DAV-202).
SUBAGENT_TIMEOUT_S = 600.0
SUBAGENT_RESEARCH_TIMEOUT_S = 180.0
# Bound parallel background subagents per project (cost + workspace races).
MAX_BACKGROUND_SUBAGENTS_PER_PROJECT = 2
MAX_STORED_SUBAGENT_RESULTS = 48
QUESTION_WAIT_TIMEOUT_S = 1800.0
# Preview screenshots are expensive (browser launch + vision payload) and models
# tend to re-shoot unchanged routes. Cap them per turn and refuse repeats that
# cannot show anything new (no write since the last capture).
MAX_SCREENSHOTS_PER_TURN = 4
SCREENSHOT_MIN_INTERVAL_S = 25.0
# Cap inline vision payloads so provider requests stay bounded.
MAX_VISION_IMAGE_BYTES = 700_000

logger = logging.getLogger(__name__)
_lifecycle_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_active_turns: dict[str, asyncio.Task[Any]] = {}
_provider_client: httpx.AsyncClient | None = None
# Cache only the static (cache-stable) instruction prefix — never session memory.
_instruction_cache: dict[tuple[str, int, str], str] = {}
# Background subagent tasks keyed by ``{project_id}:{task_id}``.
_background_subagents: dict[str, asyncio.Task[Any]] = {}
# Completed/cancelled background results keyed the same way (parent can await).
_background_subagent_results: dict[str, dict[str, Any]] = {}
# Write ownership of workspace files while a subagent is live:
# ``{project_id: {normalized_path: task_id}}``. The main agent declares the
# scope up front (delegate_task.files) so neither it nor a second subagent can
# edit a file another subagent is already working on.
_subagent_file_locks: dict[str, dict[str, str]] = defaultdict(dict)
# Fire-and-forget work that must not block turn completion (index, preview).
_bg_tasks: set[asyncio.Task[Any]] = set()
# Turso message mirrors — drained briefly before end-of-turn resync.
_turso_mirror_tasks: set[asyncio.Task[Any]] = set()


def _track_bg_task(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _store_subagent_result(key: str, result: dict[str, Any]) -> None:
    """Remember a background subagent outcome so the parent can collect it."""
    _background_subagent_results[key] = result
    overflow = len(_background_subagent_results) - MAX_STORED_SUBAGENT_RESULTS
    if overflow <= 0:
        return
    for stale in list(_background_subagent_results.keys())[:overflow]:
        _background_subagent_results.pop(stale, None)


def _normalize_scope_path(path: Any) -> str:
    """Normalize a declared file-scope path for comparison."""
    raw = str(path or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.lstrip("/")
    while "//" in raw:
        raw = raw.replace("//", "/")
    return raw


def normalize_scope_files(files: Any, *, limit: int = 40) -> list[str]:
    """Return a de-duplicated, normalized list of declared scope paths."""
    if isinstance(files, str):
        candidates = [part for part in files.replace(",", "\n").splitlines()]
    elif isinstance(files, (list, tuple, set)):
        candidates = list(files)
    else:
        candidates = []
    out: list[str] = []
    for candidate in candidates:
        norm = _normalize_scope_path(candidate)
        if norm and norm not in out:
            out.append(norm)
        if len(out) >= limit:
            break
    return out


def _reserve_subagent_files(
    project_id: str, task_id: str, files: list[str]
) -> tuple[list[str], list[dict[str, str]]]:
    """Claim write ownership of ``files`` for one subagent.

    Returns ``(reserved, conflicts)``. Nothing is reserved when any path is
    already owned by another live subagent, so two agents can never be told
    they may edit the same file.
    """
    owned = _subagent_file_locks[project_id]
    conflicts = [
        {"path": path, "task_id": owned[path]}
        for path in files
        if owned.get(path) and owned[path] != task_id
    ]
    if conflicts:
        return [], conflicts
    for path in files:
        owned[path] = task_id
    return list(files), []


def _release_subagent_files(project_id: str, task_id: str) -> list[str]:
    """Release every path reserved by one subagent."""
    owned = _subagent_file_locks.get(project_id) or {}
    released = [path for path, owner in owned.items() if owner == task_id]
    for path in released:
        owned.pop(path, None)
    if not owned:
        _subagent_file_locks.pop(project_id, None)
    return released


def subagent_file_owner(project_id: str, path: str) -> str | None:
    """Return the subagent task id that reserved ``path``, if any."""
    return (_subagent_file_locks.get(project_id) or {}).get(_normalize_scope_path(path))


def subagent_file_scope(project_id: str) -> dict[str, str]:
    """Snapshot of reserved paths → owning subagent task id."""
    return dict(_subagent_file_locks.get(project_id) or {})


def _count_background_subagents(project_id: str) -> int:
    prefix = f"{project_id}:"
    return sum(
        1
        for key, task in _background_subagents.items()
        if key.startswith(prefix) and task and not task.done()
    )


def cancel_background_subagents(project_id: str) -> int:
    """Cancel in-flight background subagents for a project (DAV-198)."""
    cancelled = 0
    prefix = f"{project_id}:"
    for key, task in list(_background_subagents.items()):
        if not key.startswith(prefix):
            continue
        _background_subagents.pop(key, None)
        if task and not task.done():
            task.cancel()
            cancelled += 1
            task_id = key.split(":", 1)[-1]
            _store_subagent_result(
                key,
                {
                    "ok": False,
                    "error": "subagent_cancelled",
                    "status": "cancelled",
                    "task_id": task_id,
                    "message": "Subagent cancelled",
                },
            )
    return cancelled


def _track_turso_mirror_task(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    _turso_mirror_tasks.add(task)
    task.add_done_callback(_turso_mirror_tasks.discard)
    return task


async def _drain_turso_mirrors(*, timeout_s: float = 5.0) -> None:
    """Let fire-and-forget Turso mirrors settle before end-of-turn resync."""
    try:
        from syte.agent_activity import drain_turso_event_mirrors

        await drain_turso_event_mirrors(timeout_s=min(2.0, float(timeout_s)))
    except Exception:
        pass
    pending = [task for task in list(_turso_mirror_tasks) if not task.done()]
    if not pending:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=max(0.1, float(timeout_s)),
        )
    except asyncio.TimeoutError:
        pass

TokenEmitter = Callable[[str], Awaitable[None]]

MAX_TOOL_RESULT_CHARS = 16_000
MAX_REASONING_HISTORY_CHARS = 4_000
MAX_PROJECT_BRIEF_CHARS = 4_000
MAX_STATIC_SKILL_CHARS = 4_000
TOOL_TRUNCATION_NOTE = "\n… [truncated for LLM context — re-read a narrower path or ask for a specific section]"

# mtime-backed cache so hot instruction builds avoid repeated sync disk reads.
_project_brief_cache: dict[str, tuple[float, str]] = {}
_syterules_cache: dict[str, tuple[float, str]] = {}


def _truncate_for_llm(text: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(TOOL_TRUNCATION_NOTE))
    return text[:keep] + TOOL_TRUNCATION_NOTE


def _truncate_tool_payload(result: dict[str, Any], *, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """JSON-encode a tool result, capping size so one tool cannot blow the context window."""
    public = {k: v for k, v in result.items() if not str(k).startswith("_")}
    encoded = json.dumps(public, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return encoded
    # Prefer truncating large string fields before chopping JSON mid-token.
    for key in ("content", "output", "files", "results", "message", "detail", "raw"):
        value = public.get(key)
        if isinstance(value, str) and len(value) > 2_000:
            public[key] = _truncate_for_llm(value, max(2_000, max_chars // 2))
            public["truncated"] = True
            encoded = json.dumps(public, ensure_ascii=False)
            if len(encoded) <= max_chars:
                return encoded
        if isinstance(value, list) and len(value) > 80:
            public[key] = value[:80]
            public["truncated"] = True
            public["truncated_count"] = len(value)
            encoded = json.dumps(public, ensure_ascii=False)
            if len(encoded) <= max_chars:
                return encoded
    return _truncate_for_llm(encoded, max_chars)


def _raise_if_cancelled() -> None:
    """Cooperative cancel checkpoint between provider/tool steps (DAV-131)."""
    task = asyncio.current_task()
    if task is not None and task.cancelled():
        raise asyncio.CancelledError()


_FAILURE_TITLES = {
    "rate_limited": "Rate limited",
    "circuit_open": "Provider temporarily unavailable",
    "provider_error": "Provider error",
}


def _failure_metadata(exc: BaseException) -> dict[str, Any]:
    """Normalize a turn failure into UI-renderable fields.

    Structured agent errors (``AgentError`` subclasses) carry an error type,
    a retryable flag, and JSON-safe detail such as ``retry_after_s``. Anything
    else degrades to a generic non-retryable failure so the UI never has to
    parse error strings.
    """
    from syte.agent_errors import AgentError

    error_type = "cloud_agent_failed"
    retryable = False
    detail: dict[str, Any] = {}
    if isinstance(exc, AgentError):
        error_type = exc.error_type or error_type
        retryable = bool(exc.retryable)
        for key, value in (exc.detail or {}).items():
            name = str(key)
            if name in {"ok", "error", "message", "retryable", "error_type"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                detail[name] = value
            elif isinstance(value, (list, tuple)):
                detail[name] = [
                    item for item in value if isinstance(item, (str, int, float, bool))
                ]
    return {
        "error_type": error_type,
        "retryable": retryable,
        "detail": detail,
        "title": _FAILURE_TITLES.get(error_type, "Request failed"),
    }


def _system_message_for_provider(static: str, dynamic: str, model: dict[str, Any]) -> dict[str, Any]:
    """Build the system message; Anthropic gets a cache breakpoint after the static prefix."""
    provider = str(model.get("provider") or "").lower()
    model_name = str(model.get("model") or "").lower()
    if "anthropic" in provider or "claude" in model_name:
        return {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": static,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": dynamic},
            ],
        }
    return {"role": "system", "content": f"{static}\n\n{dynamic}"}


def _read_project_brief(project_id: str) -> str:
    """Load durable project brief for system context (DAV-137 / DAV-179)."""
    root = workspace_path(project_id)
    candidates = (
        root / ".syte" / "PROJECT_BRIEF.md",
        root / "PROJECT_BRIEF.md",
        root / "app" / "PROJECT_BRIEF.md",
    )
    newest_key = (0.0, 0)
    newest_path: Path | None = None
    for path in candidates:
        try:
            if path.is_file():
                st = path.stat()
                key = (st.st_mtime, int(st.st_size))
                if key >= newest_key:
                    newest_key = key
                    newest_path = path
        except OSError:
            continue
    if newest_path is None:
        _project_brief_cache.pop(project_id, None)
        return ""
    cached = _project_brief_cache.get(project_id)
    if cached and cached[0] == newest_key:
        return cached[1]
    try:
        text = newest_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        _project_brief_cache.pop(project_id, None)
        return ""
    clipped = text[:MAX_PROJECT_BRIEF_CHARS]
    _project_brief_cache[project_id] = (newest_key, clipped)
    return clipped


def _seed_project_brief(project_id: str) -> str:
    """Create a short brief from README / package.json when none exists yet."""
    root = workspace_path(project_id)
    brief_dir = root / ".syte"
    brief_path = brief_dir / "PROJECT_BRIEF.md"
    if brief_path.is_file():
        return _read_project_brief(project_id)

    parts: list[str] = ["# Project brief", ""]
    pkg = root / "app" / "package.json"
    if not pkg.is_file():
        pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            name = data.get("name") or project_id
            desc = data.get("description") or ""
            deps = sorted({*(data.get("dependencies") or {}), *(data.get("devDependencies") or {})})
            parts.append(f"Name: {name}")
            if desc:
                parts.append(f"Description: {desc}")
            if deps:
                parts.append("Stack hints: " + ", ".join(deps[:24]))
            parts.append("")
        except (OSError, json.JSONDecodeError):
            pass
    for readme in (root / "app" / "README.md", root / "README.md"):
        try:
            if readme.is_file():
                excerpt = readme.read_text(encoding="utf-8", errors="replace").strip()[:1500]
                if excerpt:
                    parts.append("## README excerpt")
                    parts.append(excerpt)
                    break
        except OSError:
            continue
    if len(parts) <= 2:
        parts.append(
            f"Workspace project `{project_id}`. Update this file as the durable product/stack brief."
        )
    text = "\n".join(parts).strip()[:MAX_PROJECT_BRIEF_CHARS]
    try:
        brief_dir.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass
    return text


def _project_brief_block(project_id: str) -> str:
    brief = _read_project_brief(project_id) or _seed_project_brief(project_id)
    if not brief:
        return ""
    return (
        "## Project brief (durable context — prefer this over re-discovering the stack)\n"
        f"{brief}\n"
    )


_UI_PATH_MARKERS = (
    "components/",
    "page.tsx",
    "page.jsx",
    "layout.tsx",
    "layout.jsx",
    "globals.css",
)


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
        _project_brief_cache.clear()
        _syterules_cache.clear()
        return
    for key in [k for k in _instruction_cache if k[0] == project_id]:
        del _instruction_cache[key]
    _project_brief_cache.pop(project_id, None)
    _syterules_cache.pop(project_id, None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _mirror_message_to_turso(
    *,
    turso_session_id: str,
    project_id: str,
    local_id: int,
    role: str,
    content: str,
    session_number: int,
    request_id: str,
    tool_call_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    retries: int = 3,
) -> bool:
    """Mirror one local message to Turso with short retries (DAV-145)."""
    delays = (0.15, 0.4, 0.9)
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
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
            if saved:
                await mark_message_synced(local_id, synced=True)
                return True
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Turso mirror attempt %s failed for message %s (session %s): %s",
                attempt + 1,
                local_id,
                turso_session_id,
                exc,
            )
        if attempt < len(delays):
            await asyncio.sleep(delays[attempt])
    if last_exc is not None:
        logger.error(
            "Failed to mirror agent message %s to Turso session %s after retries",
            local_id,
            turso_session_id,
            exc_info=last_exc,
        )
    else:
        logger.error(
            "Failed to mirror agent message %s to Turso session %s (no row returned)",
            local_id,
            turso_session_id,
        )
    return False


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
    """Append one message locally, then mirror it to Turso without blocking TTFT.

    Every message the cloud agent produces (user / assistant / tool) is written
    to the local durable store first (never fails the turn). When a durable Turso
    session is open, the mirror runs as a background task so streaming / first
    token is not gated on remote round-trips or retries. Rows stay
    ``turso_synced=0`` until the mirror succeeds; :func:`_resync_unsynced_messages`
    at turn end retries any leftovers for the brain indicator.
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
        _track_turso_mirror_task(
            asyncio.create_task(
                _mirror_message_to_turso(
                    turso_session_id=turso_session_id,
                    project_id=project_id,
                    local_id=local_id,
                    role=role,
                    content=content,
                    session_number=session_number,
                    request_id=request_id,
                    tool_call_id=tool_call_id,
                    tool_calls=tool_calls,
                    reasoning_content=reasoning_content,
                )
            )
        )
    return local_id


async def _resync_unsynced_messages(
    project_id: str,
    *,
    session_number: int,
    turso_session_id: str | None,
    limit: int = 40,
) -> int:
    """Retry Turso mirror for local rows left ``turso_synced=0`` (DAV-145)."""
    if not turso_session_id:
        return 0
    from syte.cloud_agent_store import list_unsynced_messages

    pending = await list_unsynced_messages(
        project_id, session_number=session_number, limit=limit,
    )
    synced = 0
    for row in pending:
        ok = await _mirror_message_to_turso(
            turso_session_id=turso_session_id,
            project_id=project_id,
            local_id=int(row["id"]),
            role=str(row.get("role") or "assistant"),
            content=str(row.get("content") or ""),
            session_number=int(row.get("session_number") or session_number),
            request_id=str(row.get("request_id") or ""),
            tool_call_id=row.get("tool_call_id"),
            tool_calls=row.get("tool_calls") if isinstance(row.get("tool_calls"), list) else None,
            reasoning_content=row.get("reasoning_content"),
            retries=2,
        )
        if ok:
            synced += 1
    return synced


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
    """Resolve API key for a profile: settings first, then ``secret_env``."""
    resolved = await resolve_profile_api_key(profile)
    return str(resolved.get("api_key") or "")


def mask_secret(value: str, *, keep: int = 4) -> str:
    key = (value or "").strip()
    if not key:
        return ""
    if len(key) <= keep * 2:
        return "••••"
    return f"{key[:keep]}…{key[-keep:]}"


async def resolve_profile_api_key(profile: str) -> dict[str, str]:
    """Return the effective key plus where it came from (settings | env | none)."""
    spec = profile_provider(profile)
    if spec.get("local"):
        return {
            "profile": profile,
            "setting_key": spec["setting_key"],
            "secret_env": spec["secret_env"],
            # Ollama accepts any bearer token; the local profile never needs a
            # user-supplied credential.
            "api_key": "ollama",
            "source": "local",
            "settings_set": "",
            "env_set": "",
            "settings_hint": "local",
            "env_hint": "",
            "api_key_hint": "local",
        }
    setting_key = spec["setting_key"]
    secret_env = spec["secret_env"]
    from_settings = (await get_setting(setting_key, "")).strip()
    from_env = (os.environ.get(secret_env) or "").strip()
    if profile == "9router":
        from syte.model_catalog import configured_models

        if not any(row["enabled"] for row in await configured_models()):
            # A disabled custom model must never be selected by the runtime.
            from_settings = ""
            from_env = ""
    # Legacy shared OpenRouter setting (pre-split ultra key).
    legacy = ""
    if profile == "syra-ultra" and not from_settings:
        legacy = (await get_setting("agent_openrouter_api_key", "")).strip()

    if from_settings:
        source = "settings"
        api_key = from_settings
    elif from_env:
        source = "env"
        api_key = from_env
    elif legacy:
        source = "settings"
        api_key = legacy
    else:
        source = "none"
        api_key = ""

    return {
        "profile": profile,
        "setting_key": setting_key,
        "secret_env": secret_env,
        "api_key": api_key,
        "source": source,
        "settings_set": "1" if from_settings or legacy else "",
        "env_set": "1" if from_env else "",
        "settings_hint": mask_secret(from_settings or legacy),
        "env_hint": mask_secret(from_env),
        "api_key_hint": mask_secret(api_key),
    }


async def migrate_provider_lineup_keys() -> dict[str, Any]:
    """Keep the provider migration hook safe across lineup changes."""
    return {"migrated": False, "reason": "three_model_lineup"}


async def provider_key_status() -> list[dict[str, str | bool]]:
    """Public/diagnostic view of settings + env keys (values masked)."""
    await migrate_provider_lineup_keys()
    rows: list[dict[str, str | bool]] = []
    for name in PROFILE_ORDER:
        resolved = await resolve_profile_api_key(name)
        spec = PROFILE_PROVIDERS[name]
        rows.append({
            "profile": name,
            "display_name": spec.get("display_name") or name,
            "label": spec["label"],
            "model": spec["model"],
            "api_base": spec["api_base"],
            "setting_key": resolved["setting_key"],
            "secret_env": resolved["secret_env"],
            "source": resolved["source"],
            "api_key_set": bool(resolved["api_key"]),
            "settings_set": bool(resolved["settings_set"]),
            "env_set": bool(resolved["env_set"]),
            "settings_hint": resolved["settings_hint"],
            "env_hint": resolved["env_hint"],
            "api_key_hint": resolved["api_key_hint"],
            "env_value_present": bool(resolved["env_set"]),
        })
    return rows


async def bridge_settings() -> dict[str, Any]:
    from syte.ai_providers import aliyun_api_base_for_key
    from syte.model_catalog import configured_models, model_profile

    await migrate_provider_lineup_keys()
    default_profile = (
        await get_setting("agent_default_model_profile", DEFAULT_PROFILE)
    ).strip() or DEFAULT_PROFILE
    resolved_keys = await asyncio.gather(*[resolve_profile_api_key(name) for name in PROFILE_ORDER])
    profiles: dict[str, dict[str, Any]] = {}
    custom_models = await configured_models()
    enabled_custom_models = [row for row in custom_models if row["enabled"]]
    for name, resolved in zip(PROFILE_ORDER, resolved_keys, strict=True):
        spec = PROFILE_PROVIDERS[name]
        entry: dict[str, str | int | float] = {
            **spec,
            "api_key": resolved["api_key"],
            "key_source": resolved["source"],
            "api_key_hint": resolved["api_key_hint"],
            "settings_set": bool(resolved["settings_set"]),
            "env_set": bool(resolved["env_set"]),
            "settings_hint": resolved["settings_hint"],
            "env_hint": resolved["env_hint"],
        }
        # Match Aliyun endpoint to the key billing mode (Token Plan vs DashScope PAYG).
        if name == "syra-ultra" and resolved["api_key"]:
            entry["api_base"] = aliyun_api_base_for_key(str(resolved["api_key"]))
        if name == "9router":
            primary = enabled_custom_models[0] if enabled_custom_models else None
            entry["model"] = primary["name"] if primary else ""
            entry["enabled"] = bool(primary)
            entry["thinking_levels"] = ",".join(map(str, primary["thinking_levels"])) if primary else "1,2,3,4,5"
        profiles[name] = entry
    # Each enabled catalog record is a selectable agent profile. Disabled models
    # are deliberately omitted so stale requests cannot route to them.
    base_custom = profiles["9router"]
    for row in enabled_custom_models:
        profile = model_profile(row["id"])
        profiles[profile] = {
            **base_custom,
            "profile": profile,
            "model": row["name"],
            "enabled": True,
            "thinking_levels": ",".join(map(str, row["thinking_levels"])),
        }
    if default_profile not in profiles:
        default_profile = DEFAULT_PROFILE
    active = profiles[default_profile]
    return {
        "default_profile": default_profile,
        "profiles": profiles,
        "api_base": active["api_base"],
        "api_key": active["api_key"],
        "provider": active["provider"],
        # Legacy keys — selected model handles both thinking and building.
        "builder_profile": default_profile,
        "thinker_profile": None,
        "syra_nano_model": profiles["syra-nano"]["model"],
        "syra_havy_model": profiles["syra-havy"]["model"],
        "syra_ultra_model": profiles["syra-ultra"]["model"],
        "syra_nano_api_key": profiles["syra-nano"]["api_key"],
        "syra_havy_api_key": profiles["syra-havy"]["api_key"],
        "syra_ultra_api_key": profiles["syra-ultra"]["api_key"],
        "provider_keys": [
            {
                "profile": name,
                "source": profiles[name].get("key_source") or "none",
                "api_key_set": bool(profiles[name].get("api_key")),
                "settings_set": bool(profiles[name].get("settings_set")),
                "env_set": bool(profiles[name].get("env_set")),
                "secret_env": profiles[name].get("secret_env") or "",
                "api_key_hint": profiles[name].get("api_key_hint") or "",
                "settings_hint": profiles[name].get("settings_hint") or "",
                "env_hint": profiles[name].get("env_hint") or "",
            }
            for name in PROFILE_ORDER
        ],
    }


def _metadata_from_bridge(bridge: dict[str, Any], profile: str) -> dict[str, Any]:
    resolved = profile if profile in bridge["profiles"] else DEFAULT_PROFILE
    if profile.startswith("9router:") and profile not in bridge["profiles"]:
        # Do not fall through to a default provider if a catalog model was
        # disabled or removed after a project selected it.
        spec = {**bridge["profiles"]["9router"], "profile": profile, "model": "", "api_key": ""}
    else:
        spec = bridge["profiles"].get(resolved, bridge["profiles"][DEFAULT_PROFILE])
    meta: dict[str, Any] = {
        "profile": str(spec.get("profile") or resolved),
        "provider": spec["provider"],
        "label": spec.get("label") or "",
        "provider_label": spec.get("label") or "",
        "model": spec["model"],
        "api_base": spec["api_base"],
        "api_key": spec["api_key"],
        "role": spec.get("role") or "",
        "secret_env": spec.get("secret_env") or "",
        "key_source": spec.get("key_source") or ("settings" if spec.get("api_key") else "none"),
        "api_key_hint": spec.get("api_key_hint") or mask_secret(str(spec.get("api_key") or "")),
    }
    for key in ("max_tokens", "completion_token_param", "max_history_messages", "max_tool_result_chars"):
        if key in spec and spec[key] is not None:
            meta[key] = int(spec[key])
    return meta


async def is_available_model_profile(profile: str) -> bool:
    """Whether a static or enabled catalog profile may be selected by an agent."""
    if profile in PROFILE_PROVIDERS and profile != "9router":
        return True
    bridge = await bridge_settings()
    return profile in bridge["profiles"] and bool(bridge["profiles"][profile].get("enabled", True))


async def selected_model_metadata(project: dict[str, Any]) -> dict[str, Any]:
    bridge = await bridge_settings()
    profile = str(project.get("agent_model_profile") or bridge["default_profile"])
    return _metadata_from_bridge(bridge, profile)


async def model_metadata_for_profile(profile: str | None) -> dict[str, Any]:
    bridge = await bridge_settings()
    return _metadata_from_bridge(bridge, (profile or DEFAULT_PROFILE).strip() or DEFAULT_PROFILE)


def _apply_api_key_override(
    model: dict[str, Any],
    override_api_key: str,
) -> dict[str, Any]:
    """Return a copy of *model* with the provider API key replaced by *override_api_key*.

    Also corrects ``api_base`` for Aliyun profiles so Token Plan (``sk-sp-``)
    and DashScope PAYG (``sk-``) keys route to the right endpoint.
    """
    from syte.ai_providers import aliyun_api_base_for_key

    updated = dict(model)
    updated["api_key"] = override_api_key
    updated["key_source"] = "request"
    updated["api_key_hint"] = mask_secret(override_api_key)
    profile = str(updated.get("profile") or "")
    if profile == "syra-ultra":
        updated["api_base"] = aliyun_api_base_for_key(override_api_key)
    return updated


def _model_history_limit(model: dict[str, Any] | None) -> int:
    if model and model.get("max_history_messages"):
        return max(8, int(model["max_history_messages"]))
    return MAX_HISTORY_MESSAGES


def _model_tool_result_chars(model: dict[str, Any] | None) -> int:
    if model and model.get("max_tool_result_chars"):
        return max(2_000, int(model["max_tool_result_chars"]))
    return MAX_TOOL_RESULT_CHARS


def _model_max_tokens(model: dict[str, Any] | None) -> int | None:
    if model and model.get("max_tokens"):
        return max(256, int(model["max_tokens"]))
    return None


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


def _project_metadata_block(project: dict[str, Any] | None) -> str:
    """Stable project facts from Mongo/SQLite metadata — never requires a file scan."""
    project = project or {}
    name = project.get("name") or project.get("id") or "unknown"
    domain = project.get("domain") or project.get("preview_domain") or "not set"
    deploy = project.get("deploy_type") or "shell"
    status = project.get("status") or "unknown"
    return (
        "## Project metadata (never re-scan for this)\n"
        f"Name: {name}\n"
        f"Domain: {domain}\n"
        f"Deploy type: {deploy}\n"
        f"Service status: {status}\n"
    )


def _read_syterules(project_id: str) -> str:
    """Load optional `.syterules` from workspace root or app/."""
    root = workspace_path(project_id)
    candidates = (root / ".syterules", root / "app" / ".syterules")
    newest_key = (0.0, 0)
    newest_path: Path | None = None
    for candidate in candidates:
        try:
            if candidate.is_file():
                st = candidate.stat()
                key = (st.st_mtime, int(st.st_size))
                if key >= newest_key:
                    newest_key = key
                    newest_path = candidate
        except OSError:
            continue
    if newest_path is None:
        _syterules_cache.pop(project_id, None)
        return ""
    cached = _syterules_cache.get(project_id)
    if cached and cached[0] == newest_key:
        return cached[1]
    try:
        text = newest_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        _syterules_cache.pop(project_id, None)
        return ""
    block = f"## Project-specific rules (.syterules):\n{text}"
    _syterules_cache[project_id] = (newest_key, block)
    return block


def _website_enforcement_block(*, is_website: bool) -> str:
    if is_website:
        return (
            "## MANDATORY for this project: Next.js + shadcn/ui (NOT HeroUI)\n"
            "This is a Next.js website project. You MUST:\n"
            "- Use Next.js App Router (routes in app/app/ — double app/ is correct)\n"
            "- Import shadcn/ui components from @/components/ui/* only\n"
            "- Resolve every component's real API with shadcn_registry (info → search → view/docs) "
            "BEFORE writing UI, and install missing primitives with shadcn_registry(action=add). "
            "Never write a component's props or sub-component names from memory, and never "
            "hand-roll a primitive the registry already provides\n"
            "- State the creative direction (and one rejected alternative) before code; avoid the "
            "banned slop signature (indigo/violet accent, purple gradient, centered hero + three "
            "icon cards, gradient text, glassmorphism)\n"
            "- Compose pages from the 57 cataloged components; never use shadcn Blocks or block templates\n"
            "- Keep direct Radix imports inside components/ui wrappers; preserve keyboard/focus/ARIA behavior\n"
            "- Use Tailwind CSS with design system tokens (var(--color-primary), etc.)\n"
            "- NEVER use HeroUI, NextUI, Chakra, MUI, Ant Design, or invent alternate UI kits\n"
            "- Never ship bare unstyled HTML scaffolds\n"
            "- ONE stack only. Never create files that conflict with the Next.js app: no "
            "index.html, no public/index.html, no vite.config.*, no src/main.tsx, and no "
            "<script src=\"https://cdn.tailwindcss.com\"> — Tailwind is compiled from "
            "app/tailwind.config.ts + app/app/globals.css. Syte quarantines these on deploy\n"
            "- Keep exactly one page.tsx / layout.tsx / globals.css per route directory; never "
            "add a duplicate copy at the app/ root beside app/app/\n"
            "- Radix is used only inside app/components/ui/* wrappers (shadcn primitives); "
            "application code imports @/components/ui/*\n"
            "- Find the real file to edit with semantic_search / search_code / list_files BEFORE writing\n"
            "- After UI changes: preview_start → inspect_preview (DevTools load/console) first; "
            "screenshot_preview only when you need visual layout review. Use browse_url against the "
            "dev server URL when you need the browser console for a non-default route or port\n"
            "- Follow the Design Contract below strictly\n"
        )
    return (
        "## Project type: general code\n"
        "This is not detected as a Next.js website project. Match the stack to the existing "
        "files and user request. Only apply the Design Contract if the user explicitly asks "
        "for a website / web UI. When building a website, use Next.js + shadcn/ui — never HeroUI.\n"
    )


def _build_static_instruction(
    project_id: str,
    *,
    rule_lines: str,
    active_skills_block: str,
    project_meta: str,
    website_enforcement: str,
    syterules: str,
) -> str:
    """Build the cacheable instruction prefix (design contract, tools, skills, rules)."""
    from syte.design_contract import (
        DESIGN_CONTRACT_MARKDOWN,
        creative_direction_block,
        shadcn_catalog_json,
        slop_signature_block,
        themes_prompt_block,
    )

    root = agent_root(project_id)
    parts = [
        "You are Syte's cloud coding agent running persistently on the project's VM. "
        "Work only in this Syte project and optimize for correct, fast, reliable delivery. "
        "Inspect relevant files before edits, use tools instead of guessing, keep changes focused, "
        "and run the smallest useful verification after edits. Never expose credentials. "
        "Do not discuss or configure unrelated model providers.\n",
        website_enforcement,
        project_meta,
        "You build ANY kind of code the user asks for — libraries, CLIs, APIs, scripts, backends, "
        "mobile, data jobs, infra, tests, or websites. Do NOT assume every request is a website "
        "unless this project's enforcement block says otherwise. "
        "Match the stack to the request and existing files. Only when the work is a website / web UI "
        "(Next.js, React, marketing pages, dashboards) you MUST follow the Sycord Design Contract: "
        "shadcn/ui components under components/ui/*, Lucide icons, theme fonts via next/font, Tailwind tokens, "
        "and a complete styled home page. Never ship a bare unstyled web scaffold. "
        "Do NOT use HeroUI/NextUI/Chakra/MUI/Ant Design for websites.\n",
        "Tools: list/read/write/delete files; run_command; update_plan (persisted); inspect_preview "
        "(preferred: Chromium DevTools — load_ok, console/page errors, network failures, page_summary "
        "without screenshots); screenshot_preview "
        "(desktop + phone images when you need visual layout review); "
        "ask_question "
        "(interactive user input: answer/input/slider/choice/multi_choice); env_get/env_set/request_env "
        "(project env vars — request_env asks the user when a secret/value is missing); "
        "list_mcp_addons/connect_mcp/call_mcp (available MCP addons); "
        "mcp_credentials/call_external_api (stored external service credentials — check what APIs "
        "are available before asking the user for keys); web_search (find current web "
        "info); fetch_url (READ any public page/API you found — docs, references, changelogs); "
        "browse_url (real headless Chromium against any URL incl. the dev server on 127.0.0.1: "
        "console logs, page exceptions, failed requests); shadcn_registry "
        "(info/search/view/docs/add/apply_preset — the live component registry); "
        "semantic_search (meaning-based workspace lookup); search_code (ripgrep); service (preview "
        "status/start/stop/logs); update_plan with per-step assignee main|subagent; delegate_task + "
        "await_subagent for subagent-assigned work using configured public profiles.\n"
        "Keep `syra/memory.md` current with a short summary, stack, key paths, and conventions. "
        "Read it early; update it after lasting decisions so you do not re-scan basics.\n",
        "File targeting (mandatory for real changes): before editing UI/behavior, locate the exact path with "
        "semantic_search and/or search_code (or list_files on app/app and app/components). Prefer recently "
        "touched / prompt-matched indexed files. Do not invent paths or create parallel duplicates "
        "(e.g. writing app/page.tsx when the App Router file is app/app/page.tsx). Edit the file that "
        "actually renders the feature, then verify on disk with read_file.\n",
        "Token efficiency: prefer diffs and symbol lookups over dumping whole files or the repo. "
        "Start with `git diff --stat` / `--name-only` before reading full contents. Honor `.aiignore`. "
        "Command output is pre-filtered (passing tests / progress noise stripped) — re-run with a "
        "narrower command if you need a specific line.\n",
        "Use update_plan for multi-step work so the plan is visible in chat and saved. For any "
        "request that needs 3+ distinct steps, call update_plan BEFORE other tools and assign each "
        "step to main or subagent. main = you execute on the user's chosen model; subagent = call "
        "delegate_task using configured public profiles. Prefer subagent for "
        "parallel research, file location, audits, and bounded isolated edits; keep orchestration, "
        "design decisions, final integration, and user questions on main. For a new website "
        "or substantive redesign, ask one concise batched question BEFORE planning when brand, audience, "
        "content, visual direction, pages, or behavior is materially unclear. If nothing is unclear, "
        "start with update_plan; after an answer, update_plan is still required before inspection or edits. "
        "Use ask_question whenever you need a preference, secret, numeric setting, or choice. "
        "Delegation file scope (mandatory): every delegate_task call must list in `files` the exact "
        "workspace paths the subagent may create or edit — decide the split BEFORE delegating. Those "
        "paths are locked to that subagent: you and other subagents are blocked from writing them "
        "until it finishes, so never delegate two tasks that share a file and never edit a reserved "
        "file yourself — await the subagent first. "
        "Screenshots are rate-limited: at most "
        f"{MAX_SCREENSHOTS_PER_TURN} captures per turn, and a repeat of a route you already captured "
        "is refused while no file has been written since. Use inspect_preview for load/console "
        "verification and screenshot_preview only when you need visual layout review across "
        "viewports — never poll it to 'check again'. "
        "After website edits, ALWAYS call inspect_preview with include_console=true (default) to confirm "
        "the route loads and the browser console has no errors/exceptions; if console or load issues appear, "
        "fix them before finishing. Continue using tools until the request is actually complete; "
        "the user can interrupt a long turn.\n",
        "If the user asks to 'create hosting', do not just create a static site — use the integration instead. Use generative thinking to create something genuinely creative and content-specific, avoiding general AI slop. After a subagent task completes (via await_subagent or a synchronous delegate_task), you must immediately trigger code verification (e.g. npm run lint) and start the preview. Never deploy, start, stop, update, or build the production service for testing, and never run "
        "production build commands such as npm run build or next build. Prefer the isolated preview for "
        "visual checks and workspace commands for lint/tests.\n",
        "Paths: write_file paths are relative to the workspace root; application source lives in app/. "
        "For Next.js App Router, routes live under app/app/ (e.g. app/app/login/page.tsx). write_file "
        "overwrites the whole file — always send the complete body. After batches of writes, verify with "
        "list_files/read_file. Preview caching: after fixing a compile error, preview_stop then "
        "preview_start before judging the result.\n",
        "Website / web UI design contract (mandatory when building websites):\n"
        f"{DESIGN_CONTRACT_MARKDOWN}\n",
        f"{creative_direction_block()}\n",
        f"{slop_signature_block()}\n",
        f"{themes_prompt_block()}\n",
        "shadcn/ui component catalog (import only these — never invent names). This list is the "
        "allowed surface, NOT the API reference: resolve real sub-components, props and imports "
        "with shadcn_registry (action=view / action=docs) before you write the file.\n"
        f"{shadcn_catalog_json()}\n",
        f"Syte workspace rules:\n{rule_lines}\n",
        f"{active_skills_block}\n",
        f"Project workspace root: {workspace_path(project_id)}\n"
        f"Application source: {workspace_path(project_id) / 'app'}\n"
        f"Agent tools and durable data: {root}",
    ]
    if syterules:
        parts.append(syterules)
    return "\n".join(parts)


async def _build_syte_instruction_parts(
    project_id: str,
    *,
    force_refresh: bool = False,
) -> tuple[str, str]:
    """Return ``(static_prefix, dynamic_suffix)`` for prompt-cache-friendly assembly.

    Static portions (design contract, tool docs, skills, rules, project brief) are
    cached. Session memory + design profile stay dynamic so summaries reach the LLM.
    """
    from syte.agent_skills import (
        build_agent_rules,
        get_project_skills,
        read_access_config,
        write_agent_skills,
    )
    from syte.design_contract import DESIGN_CONTRACT_VERSION
    from syte.nextjs_layout import is_nextjs_repo

    root = agent_root(project_id)
    access = await read_access_config(project_id, root)
    rules = [item for item in build_agent_rules(project_id, access) if item.get("rule")]
    rule_lines = "\n".join(f"- {item['name']}: {item['rule']}" for item in rules)
    project_skills = await get_project_skills(project_id)
    active_skills_list = [skill for skill in project_skills if skill.get("active")]
    # Keep skill bodies short in the static/cacheable prefix; large skill text
    # busts Anthropic prefix cache and bloats every turn (DAV-182).
    skill_snippets: list[str] = []
    for skill in active_skills_list:
        raw = str(skill.get("content") or "").strip()
        if not raw or "\x00" in raw:
            continue
        name = str(skill.get("name") or skill.get("id") or "skill").strip()
        body = raw if len(raw) <= MAX_STATIC_SKILL_CHARS else (
            raw[: MAX_STATIC_SKILL_CHARS - len(TOOL_TRUNCATION_NOTE)] + TOOL_TRUNCATION_NOTE
        )
        skill_snippets.append(f"### Skill: {name}\n{body}")
    active_skills = "\n\n".join(skill_snippets)
    active_skills_block = f"## Active Skills\n{active_skills or 'No project skills are enabled.'}"
    syterules = _read_syterules(project_id)
    project = await get_project(project_id)
    app_root = workspace_path(project_id) / "app"
    is_website = is_nextjs_repo(app_root) or is_nextjs_repo(workspace_path(project_id))
    project_meta = _project_metadata_block(project)
    website_enforcement = _website_enforcement_block(is_website=is_website)
    project_brief = _project_brief_block(project_id)

    rules_hash = hashlib.sha256(
        f"{DESIGN_CONTRACT_VERSION}\n{rule_lines}\n{active_skills_block}\n"
        f"{website_enforcement}\n{project_meta}\n{syterules}\n{project_brief}\n"
        f"{workspace_path(project_id)}".encode()
    ).hexdigest()[:16]
    cache_key = (project_id, AGENT_INSTRUCTION_VERSION, rules_hash)
    if not force_refresh and cache_key in _instruction_cache:
        static = _instruction_cache[cache_key]
    else:
        write_agent_skills(
            project_id,
            root,
            custom_skills=[skill for skill in project_skills if skill.get("custom")],
        )
        static = _build_static_instruction(
            project_id,
            rule_lines=rule_lines,
            active_skills_block=active_skills_block,
            project_meta=project_meta,
            website_enforcement=website_enforcement,
            syterules=syterules,
        )
        if project_brief:
            static = f"{static}\n\n{project_brief}"
        # Drop older hashes for this project so the cache stays bounded.
        invalidate_instruction_cache(project_id)
        _instruction_cache[cache_key] = static

    from syte.agent_memory import (
        design_profile_prompt_block,
        get_design_profile,
        latest_session_meta,
        latest_summary,
        lookup_workspace_paths,
        memory_context_block,
        workspace_map_block,
    )
    from syte.project_memory_file import (
        ensure_project_memory_md,
        project_memory_md_prompt_block,
    )

    try:
        ensure_project_memory_md(project_id)
    except OSError:
        pass

    summary = await latest_summary(project_id)
    meta = await latest_session_meta(project_id)
    active_files = list((meta or {}).get("active_files") or [])
    memory_block = memory_context_block(summary, active_files)
    file_memory_block = project_memory_md_prompt_block(project_id)
    design_block = design_profile_prompt_block(await get_design_profile(project_id))
    # Prefer layout/page/component index hits so the model edits real files.
    index_hits = await lookup_workspace_paths(
        project_id,
        tags=["page", "layout", "navbar", "hero", "colors"],
        limit=20,
    )
    if len(index_hits) < 8:
        more = await lookup_workspace_paths(project_id, limit=20)
        seen = {str(item.get("path")) for item in index_hits}
        for item in more:
            path = str(item.get("path") or "")
            if path and path not in seen:
                index_hits.append(item)
                seen.add(path)
            if len(index_hits) >= 20:
                break
    map_block = workspace_map_block(index_hits, limit=20)
    dynamic = "\n\n".join(
        part
        for part in (file_memory_block, design_block, memory_block, map_block)
        if part and str(part).strip()
    ).strip()
    return static, dynamic


async def _build_syte_instruction(
    project_id: str,
    *,
    force_refresh: bool = False,
) -> str:
    """Assemble full system instruction with fresh dynamic memory every call."""
    static, dynamic = await _build_syte_instruction_parts(
        project_id, force_refresh=force_refresh,
    )
    return f"{static}\n\n{dynamic}" if dynamic else static


async def write_agent_config(
    project: dict[str, Any],
    *,
    override_api_key: str | None = None,
) -> Path:
    project = await ensure_agent_runtime(project)
    model = await selected_model_metadata(project)
    if override_api_key and override_api_key.strip():
        model = _apply_api_key_override(model, override_api_key.strip())
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


async def start_agent(
    project_id: str,
    *,
    override_api_key: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    async with _lifecycle_locks[project_id]:
        project = await get_project(project_id)
        if not project:
            return False, "Project not found", {}
        already_running = project.get("agent_status") == "running"
        try:
            await write_agent_config(project, override_api_key=override_api_key)
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
        # Warm the workspace file index in the background — never block TTFT (DAV-128).
        try:
            _track_bg_task(asyncio.create_task(_warm_workspace_index(project_id)))
        except Exception:
            logger.exception("workspace index warm schedule failed for %s", project_id)
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


async def _warm_workspace_index(project_id: str) -> None:
    """Scan workspace into the index when empty — runs off the chat hot path."""
    try:
        from syte.agent_memory import lookup_workspace_paths, scan_workspace_index

        existing = await lookup_workspace_paths(project_id, limit=1)
        if not existing:
            await scan_workspace_index(project_id)
    except Exception:
        logger.exception("workspace index warm failed for %s", project_id)


async def _post_turn_preview_checks(
    project_id: str,
    *,
    model: dict[str, str],
    tool_context: dict[str, Any],
    source: str,
    turso_session_id: str | None,
    request_id: str,
    session_number: int,
) -> None:
    """Preview readiness + soft route validation after the turn already completed."""
    def _payload(kind: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
        out = {
            "request_id": request_id,
            "session": session_number,
            "mark_kind": kind,
            "async_post_turn": True,
        }
        if base:
            out.update(base)
        return out

    try:
        from syte.preview_health import wait_for_preview_ready

        ok_preview, preview_url, _status = await wait_for_preview_ready(
            project_id, max_wait_s=5, poll_s=1.0,
        )
        if not ok_preview:
            await record_agent_event(
                project_id,
                "status",
                title="Preview did not start",
                detail=(
                    "Agent completed work but preview is not reachable. "
                    "Check logs or run service preview_start."
                ),
                payload=_payload(
                    "status",
                    {"preview_unreachable": True, "preview_url": preview_url},
                ),
                source=source,
                turso_session_id=turso_session_id,
            )
        elif not tool_context.get("_screenshot_captured"):
            await _execute_tool(
                project_id,
                "screenshot_preview",
                {"route": "/", "viewports": ["desktop"]},
                model=model,
                context=tool_context,
            )
        routes_dir = workspace_path(project_id) / "app" / "app"
        if not routes_dir.exists() or not await asyncio.to_thread(
            lambda: any(routes_dir.rglob("page.tsx"))
        ):
            await record_agent_event(
                project_id,
                "status",
                title="No app/app/ routes found",
                detail=(
                    "Agent may have created routes in the wrong location. "
                    "Expected app/app/page.tsx"
                ),
                payload=_payload("status", {"no_routes_detected": True}),
                source=source,
                turso_session_id=turso_session_id,
            )
    except Exception:
        logger.debug("preview health check failed", exc_info=True)


async def _sweep_orphan_subagents(project_id: str) -> int:
    """Close local subagent rows whose worker no longer exists.

    A service restart or a hard interrupt leaves rows stuck at ``running``,
    which made ``await_subagent`` wait on nothing and the GUI show a task as
    permanently in-flight. Only safe when this process has no live background
    subagent for the project.
    """
    if _count_background_subagents(project_id):
        return 0
    from syte.subagent_store import mark_orphans_cancelled

    return await mark_orphans_cancelled(project_id)


async def warm_agent(project_id: str, *, source: str = "api") -> dict[str, Any]:
    ok, message, status = await start_agent(project_id)
    await _sweep_orphan_subagents(project_id)
    return {"ok": ok, "status": status.get("agent_status", "error"), "message": message,
            "project_id": project_id, "already_warming": False, "source": source}


async def stop_agent(project_id: str) -> tuple[bool, str]:
    from syte.agent_artifacts import cancel_pending_questions, mark_session_stopped

    cancel_background_subagents(project_id)
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


async def interrupt_agent(
    project_id: str,
    *,
    turso_session_id: str | None = None,
) -> tuple[bool, str]:
    """Interrupt the active turn and close the superseded Turso session.

    ``turso_session_id`` should be the session belonging to the turn being
    cancelled. Callers that already opened a *new* session (e.g.
    ``submit_agent_request``) must pass the previous id explicitly so the
    new turn is not closed as ``cancelled`` mid-flight.
    """
    from syte.agent_artifacts import cancel_pending_questions, mark_session_stopped

    cancel_background_subagents(project_id)
    task = _active_turns.get(project_id)
    if task and not task.done():
        # Drop the busy marker immediately so status APIs stop reporting
        # "processing" / stuck running while cooperative cancel finishes (DAV-180).
        if _active_turns.get(project_id) is task:
            _active_turns.pop(project_id, None)
        task.cancel()
        session_number = await current_session_number(project_id)
        close_id = turso_session_id
        if close_id is None:
            close_id = await current_turso_session_id(project_id)
        await cancel_pending_questions(project_id)
        stop = await mark_session_stopped(
            project_id,
            reason="interrupted",
            source="api",
            session_number=session_number,
            turso_session_id=close_id,
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
                "turso_session_id": close_id,
                "stop_id": stop["id"],
            },
            source=CLOUD_RUNTIME,
            turso_session_id=close_id,
        )
        if close_id:
            await close_turso_session(close_id, status="cancelled")
        return True, "Active cloud-agent turn interrupted."
    # Ensure a stale completed task cannot keep the busy indicator stuck.
    if _active_turns.get(project_id) is task:
        _active_turns.pop(project_id, None)
    # No in-process turn, but still close a known orphaned Turso session so
    # clients never poll an endless status=open / "generating" state.
    if turso_session_id:
        await close_turso_session(turso_session_id, status="cancelled")
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
    turn_task = _active_turns.get(project_id)
    active = bool(turn_task and not turn_task.done())
    # Also treat the durable job runner as busy so cancel/pause cannot leave a
    # stale "processing" indicator when the turn task was already cleared.
    try:
        from syte.agent_jobs import is_agent_job_running

        job_running = is_agent_job_running(project_id)
    except Exception:
        job_running = False
    busy = active or job_running
    turso_sync = await turso_message_sync_status(project_id)
    return {
        "agent_runtime": CLOUD_RUNTIME,
        "agent_runtime_type": "cloud",
        "agent_status": "processing" if busy else agent_status_value,
        "agent_turso_sync": turso_sync,
        # Runtime readiness (started) vs in-flight turn (busy).
        "agent_running": agent_status_value != "stopped",
        "agent_busy": busy,
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
            "screenshot_preview", "inspect_preview", "vision_screenshots", "interactive_questions",
            "env_access", "mcp_addons", "mcp_credentials", "call_external_api",
            "session_stop_markers", "any_code_type",
            "shadcn_websites", "agent_memory", "visual_analyses", "design_profiles",
            "workspace_index", "activity_sse", "model_routing", "web_search",
            "semantic_search", "prompt_caching", "circuit_breaker", "skill_keywords",
            "preview_health", "planner_executor", "syterules", "background_subagents",
            "subagent_timeout", "workspace_shell_boundary", "subagent_await",
            "subagent_model_routing", "auto_model_routing", "plan_assignees",
            "nvidia_subagent_provider",
        ],
    }


async def update_agent_settings(
    project_id: str, *, model_profile: str | None = None, include_status: bool = True
) -> dict[str, Any]:
    if model_profile is not None:
        profile = model_profile.strip() or DEFAULT_PROFILE
        if not await is_available_model_profile(profile):
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
    {"type": "function", "function": {"name": "update_plan", "description": (
        "Publish or revise a concise execution plan BEFORE multi-step work. Each step must be "
        "assigned: main (you, on the user's chosen model) or subagent (delegate_task / NVIDIA GLM 5.2). "
        "Pass assignees aligned with steps, or prefix step text with [main]/[subagent]."),
     "parameters": {"type": "object", "properties": {
         "steps": {"type": "array", "items": {"type": "string"}},
         "assignees": {
             "type": "array",
             "items": {"type": "string", "enum": ["main", "subagent"]},
             "description": "Same length as steps. main=chosen model; subagent=NVIDIA GLM 5.2",
         },
         "note": {"type": "string"},
     }, "required": ["steps"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "screenshot_preview", "description": (
        "Capture desktop (1280x800) and phone (390x844) screenshots of a preview route. Images are "
        "saved to the DB/disk, shown in chat, and returned for vision inspection. Start the preview first."),
     "parameters": {"type": "object", "properties": {
         "route": {"type": "string", "description": "Path on the preview origin, e.g. / or /login"},
         "url": {"type": "string", "description": "Optional full URL override (must be an allowed preview URL)"},
         "viewports": {"type": "array", "items": {"type": "string", "enum": ["desktop", "phone"]}, "description": "Defaults to both desktop and phone"},
     }, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_preview", "description": (
        "Preferred preview health check via real Chromium DevTools (no screenshot needed for "
        "load/console verification). Fetches the route, returns load_ok, readyState, title, "
        "console errors, page exceptions, network failures, and a page_summary (body text length, "
        "h1, visible controls, failed resources). Use this AFTER UI edits to confirm the preview "
        "actually loads — only add include_screenshot=true when you need visual layout review."),
     "parameters": {"type": "object", "properties": {
         "route": {"type": "string", "description": "Path on the preview origin, e.g. / or /login"},
         "url": {"type": "string", "description": "Optional full URL override (must be allowlisted)"},
         "include_screenshot": {"type": "boolean", "description": "Also capture a desktop screenshot (default false)"},
         "include_console": {"type": "boolean", "description": "Capture browser DevTools console + page errors (default true)"},
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
    {"type": "function", "function": {"name": "mcp_credentials", "description": (
        "List stored MCP credentials (API keys for external services like GitHub, Jira, "
        "Slack, etc.) that an external service saved for this project. Returns service names, "
        "display names, descriptions, API base URLs, and masked keys. Use this FIRST when the "
        "user asks you to interact with an external service — never ask the user for API keys "
        "that may already be stored. After confirming a credential exists, call call_external_api "
        "with that service_name to make an authenticated request."),
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "call_external_api", "description": (
        "Make an authenticated HTTP request to an external service using stored MCP credentials. "
        "Call mcp_credentials first to see available services. The service_name must match a "
        "saved credential. The stored api_key is automatically attached as a Bearer token "
        "(Authorization header). Use this for any external API the user has configured — "
        "GitHub, Jira, Slack, Linear, etc. Do NOT hardcode API keys or pass them in prompts."),
     "parameters": {"type": "object", "properties": {
         "service_name": {"type": "string", "description": "The stored service name from mcp_credentials list"},
         "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method, default GET"},
         "path": {"type": "string", "description": "URL path appended to the stored api_url, e.g. /repos/owner/repo/issues"},
         "body": {"type": "object", "description": "JSON request body for POST/PUT/PATCH"},
         "headers": {"type": "object", "description": "Additional headers merged with the auth header"},
     }, "required": ["service_name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "web_search", "description": (
        "Search the web for current information, docs, product/image ideas, or news. "
        "Prefer this over guessing when the user asks for latest/current facts."),
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"},
         "max_results": {"type": "integer"},
     }, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "fetch_url", "description": (
        "Read any public web page or API response as text. Use it after web_search to actually "
        "READ the documentation, design reference, or changelog you found instead of relying on "
        "memory. HTML is reduced to readable prose. Blocked: non-http(s) schemes, cloud metadata "
        "endpoints, and private/loopback addresses (use browse_url for this project's dev server)."),
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string"},
         "extract": {"type": "string", "enum": ["text", "raw", "links"], "description": "text=readable prose (default), raw=body as received, links=text + outbound links"},
         "max_chars": {"type": "integer", "description": "Content cap (default 20000, max 120000)"},
     }, "required": ["url"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "browse_url", "description": (
        "Open a URL in real headless Chromium and return browser-side diagnostics: console logs, "
        "uncaught page exceptions, failed network requests, document title and readyState. This is "
        "how you read the DEV SERVER's browser console (hydration errors, client runtime errors) "
        "which never appear in server logs. Loopback/private hosts are allowed here on purpose. "
        "For the project's own preview route prefer inspect_preview; use browse_url for any other "
        "origin, a specific port, or an external page you need rendered."),
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string", "description": "Full URL, e.g. http://127.0.0.1:3000/dashboard"},
         "include_screenshot": {"type": "boolean", "description": "Also return a screenshot (default false)"},
         "width": {"type": "integer"},
         "height": {"type": "integer"},
     }, "required": ["url"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "shadcn_registry", "description": (
        "Live shadcn/ui registry access — the source of truth for component names, sub-components, "
        "props, dependencies and file paths. Call this BEFORE writing UI code; never recall a "
        "component API from memory. Actions: info (this project's framework/aliases/installed "
        "components), search (find components across registries), view (real source + deps of "
        "specific items), docs (real API reference), add (install actual registry source into "
        "components/ui — use instead of hand-writing a primitive), apply_preset (theme/font preset), "
        "presets (resolve current preset). Typical flow: info → search → view/docs → add → build."),
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["info", "search", "view", "docs", "add", "apply_preset", "presets"]},
         "query": {"type": "string", "description": "For action=search"},
         "items": {"type": "array", "items": {"type": "string"}, "description": "Item names for view/add, e.g. ['button','navigation-menu']"},
         "component": {"type": "string", "description": "For action=docs"},
         "registries": {"type": "array", "items": {"type": "string"}, "description": "Registry names for search (default ['@shadcn'])"},
         "limit": {"type": "integer"},
         "dry_run": {"type": "boolean", "description": "For action=add — preview without writing"},
         "overwrite": {"type": "boolean", "description": "For action=add"},
         "preset": {"type": "string", "description": "For action=apply_preset"},
         "only": {"type": "string", "description": "For action=apply_preset: theme | font"},
     }, "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "semantic_search", "description": (
        "Search the workspace index by meaning/tags (not exact keywords only). "
        "Use before full-workspace crawls when looking for related components or pages."),
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"},
         "limit": {"type": "integer"},
     }, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_code", "description": (
        "Ripgrep-style search across the project workspace for an exact/regex pattern. "
        "Prefer this over `run_command grep/rg` or listing thousands of files. "
        "Returns matching file paths with line snippets (capped)."),
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string", "description": "Regex or literal search pattern"},
         "path": {"type": "string", "description": "Optional subdirectory relative to workspace (default app/)"},
         "glob": {"type": "string", "description": "Optional file glob, e.g. '*.tsx'"},
         "max_matches": {"type": "integer", "description": "Max matches to return (default 40, max 80)"},
         "case_insensitive": {"type": "boolean"},
     }, "required": ["pattern"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delegate_task", "description": (
        "Delegate one plan step assigned to subagent using a public profile "
        "(falls back to Go). Use mode=research (default) for "
        "read-only find/review; mode=implementation when the subagent must edit files. Set "
        "background:true to run asynchronously, then call await_subagent with the returned task_id. "
        "ALWAYS list in `files` every workspace file the subagent may create or edit — that scope "
        "is reserved for it, and both you and other subagents are blocked from writing those "
        "paths until it finishes, so two agents never edit the same file in parallel. For "
        "mode=implementation, `files` is mandatory. Never delegate two tasks that share a file: "
        "split by file, or run them one after another."),
     "parameters": {"type": "object", "properties": {
         "task": {"type": "string"},
         "files": {
             "type": "array",
             "items": {"type": "string"},
             "description": (
                 "Workspace-relative paths the subagent owns, e.g. "
                 "['app/app/pricing/page.tsx', 'app/components/pricing-table.tsx']. "
                 "Required for mode=implementation; recommended for research."
             ),
         },
         "background": {"type": "boolean"},
         "mode": {
             "type": "string",
             "enum": ["research", "implementation"],
             "description": "research=read-only/fast; implementation=can write files",
         },
         "max_steps": {
             "type": "integer",
             "description": "Optional tool-round budget (default 14 research / 28 implementation, max 48)",
         },
     }, "required": ["task", "files"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "await_subagent", "description": (
        "Wait for a background subagent started by delegate_task and return its findings. "
        "Pass the task_id from the delegate_task result."),
     "parameters": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "timeout_s": {"type": "integer", "description": "Seconds to wait (default 120, max 600)"},
     }, "required": ["task_id"], "additionalProperties": False}}},
]

_SUBAGENT_MUTATING_TOOLS = frozenset({
    "write_file", "delete_file", "run_command", "service", "update_plan",
    "env_set", "connect_mcp", "call_mcp", "ask_question", "request_env",
    "delegate_task", "await_subagent", "call_external_api",
})

SUBAGENT_TOOLS = [
    tool for tool in TOOLS
    if tool["function"]["name"] not in {
        "delegate_task", "await_subagent", "ask_question", "request_env",
    }
]

SUBAGENT_RESEARCH_TOOLS = [
    tool for tool in SUBAGENT_TOOLS
    if tool["function"]["name"] not in _SUBAGENT_MUTATING_TOOLS
]


def _reserved_file_error(
    project_id: str, path: str, ctx: dict[str, Any]
) -> dict[str, Any] | None:
    """Block a write when a live subagent owns the path (no parallel edits)."""
    owner = subagent_file_owner(project_id, path)
    if not owner or owner == ctx.get("subagent_task_id"):
        return None
    return {
        "ok": False,
        "error": "file_reserved_by_subagent",
        "retryable": True,
        "message": (
            f"{path} is reserved by subagent {owner} until it finishes — two agents must "
            "never edit one file. Wait for it (await_subagent) and then re-read the file, "
            "or work on a different file now."
        ),
        "task_id": owner,
        "reserved_files": sorted(subagent_file_scope(project_id)),
    }


def _screenshot_guard(
    project_id: str, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any] | None:
    """Suppress redundant ``screenshot_preview`` calls.

    Capturing a preview costs a browser launch plus a vision payload, and models
    tend to re-shoot the same unchanged route repeatedly. A capture is skipped
    when it would repeat a route/viewport combination while nothing has been
    written since, when it lands inside the cooldown window, or when the turn
    already used its screenshot budget. The cached description of the previous
    capture is returned so the model still has the information it asked for.
    """
    route = str(args.get("route") or "/").strip() or "/"
    if not route.startswith("/"):
        route = "/" + route
    viewports = args.get("viewports") or ["desktop", "phone"]
    if not isinstance(viewports, list):
        viewports = ["desktop", "phone"]
    signature = f"{route}|{','.join(sorted(str(v) for v in viewports))}"
    history: dict[str, dict[str, Any]] = ctx.setdefault("_screenshot_history", {})
    count = int(ctx.get("_screenshot_count") or 0)
    dirty = bool(ctx.get("_workspace_dirty_since_screenshot"))
    now = time.monotonic()

    previous = history.get(signature)
    if previous and not dirty:
        return {
            "ok": True,
            "action": "screenshot_preview",
            "route": route,
            "url": previous.get("url"),
            "skipped": True,
            "reason": "unchanged_since_last_capture",
            "screenshots": previous.get("screenshots") or [],
            "message": (
                f"Reusing the screenshot already captured for {route} "
                f"({', '.join(str(v) for v in viewports)}) — no file has been written since, "
                "so the preview cannot have changed. Do not capture the same route again "
                "unless you edit something."
            ),
        }

    if count >= MAX_SCREENSHOTS_PER_TURN:
        return {
            "ok": False,
            "error": "screenshot_budget_exhausted",
            "route": route,
            "message": (
                f"This turn already captured {count} screenshots (limit "
                f"{MAX_SCREENSHOTS_PER_TURN}). Use inspect_preview for load/console checks "
                "and rely on the screenshots you already have."
            ),
            "captured": count,
            "limit": MAX_SCREENSHOTS_PER_TURN,
        }

    # Cooldown applies to re-shooting the *same* route (e.g. only the viewport
    # list changed). A different route can show something new, so it is allowed
    # and bounded by the per-turn budget alone.
    last_ts = ctx.get("_last_screenshot_ts")
    same_route = ctx.get("_last_screenshot_route") == route
    if isinstance(last_ts, (int, float)) and same_route and not dirty:
        elapsed = now - float(last_ts)
        if elapsed < SCREENSHOT_MIN_INTERVAL_S:
            return {
                "ok": False,
                "error": "screenshot_rate_limited",
                "route": route,
                "retry_after_s": round(SCREENSHOT_MIN_INTERVAL_S - elapsed, 1),
                "message": (
                    f"{route} was captured {elapsed:.0f}s ago and nothing changed since. "
                    "Make an edit first, or use inspect_preview — do not poll "
                    "screenshot_preview."
                ),
            }
    return None


def _record_screenshot_capture(
    args: dict[str, Any], ctx: dict[str, Any], result: dict[str, Any]
) -> None:
    """Remember a successful capture for the dedupe/cooldown guard."""
    route = str(args.get("route") or "/").strip() or "/"
    if not route.startswith("/"):
        route = "/" + route
    viewports = args.get("viewports") or ["desktop", "phone"]
    if not isinstance(viewports, list):
        viewports = ["desktop", "phone"]
    signature = f"{route}|{','.join(sorted(str(v) for v in viewports))}"
    history: dict[str, dict[str, Any]] = ctx.setdefault("_screenshot_history", {})
    history[signature] = {
        "url": result.get("url"),
        "screenshots": result.get("screenshots") or [],
    }
    ctx["_screenshot_count"] = int(ctx.get("_screenshot_count") or 0) + 1
    ctx["_last_screenshot_ts"] = time.monotonic()
    ctx["_last_screenshot_route"] = route
    ctx["_workspace_dirty_since_screenshot"] = False


async def _execute_tool(
    project_id: str,
    name: str,
    args: dict[str, Any],
    *,
    model: dict[str, Any] | None = None,
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
    tool_chars = int(ctx.get("max_tool_result_chars") or _model_tool_result_chars(model))
    try:
        if (
            ctx.get("question_required")
            and not ctx.get("question_answered")
            and name != "ask_question"
        ):
            return {
                "ok": False,
                "error": "question_required",
                "retryable": True,
                "message": (
                    "This new website brief is missing a material visual direction. "
                    "Call ask_question once with a concise theme/design choice before planning."
                ),
            }
        # Hard planner gate: Deep/Max and substantive website work must plan
        # before inspection or mutation. A user question is the only allowed
        # pre-plan action so clarification can genuinely happen first.
        if (
            ctx.get("mandatory_plan")
            and not ctx.get("plan_submitted")
            and name not in {"update_plan", "ask_question"}
        ):
            website_gate = ctx.get("plan_gate_reason") == "website"
            return {
                "ok": False,
                "error": "plan_required",
                "retryable": True,
                "message": (
                    "Website workflow requires clarification-or-plan first. Call ask_question "
                    "if a material design decision is missing; otherwise call update_plan with "
                    "an ordered, design-specific implementation plan."
                    if website_gate
                    else
                    "Thinking level requires a plan first. Call update_plan with an "
                    "ordered list of concrete steps, then continue with other tools."
                ),
            }
        if name == "list_files":
            from syte.token_efficiency import load_aiignore_patterns, path_is_ignored

            files = await list_workspace_files(project_id, str(args.get("path") or "app"))
            patterns = load_aiignore_patterns(
                workspace_path(project_id),
                workspace_path(project_id) / "app",
            )
            filtered = [
                f for f in files
                if not path_is_ignored(str(f.get("path") or f.get("name") or ""), patterns)
            ]
            if len(filtered) > 200:
                return {
                    "ok": True,
                    "files": filtered[:200],
                    "truncated": True,
                    "truncated_count": len(filtered),
                    "ignored_filtered": len(files) - len(filtered),
                    "note": "Listing truncated to 200 entries — narrow the path or use search_code.",
                }
            return {
                "ok": True,
                "files": filtered,
                "ignored_filtered": len(files) - len(filtered),
            }
        if name == "read_file":
            ok, content, mime = await read_file(project_id, str(args["path"]))
            await _track_touched_file(project_id, str(args["path"]), ctx, content=content if isinstance(content, str) else None)
            text = content if isinstance(content, str) else "Binary file"
            # Cap tool payload so a single large file cannot blow the LLM context.
            if isinstance(text, str) and len(text) > tool_chars:
                text = _truncate_for_llm(text, tool_chars)
            return {"ok": ok, "content": text, "mime": mime}
        if name == "write_file":
            path = str(args["path"])
            locked = _reserved_file_error(project_id, path, ctx)
            if locked:
                return locked
            ok, message = await write_file(project_id, path, str(args["content"]))
            await _track_touched_file(
                project_id, path, ctx, content=str(args.get("content") or ""),
            )
            path_l = path.lower()
            if any(marker in path_l for marker in _UI_PATH_MARKERS):
                ctx["_ui_edit_detected"] = True
            if ok:
                # A new capture can only show something new after a write.
                ctx["_workspace_dirty_since_screenshot"] = True
            return {"ok": ok, "message": message}
        if name == "delete_file":
            locked = _reserved_file_error(project_id, str(args["path"]), ctx)
            if locked:
                return locked
            ok, message = await delete_file(project_id, str(args["path"]))
            await _track_touched_file(project_id, str(args["path"]), ctx)
            if ok:
                ctx["_workspace_dirty_since_screenshot"] = True
            return {"ok": ok, "message": message}
        if name == "run_command":
            from syte.token_efficiency import filter_cli_output

            timeout_s = max(1, min(int(args.get("timeout") or 300), 900))
            command = str(args["command"])
            code, output = await execute_command(
                project_id, command, cwd=str(args.get("cwd") or "app"),
                timeout=timeout_s, source="agent",
            )
            filtered = filter_cli_output(command, output, max_chars=tool_chars)
            truncated = _truncate_for_llm(filtered, tool_chars)
            if code == 124:
                return {
                    "ok": False,
                    "exit_code": 124,
                    "error": "timeout",
                    "retryable": True,
                    "message": f"Command timed out after {timeout_s}s",
                    "output": truncated,
                }
            return {"ok": code == 0, "exit_code": code, "output": truncated}
        if name == "service":
            return await run_service_action(
                project_id, str(args["action"]), command=args.get("command"),
                cwd=str(args.get("cwd") or "app"), lines=int(args.get("lines") or 200),
                timeout=int(args.get("timeout") or 300), source="agent",
            )
        if name == "update_plan":
            result = await _tool_update_plan(project_id, args, ctx)
            if result.get("ok"):
                ctx["plan_submitted"] = True
            return result
        if name == "screenshot_preview":
            blocked = _screenshot_guard(project_id, args, ctx)
            if blocked is not None:
                # Mark as "captured" for a dedupe hit so post-turn automation does
                # not immediately shoot the same route again.
                if blocked.get("skipped"):
                    ctx["_screenshot_captured"] = True
                return blocked
            result = await _tool_screenshot_preview(project_id, args, ctx)
            ctx["_screenshot_captured"] = True
            if result.get("ok"):
                _record_screenshot_capture(args, ctx, result)
            return result
        if name == "inspect_preview":
            return await _tool_inspect_preview(project_id, args, ctx)
        if name == "ask_question":
            result = await _tool_ask_question(project_id, args, ctx)
            if result.get("ok"):
                ctx["question_answered"] = True
            return result
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
        if name == "mcp_credentials":
            from syte.turso_store import list_mcp_credentials

            creds = await list_mcp_credentials(project_id)
            if not creds:
                return {
                    "ok": True,
                    "credentials": [],
                    "note": "No stored MCP credentials for this project. An external service can save credentials via the API.",
                }
            return {"ok": True, "credentials": creds}
        if name == "call_external_api":
            return await _tool_call_external_api(project_id, args)
        if name == "web_search":
            from syte.web_search import web_search as do_web_search

            return await do_web_search(
                str(args.get("query") or ""),
                max_results=int(args.get("max_results") or 5),
            )
        if name == "fetch_url":
            from syte.web_access import fetch_url as do_fetch_url

            return await do_fetch_url(
                str(args.get("url") or ""),
                extract=str(args.get("extract") or "text"),
                max_chars=int(args.get("max_chars") or 20_000),
            )
        if name == "browse_url":
            from syte.web_access import browse_url as do_browse_url

            return await do_browse_url(
                str(args.get("url") or ""),
                include_screenshot=bool(args.get("include_screenshot")),
                width=int(args.get("width") or 1280),
                height=int(args.get("height") or 800),
            )
        if name == "shadcn_registry":
            from syte.shadcn_registry import run_action as shadcn_action

            action = str(args.get("action") or "").strip().lower()
            # Read-only subagents may inspect the registry but never install into
            # the shared workspace (that is a write the parent must own).
            if (
                ctx.get("subagent_mode") == "research"
                and action in {"add", "apply_preset"}
            ):
                return {
                    "ok": False,
                    "error": "research_readonly",
                    "message": (
                        f"shadcn_registry action={action} writes to the workspace and is "
                        "blocked in research mode. Report the components to install back "
                        "to the parent agent."
                    ),
                }
            return await shadcn_action(project_id, args)
        if name == "semantic_search":
            from syte.agent_memory import lookup_workspace_paths, prompt_tags_from_message

            query = str(args.get("query") or "").strip()
            tags = prompt_tags_from_message(query)
            hits = await lookup_workspace_paths(
                project_id,
                tags=tags or None,
                query=query,
                limit=max(1, min(int(args.get("limit") or 20), 50)),
            )
            return {"ok": True, "query": query, "tags": tags, "results": hits}
        if name == "search_code":
            return await _tool_search_code(project_id, args)
        if name == "delegate_task":
            return await _tool_delegate_task(project_id, args, model=model, context=ctx)
        if name == "await_subagent":
            return await _tool_await_subagent(project_id, args)

        # Auto-connect MCP addons that expose this unknown tool name.
        from syte.agent_artifacts import call_mcp_addon, connect_mcp_addon, list_mcp_addons

        addons = await list_mcp_addons(project_id)
        for addon in addons:
            tool_names = [
                str(t.get("name") or "")
                for t in (addon.get("tools") or [])
                if isinstance(t, dict)
            ]
            if name not in tool_names and name not in {
                "syte_service", "syte_access", "web_search",
            }:
                continue
            if addon.get("status") != "connected":
                connected = await connect_mcp_addon(project_id, addon["id"])
                if not connected.get("ok"):
                    continue
            return await call_mcp_addon(project_id, addon["id"], name, args if isinstance(args, dict) else {})

        return {"ok": False, "error": "unknown_tool", "message": name}
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": "file_not_found",
            "message": str(exc) or "File not found",
            "hint": "List files first or check the path",
        }
    except (TimeoutError, asyncio.TimeoutError) as exc:
        return {
            "ok": False,
            "error": "timeout",
            "message": str(exc) or "Timed out",
            "hint": "Try with a shorter timeout or simpler command",
            "retryable": True,
        }
    except asyncio.CancelledError:
        # Propagate cancel so interrupt/stop can unwind the turn; the tool loop
        # records a cancelled tool result when appropriate.
        raise
    except Exception as exc:
        return {
            "ok": False,
            "error": "tool_failed",
            "message": str(exc) or type(exc).__name__,
            "retryable": False,
        }


async def _tool_search_code(project_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Ripgrep (or Python fallback) search capped for LLM context (DAV-150)."""
    import shutil

    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return {"ok": False, "error": "invalid_pattern", "message": "pattern is required"}
    if len(pattern) > 400:
        return {"ok": False, "error": "invalid_pattern", "message": "pattern too long (max 400)"}

    rel = str(args.get("path") or "app").strip().lstrip("/") or "app"
    try:
        root = _resolve_search_root(project_id, rel)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_path", "message": str(exc)}

    max_matches = max(1, min(int(args.get("max_matches") or 40), 80))
    glob_pat = str(args.get("glob") or "").strip() or None
    case_insensitive = bool(args.get("case_insensitive"))
    skip_dirs = {".git", "node_modules", ".next", "dist", "build", "__pycache__", ".turbo", "coverage"}
    from syte.token_efficiency import load_aiignore_patterns, path_is_ignored

    aiignore = load_aiignore_patterns(
        workspace_path(project_id),
        workspace_path(project_id) / "app",
    )

    matches: list[dict[str, Any]] = []
    rg = shutil.which("rg")
    if rg:
        cmd = [
            rg, "--line-number", "--no-heading", "--color", "never",
            "--max-count", str(max_matches),
            "--glob", "!node_modules/**", "--glob", "!.git/**", "--glob", "!.next/**",
        ]
        if case_insensitive:
            cmd.append("-i")
        if glob_pat:
            cmd.extend(["--glob", glob_pat])
        cmd.extend(["--", pattern, str(root)])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (OSError, asyncio.TimeoutError) as exc:
            return {"ok": False, "error": "search_failed", "message": str(exc), "retryable": True}
        text = (stdout_b or b"").decode("utf-8", errors="replace")
        for line in text.splitlines():
            if len(matches) >= max_matches:
                break
            # path:line:content
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            path_s, line_s, snippet = parts[0], parts[1], parts[2]
            try:
                rel_path = str(Path(path_s).resolve().relative_to(workspace_path(project_id)))
            except ValueError:
                rel_path = path_s
            rel_norm = rel_path.replace("\\", "/")
            if path_is_ignored(rel_norm, aiignore):
                continue
            matches.append({
                "path": rel_norm,
                "line": int(line_s) if line_s.isdigit() else line_s,
                "snippet": snippet[:240],
            })
        return {
            "ok": True,
            "pattern": pattern,
            "path": rel,
            "match_count": len(matches),
            "matches": matches,
            "truncated": len(matches) >= max_matches,
            "engine": "rg",
            "stderr": (stderr_b or b"").decode("utf-8", errors="replace")[:500] or None,
        }

    # Fallback: bounded Python walk when rg is unavailable.
    import re as _re

    try:
        regex = _re.compile(pattern, _re.I if case_insensitive else 0)
    except _re.error as exc:
        return {"ok": False, "error": "invalid_pattern", "message": f"Bad regex: {exc}"}

    for path in root.rglob("*"):
        if len(matches) >= max_matches:
            break
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        try:
            rel_check = str(path.resolve().relative_to(workspace_path(project_id))).replace("\\", "/")
        except ValueError:
            rel_check = str(path)
        if path_is_ignored(rel_check, aiignore):
            continue
        if glob_pat and not path.match(glob_pat):
            continue
        if path.suffix.lower() not in {
            ".ts", ".tsx", ".js", ".jsx", ".py", ".css", ".scss", ".md", ".json",
            ".html", ".yml", ".yaml", ".toml", ".go", ".rs", ".java",
        }:
            continue
        try:
            if path.stat().st_size > 400_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                try:
                    rel_path = str(path.resolve().relative_to(workspace_path(project_id)))
                except ValueError:
                    rel_path = str(path)
                matches.append({
                    "path": rel_path.replace("\\", "/"),
                    "line": idx,
                    "snippet": line[:240],
                })
                if len(matches) >= max_matches:
                    break
    return {
        "ok": True,
        "pattern": pattern,
        "path": rel,
        "match_count": len(matches),
        "matches": matches,
        "truncated": len(matches) >= max_matches,
        "engine": "python",
    }


def _resolve_search_root(project_id: str, rel: str) -> Path:
    from syte.workspace_api import _resolve_workspace_path

    return _resolve_workspace_path(project_id, rel)


async def _track_touched_file(
    project_id: str,
    path: str,
    ctx: dict[str, Any],
    *,
    content: str | None = None,
) -> None:
    """Record active files + workspace index entry for context packing."""
    from syte.agent_memory import touch_active_file, upsert_workspace_file

    session_number = int(ctx.get("session_number") or 0)
    if session_number > 0 and path:
        try:
            await touch_active_file(project_id, session_number, path)
        except Exception:
            logging.getLogger(__name__).debug("active file track failed", exc_info=True)
    if path:
        try:
            await upsert_workspace_file(project_id, path, content=content)
        except Exception:
            logging.getLogger(__name__).debug("workspace index update failed", exc_info=True)


async def _tool_update_plan(
    project_id: str, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from syte.agent_artifacts import save_plan
    from syte.model_routing import normalize_plan_steps

    normalized = normalize_plan_steps(args.get("steps"), args.get("assignees"))
    if not normalized:
        return {"ok": False, "error": "empty_plan", "message": "Provide at least one plan step."}
    # Persist display-friendly strings while keeping structured assignee metadata.
    steps = [f"[{row['assignee']}] {row['text']}" for row in normalized]
    note = str(args.get("note") or "")
    plan = await save_plan(
        project_id,
        steps,
        note=note,
        request_id=str(ctx.get("request_id") or ""),
        session_number=int(ctx.get("session_number") or 0),
        turso_session_id=ctx.get("turso_session_id"),
    )
    return {
        "ok": True,
        "plan_id": plan["id"],
        "steps": steps,
        "assignments": normalized,
        "note": note,
        "guidance": (
            "Execute [main] steps yourself on the chosen model. "
            "For each [subagent] step call delegate_task (then await_subagent if background) and "
            "pass `files` — the exact paths that subagent owns. Two steps that touch the same file "
            "must not run in parallel: give them disjoint file lists or run them sequentially."
        ),
    }


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

    raw = await capture_preview_screenshots(
        target, viewports=tuple([*viewport_names, "thumb"])
    )
    # Prefer the parallel thumb capture; fall back to first ok viewport bytes.
    thumb_shot = raw.get("thumb") if (raw.get("thumb") or {}).get("ok") else None
    if thumb_shot is None:
        for name in ("desktop", "phone"):
            if (raw.get(name) or {}).get("ok") and (raw.get(name) or {}).get("png_bytes"):
                thumb_shot = {
                    "ok": True,
                    "png_bytes": raw[name]["png_bytes"],
                    "width": raw[name].get("width"),
                    "height": raw[name].get("height"),
                }
                break

    shots_out: list[dict[str, Any]] = []
    vision_parts: list[dict[str, Any]] = []

    async def _persist_one(name: str) -> dict[str, Any]:
        shot = raw.get(name) or {}
        if not shot.get("ok") or not shot.get("png_bytes"):
            return {
                "viewport": name,
                "ok": False,
                "error": shot.get("error"),
                "message": shot.get("message"),
            }
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
        return {
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
            "_png": png,
            "_record": record,
        }

    persisted = await asyncio.gather(*(_persist_one(name) for name in viewport_names))
    for name, entry in zip(viewport_names, persisted):
        png = entry.pop("_png", None)
        record = entry.pop("_record", None)
        shots_out.append(entry)
        if not entry.get("ok") or not isinstance(png, (bytes, bytearray)) or not isinstance(record, dict):
            continue
        if len(png) <= MAX_VISION_IMAGE_BYTES:
            vision_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{record['image_base64']}"},
            })
            vision_parts.append({
                "type": "text",
                "text": f"[{name} {record['width']}x{record['height']}] {route}",
            })
        # Structured visual analysis (best-effort) for the visual feedback loop.
        try:
            from syte.visual_analysis import analyze_and_store

            analysis = await analyze_and_store(
                project_id,
                screenshot_id=str(record["id"]),
                image_base64=str(record.get("image_base64") or ""),
                viewport=name,
                width=int(record.get("width") or 0),
                height=int(record.get("height") or 0),
                route=route,
                screenshot_url=entry["image_url"],
                session_id=ctx.get("turso_session_id"),
                session_number=int(ctx.get("session_number") or 0),
                model=ctx.get("model") if isinstance(ctx.get("model"), dict) else None,
            )
            entry["visual_analysis_id"] = analysis.get("id")
            entry["visual_analysis"] = {
                "id": analysis.get("id"),
                "issues": (analysis.get("issues") or [])[:8],
                "suggestions": (analysis.get("suggestions") or [])[:8],
            }
        except Exception:
            logging.getLogger(__name__).debug("visual analysis failed", exc_info=True)

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


async def _tool_inspect_preview(
    project_id: str, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Limited browser: fetch preview HTML/text + DevTools console (+ optional screenshot)."""
    from syte.preview_access import run_access_action
    from urllib.parse import urljoin

    route = str(args.get("route") or "/").strip() or "/"
    if not route.startswith("/"):
        route = "/" + route
    explicit_url = str(args.get("url") or "").strip()
    include_shot = bool(args.get("include_screenshot"))
    # Default ON — agents must see browser console / load failures.
    include_console = True if "include_console" not in args else bool(args.get("include_console"))

    status = await run_access_action(project_id, "status")
    # Prefer the fetchable origin (direct when TLS is down) so allowlist checks pass.
    base = str(
        status.get("preview_fetch_url")
        or status.get("preview_url")
        or status.get("preview_direct_url")
        or status.get("preview_domain_url")
        or ""
    )
    target = explicit_url or (urljoin(base.rstrip("/") + "/", route.lstrip("/")) if base else "")
    if not target and base:
        target = base.rstrip("/") + route
    if not target:
        logs = await run_access_action(project_id, "logs", lines=120)
        return {
            "ok": False,
            "error": "no_preview",
            "message": "Preview URL unavailable — call service preview_start first.",
            "preview_logs": (logs.get("logs") or "")[-8000:],
        }

    fetched = await run_access_action(project_id, "fetch", url=target)
    # If the agent passed a domain URL while only direct is reachable, retry fetch URL.
    if (
        not fetched.get("ok")
        and fetched.get("error") == "url_not_allowed"
        and not explicit_url
        and status.get("preview_fetch_url")
        and str(status.get("preview_fetch_url")) != base
    ):
        alt_base = str(status.get("preview_fetch_url") or "")
        alt_target = urljoin(alt_base.rstrip("/") + "/", route.lstrip("/"))
        fetched = await run_access_action(project_id, "fetch", url=alt_target)
        if fetched.get("ok") or fetched.get("error") != "url_not_allowed":
            target = alt_target
            base = alt_base

    result: dict[str, Any] = {
        "ok": bool(fetched.get("ok")),
        "action": "inspect_preview",
        "route": route,
        "url": fetched.get("url") or target,
        "status_code": fetched.get("status_code"),
        "content_type": fetched.get("content_type"),
        "content": fetched.get("content") or fetched.get("message"),
        "message": (
            f"Fetched preview {route} (HTTP {fetched.get('status_code')})"
            if fetched.get("ok")
            else str(fetched.get("message") or fetched.get("error") or "Fetch failed")
        ),
    }
    if fetched.get("error"):
        result["error"] = fetched.get("error")
    if fetched.get("allowed_urls"):
        result["allowed_urls"] = fetched.get("allowed_urls")

    if include_console and result.get("ok") is not False:
        console = await run_access_action(
            project_id,
            "console",
            url=target,
            include_screenshot=False,
        )
        result["devtools"] = {
            k: v
            for k, v in console.items()
            if k not in {"png_bytes", "image_base64"}
        }
        result["load_ok"] = bool(console.get("load_ok"))
        result["console_logs"] = console.get("console_logs") or []
        result["page_errors"] = console.get("page_errors") or []
        result["network_failures"] = console.get("network_failures") or []
        result["page_summary"] = console.get("page_summary") or {}
        result["console_error_count"] = int(console.get("console_error_count") or 0)
        result["page_error_count"] = int(console.get("page_error_count") or 0)
        result["title"] = console.get("title") or ""
        result["ready_state"] = console.get("ready_state") or ""
        summary = result["page_summary"] if isinstance(result["page_summary"], dict) else {}
        body_len = int(summary.get("body_text_length") or 0)
        if not console.get("ok"):
            result["ok"] = False
            result["message"] = (
                f"{result['message']}; DevTools: {console.get('message') or 'console issues'}"
            )
            if console.get("error"):
                result["error"] = console.get("error")
        else:
            result["message"] = (
                f"{result['message']}; browser load_ok={bool(console.get('load_ok'))}, "
                f"console_errors={result['console_error_count']}, "
                f"page_errors={result['page_error_count']}, "
                f"body_chars={body_len}"
            )
    elif include_console and result.get("ok") is False:
        # Still try console on the preferred fetch URL when fetch failed for other reasons.
        console = await run_access_action(
            project_id,
            "console",
            url=target,
            include_screenshot=False,
        )
        if console.get("ok") or console.get("console_logs") or console.get("page_errors"):
            result["devtools"] = {
                k: v
                for k, v in console.items()
                if k not in {"png_bytes", "image_base64"}
            }
            result["console_logs"] = console.get("console_logs") or []
            result["page_errors"] = console.get("page_errors") or []

    # Always attach recent preview server logs when inspect fails so the agent
    # can diagnose without a second tool round-trip (and when URL allow fails).
    if not result.get("ok"):
        try:
            logs = await run_access_action(project_id, "logs", lines=160)
            log_text = str(logs.get("logs") or "")
            if log_text.strip():
                result["preview_logs"] = log_text[-8000:]
                result["message"] = (
                    f"{result['message']}; preview_logs attached "
                    f"({min(160, log_text.count(chr(10)) + 1)} lines)"
                )
        except Exception:
            logger.debug("preview log attach failed", exc_info=True)

    if include_shot:
        shot_args = {"route": route, "url": target, "viewports": ["desktop"]}
        # Piggybacked captures obey the same per-turn budget / repeat guard.
        blocked = _screenshot_guard(project_id, shot_args, ctx)
        if blocked is not None:
            result["screenshot"] = blocked
            result["message"] = (
                f"{result['message']}; screenshot skipped ({blocked.get('reason') or blocked.get('error')})"
            )
            return result
        shot = await _tool_screenshot_preview(project_id, shot_args, ctx)
        result["screenshot"] = {
            k: v for k, v in shot.items() if not str(k).startswith("_")
        }
        if shot.get("_chat_screenshots"):
            result["_chat_screenshots"] = shot["_chat_screenshots"]
        if shot.get("_vision_parts"):
            result["_vision_parts"] = shot["_vision_parts"]
        if shot.get("ok"):
            ctx["_screenshot_captured"] = True
            _record_screenshot_capture(shot_args, ctx, shot)
            if result.get("ok") is not False:
                result["ok"] = True
            result["message"] = f"{result['message']}; desktop screenshot attached"
    return result


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


async def _tool_call_external_api(
    project_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Make an authenticated HTTP request to an external service using stored credentials."""
    from syte.turso_store import get_mcp_credential, list_mcp_credentials
    import httpx

    service_name = str(args.get("service_name") or "").strip().lower()
    if not service_name:
        return {"ok": False, "error": "invalid_service", "message": "service_name is required"}

    cred = await get_mcp_credential(project_id, service_name, include_key=True)
    if not cred:
        available = await list_mcp_credentials(project_id)
        names = [c["service_name"] for c in available]
        return {
            "ok": False,
            "error": "credential_not_found",
            "message": f"No stored credential for '{service_name}'. Available: {names or 'none'}",
        }

    method = str(args.get("method") or "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        method = "GET"

    path = str(args.get("path") or "").strip()
    base_url = str(cred.get("api_url") or "").rstrip("/")
    url = f"{base_url}{'/' + path.lstrip('/') if path else ''}"

    body = args.get("body") if isinstance(args.get("body"), dict) else None
    extra_headers = args.get("headers") if isinstance(args.get("headers"), dict) else {}

    headers: dict[str, str] = {
        "Authorization": f"Bearer {cred['api_key']}",
        "Accept": "application/json",
        **{str(k): str(v) for k, v in extra_headers.items()},
    }
    if body is not None:
        headers.setdefault("Content-Type", "application/json")

    try:
        timeout = httpx.Timeout(45.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            kwargs: dict[str, Any] = {"headers": headers}
            if body is not None:
                kwargs["json"] = body
            if method == "GET":
                resp = await client.get(url, **kwargs)
            elif method == "POST":
                resp = await client.post(url, **kwargs)
            elif method == "PUT":
                resp = await client.put(url, **kwargs)
            elif method == "PATCH":
                resp = await client.patch(url, **kwargs)
            elif method == "DELETE":
                resp = await client.delete(url, **kwargs)
            else:
                return {"ok": False, "error": "bad_method", "message": f"Unknown HTTP method: {method}"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "timeout", "message": f"Request to {url} timed out"}
    except Exception as exc:
        return {"ok": False, "error": "request_failed", "message": str(exc)[:500]}

    is_json = "application/json" in (resp.headers.get("content-type") or "")
    try:
        data = resp.json() if is_json else resp.text[:50_000]
    except Exception:
        data = resp.text[:50_000]

    return {
        "ok": resp.is_success,
        "status": resp.status_code,
        "service": service_name,
        "method": method,
        "url": url,
        "data": data,
    }


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
    on_reasoning: TokenEmitter | None = None,
) -> dict[str, Any]:
    """Parse an OpenAI-compatible SSE chat.completion.chunk stream into one message."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_acc: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage_acc: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "thinking_tokens": 0,
    }

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
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            usage_acc["input_tokens"] = int(
                usage.get("prompt_tokens") or usage.get("input_tokens") or usage_acc["input_tokens"]
            )
            usage_acc["output_tokens"] = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or usage_acc["output_tokens"]
            )
            usage_acc["total_tokens"] = int(
                usage.get("total_tokens") or usage_acc["total_tokens"]
            )
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
            text = str(reason_piece)
            reasoning_parts.append(text)
            if on_reasoning:
                await on_reasoning(text)
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
    if any(usage_acc.values()):
        message["_usage"] = usage_acc
    return message


_THINKING_TARGET_KEYS = (
    "path", "route", "command", "query", "pattern", "task", "addon", "action", "url",
)

_THINKING_PHASE_LABELS = {
    "before_tools": "before",
    "final_answer": "final answer",
    "planning": "planning",
    "site_plan": "site plan",
}


def _thinking_context(
    *,
    step: int,
    phase: str,
    calls: list[dict[str, Any]] | None = None,
    scope_files: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Describe *where* a thinking block happened so the UI can label it.

    Returns payload fields (``step``, ``phase``, ``thinking_tools``,
    ``thinking_targets``, ``thinking_where``) that the chat renders as e.g.
    "Thinking · step 3 · before write_file app/app/page.tsx".
    """
    tools: list[str] = []
    targets: list[str] = []
    for call in calls or []:
        function = call.get("function") if isinstance(call, dict) else None
        function = function or {}
        name = str(function.get("name") or "").strip()
        if name and name not in tools:
            tools.append(name)
        try:
            args = json.loads(function.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        if not isinstance(args, dict):
            continue
        for key in _THINKING_TARGET_KEYS:
            value = args.get(key)
            if value:
                label = str(value).replace("\n", " ")[:80]
                target = f"{name} {label}".strip()
                if target not in targets:
                    targets.append(target)
                break
    phase_label = _THINKING_PHASE_LABELS.get(phase, phase.replace("_", " "))
    where_bits = [f"step {step}"] if step else []
    if targets:
        where_bits.append(f"{phase_label} {targets[0]}")
    elif tools:
        where_bits.append(f"{phase_label} {', '.join(tools[:3])}")
    elif phase_label:
        where_bits.append(phase_label)
    if note:
        where_bits.append(note)
    return {
        "step": int(step or 0),
        "phase": phase,
        "thinking_tools": tools[:6],
        "thinking_targets": targets[:6],
        "thinking_where": " · ".join(where_bits)[:200],
        "scope_files": list(scope_files or [])[:12],
    }


async def _finalize_request_row(
    project_id: str,
    request_id: str,
    *,
    status: str,
    reply: str = "",
    error: str = "",
    usage: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    subagent_count: int = 0,
) -> None:
    """Write the end-of-generation rollup (activity volume + USD cost) for a turn."""
    from syte.agent_activity import (
        activity_count_for_request,
        clear_activity_count_for_request,
    )

    activity_count = activity_count_for_request(request_id)
    try:
        from syte.turso_store import finalize_request

        await finalize_request(
            request_id,
            project_id,
            status=status,
            reply=reply,
            error=error,
            usage=usage,
            cost=cost,
            activity_count=activity_count,
            subagent_count=int(subagent_count or 0),
        )
    except Exception:
        logger.debug("failed to finalize request row %s", request_id, exc_info=True)
    finally:
        clear_activity_count_for_request(request_id)


def _estimate_turn_cost(model: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    """Estimate USD cost for a turn from catalog $/1M prices and token usage."""
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    thinking_tokens = int(usage.get("thinking_tokens") or 0)
    # Treat thinking tokens as billed output when providers report them separately.
    billed_output = output_tokens + thinking_tokens
    total = int(usage.get("total_tokens") or (input_tokens + billed_output))
    in_price = model.get("input_price_per_mtok")
    out_price = model.get("output_price_per_mtok")
    # Bridge metadata may omit prices — fall back to profile catalog.
    if in_price is None or out_price is None:
        try:
            from syte.ai_providers import PROFILE_PROVIDERS

            spec = PROFILE_PROVIDERS.get(str(model.get("profile") or "")) or {}
            if in_price is None:
                in_price = spec.get("input_price_per_mtok")
            if out_price is None:
                out_price = spec.get("output_price_per_mtok")
        except Exception:
            pass
    cost_usd = None
    if in_price is not None and out_price is not None and (input_tokens or billed_output):
        cost_usd = (input_tokens / 1_000_000.0) * float(in_price) + (
            billed_output / 1_000_000.0
        ) * float(out_price)
    if cost_usd is None:
        label = f"{total} tokens" if total else "usage unavailable"
    elif cost_usd < 0.01:
        label = f"${cost_usd:.4f} · {total} tokens"
    else:
        label = f"${cost_usd:.3f} · {total} tokens"
    return {
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "total_tokens": total,
        "label": label,
        "model": model.get("model"),
        "profile": model.get("profile"),
    }


async def _provider_completion(
    model: dict[str, str],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    thinking_config: dict[str, Any] | None = None,
    stream: bool = False,
    on_token: TokenEmitter | None = None,
    on_reasoning: TokenEmitter | None = None,
) -> dict[str, Any]:
    from syte.agent_errors import (
        ProviderError,
        check_circuit_breaker,
        record_circuit_failure,
        record_circuit_success,
    )
    from syte.cloud_agent_store import sanitize_provider_messages

    check_circuit_breaker(model.get("provider") or "", model.get("model") or "")

    cfg = dict(thinking_config or {})
    if "temperature" not in cfg or cfg.get("temperature") is None:
        cfg["temperature"] = temperature
    thinking_params = build_model_thinking_params(
        cfg,
        provider=str(model.get("provider") or ""),
        model=str(model.get("model") or ""),
        api_base=str(model.get("api_base") or ""),
    )
    use_tools = TOOLS if tools is None else tools
    cached_messages = apply_prompt_cache_markers(
        sanitize_provider_messages(list(messages)),
        provider=str(model.get("provider") or ""),
        model=str(model.get("model") or ""),
        api_base=str(model.get("api_base") or ""),
    )
    payload: dict[str, Any] = {
        "model": model["model"],
        "messages": cached_messages,
        "temperature": float(thinking_params.get("temperature", temperature)),
        "top_p": float(thinking_params.get("top_p", 0.95)),
        "stream": bool(stream and (on_token is not None or on_reasoning is not None)),
    }
    max_tokens = _model_max_tokens(model)
    if max_tokens is not None:
        if str(model.get("completion_token_param") or "") == "max_completion_tokens":
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
    if use_tools:
        payload["tools"] = use_tools
        payload["tool_choice"] = "auto"
    if "thinking" in thinking_params:
        payload["thinking"] = thinking_params["thinking"]
    if thinking_params.get("cache_prompt"):
        payload["cache_prompt"] = True
    if thinking_params.get("reasoning_effort"):
        payload["reasoning_effort"] = thinking_params["reasoning_effort"]

    headers = {"Authorization": f"Bearer {model['api_key']}", "Content-Type": "application/json"}
    url = provider_chat_completion_url(model["api_base"])
    error = "Provider request failed"
    client = _get_provider_client()

    from syte import provider_quota
    from syte.gemini_native import (
        GoogleApiError,
        native_generate_content,
        should_use_native_gemini,
    )

    use_native_gemini = should_use_native_gemini(
        str(model.get("api_key") or ""),
        str(model.get("api_base") or ""),
    )

    def _provider_error_message(status_code: int, reason: str, request_url: str, detail: str) -> str:
        detail_l = (detail or "").lower()
        if "no active upstream keys" in detail_l:
            return (
                f"Provider has no active upstream keys for {model.get('model') or 'this model'}. "
                "Check the provider admin console, then retry."
            )
        from syte.gemini_native import explain_google_api_error

        google_hint = explain_google_api_error(detail, status_code=status_code)
        if google_hint:
            return google_hint
        if (
            status_code in {401, 403}
            or "invalid or deactivated api key" in detail_l
            or "api key you provided is invalid" in detail_l
            or "unauthorized" in detail_l
            or "authentication" in detail_l
        ):
            label = (
                model.get("label")
                or model.get("provider_label")
                or model.get("provider")
                or "provider"
            )
            profile = model.get("profile") or "selected"
            model_name = model.get("model") or "unknown-model"
            secret_env = model.get("secret_env") or ""
            key_source = model.get("key_source") or "unknown"
            key_hint = model.get("api_key_hint") or ""
            hint = ""
            api_base = str(model.get("api_base") or "").lower()
            if "deepseek.com" in api_base or str(label).lower() == "deepseek":
                hint = (
                    " Configure the selected provider in AI settings."
                )
            elif "maas.aliyuncs.com" in api_base or "dashscope.aliyuncs.com" in api_base or str(label).lower() == "aliyun":
                hint = (
                    " Use an Aliyun Token Plan key starting with sk-sp- "
                    "(Token Plan console), or a standard Model Studio sk- key "
                    "for DashScope pay-as-you-go. OpenRouter keys (sk-or-…) no longer work for syra-ultra."
                )
            elif "aiplatform.googleapis.com" in api_base or "generativelanguage.googleapis.com" in api_base or "vertex" in str(label).lower():
                hint = (
                    " Use a Google Cloud Vertex AI Express Mode API key "
                    "(Cloud Console → Credentials). Syte calls "
                    "aiplatform.googleapis.com/v1/publishers/google/models/…:generateContent."
                )
            elif "chinaapi.ai" in api_base or "chinaapi" in str(label).lower():
                hint = (
                    " Use the ChinaAPI key from https://dash.chinaapi.ai/. "
                    "The API endpoint is https://api.chinaapi.ai/v1."
                )
            source_bits = [f"source={key_source}"]
            if secret_env:
                source_bits.append(f"env={secret_env}")
            if key_hint:
                source_bits.append(f"key={key_hint}")
            return (
                f"Invalid API key for {profile} ({label} · {model_name}). "
                f"Update the key in AI provider settings "
                f"[{'; '.join(source_bits)}].{hint}"
            )
        return (
            f"Client error '{status_code} {reason}' for url '{request_url}'"
            + (f": {detail}" if detail else "")
        )

    def _is_retryable_provider_status(status_code: int, detail: str) -> bool:
        if status_code not in {408, 429, 500, 502, 503, 504}:
            return False
        detail_l = (detail or "").lower()
        # Permanent gateway/config failures — retrying only burns latency.
        if any(
            marker in detail_l
            for marker in (
                "no active upstream keys",
                "invalid or deactivated api key",
                "invalid api key",
                "incorrect api key",
                "api key you provided is invalid",
                "api_key_service_blocked",
            )
        ):
            return False
        return True

    # Quota-aware retry state. ``active_model`` may rotate to a same-provider
    # fallback while the primary model is parked after HTTP 429.
    base_model_id = str(model.get("model") or "")
    active_model = base_model_id
    models_tried: list[str] = []
    pending_delay = 0.0
    quota_exhausted = False
    quota_cooldown_s = 0.0
    last_attempt = PROVIDER_MAX_ATTEMPTS - 1

    def _quota_message(model_id: str, cooldown: float) -> str:
        profile = model.get("profile") or "The selected model"
        bits = [
            f"{profile} hit the provider quota (HTTP 429 RESOURCE_EXHAUSTED) on {model_id}.",
            f"Syte paced and retried the request, then parked this model for "
            f"~{int(max(1, round(cooldown)))}s.",
        ]
        if models_tried:
            bits.append("Fallback models tried: " + ", ".join(models_tried) + ".")
        bits.append(
            "Retry in a moment, switch model profile, or raise the provider quota "
            "(Vertex AI: Google Cloud Console → IAM & Admin → Quotas → aiplatform.googleapis.com; "
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deploy/error-code-429)."
        )
        return " ".join(bits)

    def _plan_quota_retry(
        model_id: str, retry_after: float | None, detail: str, attempt: int
    ) -> tuple[str, str, float]:
        """Record a 429 and decide the next step.

        Returns ``(action, next_model, delay)`` where action is ``rotate``,
        ``retry``, or ``stop``. Rotation keeps the turn alive on a sibling model
        instead of failing the whole request while quota recovers.
        """
        nonlocal error, quota_exhausted, quota_cooldown_s
        cooldown = provider_quota.record_quota_exhausted(model_id, retry_after=retry_after)
        quota_exhausted = True
        quota_cooldown_s = cooldown
        error = _quota_message(model_id, cooldown)
        logger.warning(
            "provider quota 429 on %s (cooldown %.1fs, attempt %d/%d)",
            model_id,
            cooldown,
            attempt + 1,
            PROVIDER_MAX_ATTEMPTS,
        )
        if attempt >= last_attempt:
            return "stop", model_id, 0.0
        rotated = provider_quota.next_available_model(model_id)
        if rotated and rotated not in models_tried:
            models_tried.append(rotated)
            # Brief pause before the sibling model — full backoff is reserved
            # for same-model retries (truncated exponential + jitter).
            return "rotate", rotated, 0.25
        delay = provider_quota.retry_delay(attempt, retry_after=retry_after)
        return "retry", model_id, delay

    for attempt in range(PROVIDER_MAX_ATTEMPTS):
        if pending_delay > 0:
            await asyncio.sleep(pending_delay)
            pending_delay = 0.0
        payload["model"] = active_model
        # Pace outbound calls per model so bursts do not trip provider quota.
        # Capture the model we acquired — active_model may rotate mid-attempt,
        # and releasing the wrong semaphore permanently leaks concurrency slots
        # (which then looks like more 429s under load).
        slot_model = active_model
        await provider_quota.acquire(slot_model)
        try:
            if use_native_gemini:
                # Vertex Express Mode API keys use native generateContent (?key=),
                # not OpenAI-compat Bearer auth.
                try:
                    message = await native_generate_content(
                        client=client,
                        api_key=str(model["api_key"]),
                        model=active_model,
                        messages=cached_messages,
                        tools=use_tools or None,
                        temperature=float(thinking_params.get("temperature", temperature)),
                        top_p=float(thinking_params.get("top_p", 0.95)),
                        max_tokens=max_tokens,
                        thinking_config=thinking_params,
                    )
                except GoogleApiError as exc:
                    error = str(exc)
                    detail = exc.detail or error
                    if exc.quota:
                        action, next_model, delay = _plan_quota_retry(
                            active_model, exc.retry_after, detail, attempt,
                        )
                        if action == "stop":
                            break
                        active_model = next_model
                        pending_delay = delay
                        continue
                    if (
                        _is_retryable_provider_status(exc.status_code, detail)
                        and attempt < last_attempt
                    ):
                        pending_delay = provider_quota.retry_delay(
                            attempt, retry_after=exc.retry_after
                        )
                        continue
                    break
                if on_token and message.get("content"):
                    await on_token(str(message["content"]))
                if on_reasoning and message.get("reasoning_content"):
                    await on_reasoning(str(message["reasoning_content"]))
                provider_quota.record_success(active_model)
                record_circuit_success(model.get("provider") or "", active_model)
                return message

            if payload["stream"]:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    peek = ""
                    if response.status_code >= 400:
                        peek = (await response.aread()).decode(errors="replace").strip()[:800]
                    if provider_quota.is_quota_detail(response.status_code, peek):
                        action, next_model, delay = _plan_quota_retry(
                            active_model,
                            provider_quota.parse_retry_after_seconds(
                                getattr(response, "headers", None), peek
                            ),
                            peek,
                            attempt,
                        )
                        if action == "stop":
                            break
                        active_model = next_model
                        pending_delay = delay
                        continue
                    if _is_retryable_provider_status(response.status_code, peek) and attempt < last_attempt:
                        if not peek:
                            await response.aread()
                        pending_delay = provider_quota.retry_delay(attempt)
                        continue
                    if response.status_code >= 400:
                        detail = peek or (await response.aread()).decode(errors="replace").strip()[:800]
                        raise RuntimeError(
                            _provider_error_message(
                                response.status_code,
                                response.reason_phrase,
                                str(response.request.url),
                                detail,
                            )
                        )
                    message = await _parse_sse_completion(
                        response, on_token=on_token, on_reasoning=on_reasoning,
                    )
                    provider_quota.record_success(active_model)
                    record_circuit_success(model.get("provider") or "", active_model)
                    return message

            response = await client.post(url, headers=headers, json=payload)
            detail = (response.text or "").strip()[:800] if response.status_code >= 400 else ""
            if response.status_code >= 400 and provider_quota.is_quota_detail(
                response.status_code, detail
            ):
                action, next_model, delay = _plan_quota_retry(
                    active_model,
                    provider_quota.parse_retry_after_seconds(
                        getattr(response, "headers", None), detail
                    ),
                    detail,
                    attempt,
                )
                if action == "stop":
                    break
                active_model = next_model
                pending_delay = delay
                continue
            if _is_retryable_provider_status(response.status_code, detail) and attempt < last_attempt:
                pending_delay = provider_quota.retry_delay(attempt)
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    _provider_error_message(
                        response.status_code,
                        response.reason_phrase,
                        str(response.request.url),
                        detail,
                    )
                )
            data = response.json()
            choices = data.get("choices") or []
            if not choices or not isinstance(choices[0].get("message"), dict):
                raise RuntimeError("Provider returned no assistant message")
            message = dict(choices[0]["message"])
            usage = data.get("usage")
            if isinstance(usage, dict):
                message["_usage"] = {
                    "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                    "output_tokens": int(
                        usage.get("completion_tokens") or usage.get("output_tokens") or 0
                    ),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                    "thinking_tokens": int(usage.get("reasoning_tokens") or 0),
                }
            provider_quota.record_success(active_model)
            record_circuit_success(model.get("provider") or "", active_model)
            return message
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            error = str(exc)
            if attempt < last_attempt:
                pending_delay = provider_quota.retry_delay(attempt)
                continue
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
            # Streaming failures fall back once to a non-stream request.
            if (
                payload.get("stream")
                and attempt < last_attempt
                and "no active upstream keys" not in error.lower()
            ):
                payload["stream"] = False
                pending_delay = 0.2
                continue
            break
        finally:
            provider_quota.release(slot_model)

    if quota_exhausted:
        # Quota is transient: keep the circuit closed so the next turn can try
        # again once the cooldown elapses, and hand the UI a structured error.
        raise ProviderError(
            error,
            error_type="rate_limited",
            retryable=True,
            detail={
                "provider": model.get("provider") or "",
                "profile": model.get("profile") or "",
                "model": base_model_id,
                "models_tried": [base_model_id, *models_tried],
                "retry_after_s": round(quota_cooldown_s, 1),
                "help_url": (
                    "https://docs.cloud.google.com/gemini-enterprise-agent-platform/"
                    "models/deploy/error-code-429"
                ),
            },
        )
    record_circuit_failure(model.get("provider") or "", base_model_id)
    raise RuntimeError(error)


async def _resolve_subagent_model(
    parent_model: dict[str, Any],
    task: str,
    *,
    mode: str | None = None,
    override_api_key: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prefer NVIDIA GLM subagent; fall back to nano/base/parent when needed."""
    from syte.model_routing import fallback_subagent_profile, suggest_subagent_profile

    routing = suggest_subagent_profile(
        task,
        parent_profile=str(parent_model.get("profile") or ""),
        mode=mode,
    )
    preferred = str(routing.get("effective_profile") or "syra-nano")
    resolved_mode = str(routing.get("mode") or "research")

    available: set[str] = set()
    metas: dict[str, dict[str, Any]] = {}
    candidates = [preferred, *list(routing.get("fallback_profiles") or []), str(parent_model.get("profile") or "")]
    for name in candidates:
        profile = (name or "").strip()
        if not profile or profile in metas:
            continue
        try:
            meta = await model_metadata_for_profile(profile)
        except Exception:
            continue
        if override_api_key and override_api_key.strip():
            meta = _apply_api_key_override(meta, override_api_key.strip())
        metas[profile] = meta
        if (meta.get("api_key") or "").strip():
            available.add(profile)

    chosen, source = fallback_subagent_profile(
        preferred,
        parent_profile=str(parent_model.get("profile") or ""),
        mode=resolved_mode,
        available_profiles=available,
    )
    if chosen in metas and (metas[chosen].get("api_key") or "").strip():
        meta = metas[chosen]
        routing = {
            **routing,
            "effective_profile": chosen,
            "resolve_source": source,
            "reason": (
                routing.get("reason")
                if chosen == preferred
                else f"{routing.get('reason')}; using {chosen} ({source})"
            ),
        }
        return dict(meta), routing
    if chosen == str(parent_model.get("profile") or "") and (override_api_key and override_api_key.strip()):
        return _apply_api_key_override(dict(parent_model), override_api_key.strip()), routing
    # Last resort: parent model metadata already in hand.
    routing = {
        **routing,
        "effective_profile": parent_model.get("profile"),
        "resolve_source": "fallback_parent_key_missing",
        "reason": f"{routing.get('reason')}; fallback parent (no subagent keys configured)",
        "fallback": "api_key_missing",
    }
    return dict(parent_model), routing


async def _persist_subagent_task(
    project_id: str,
    task_id: str,
    task: str,
    *,
    turso_session_id: str | None,
    session_number: int,
    parent_request_id: str,
    mode: str,
    model: dict[str, Any],
    background: bool,
    files: list[str],
) -> None:
    """Durably record a delegated task (task text, scope, start time).

    Written to BOTH stores: local SQLite is always available (and is what
    ``await_subagent`` recovery and the GUI subagent tab read), Turso is the
    cross-host mirror and may be unconfigured or unreachable.
    """
    from syte.subagent_store import record_task as record_local_subagent_task

    await record_local_subagent_task(
        task_id,
        project_id,
        task,
        session=session_number,
        turso_session_id=turso_session_id,
        parent_request_id=parent_request_id,
        mode=mode,
        profile=str(model.get("profile") or ""),
        model=str(model.get("model") or ""),
        background=background,
        files=files,
    )
    try:
        from syte.turso_store import record_subagent_task

        await record_subagent_task(
            task_id,
            project_id,
            task,
            session_id=turso_session_id,
            session_number=session_number,
            parent_request_id=parent_request_id,
            mode=mode,
            profile=str(model.get("profile") or ""),
            model=str(model.get("model") or ""),
            background=background,
            files=files,
        )
    except Exception:
        logger.debug("failed to persist subagent task %s", task_id, exc_info=True)


async def _finalize_subagent_task(
    project_id: str,
    task_id: str,
    result: dict[str, Any],
    *,
    mode: str,
    released: list[str] | None = None,
) -> None:
    """Close the durable subagent row with outcome, usage and cost (local + Turso)."""
    from syte.subagent_store import finalize_task as finalize_local_subagent_task

    await finalize_local_subagent_task(task_id, project_id, result)
    try:
        from syte.turso_store import finalize_subagent_task

        await finalize_subagent_task(
            task_id,
            project_id,
            status=str(
                result.get("status")
                or ("completed" if result.get("ok") else "failed")
            ),
            result=str(result.get("result") or "")[:20000],
            error=str(result.get("error") or ""),
            usage=result.get("usage") if isinstance(result.get("usage"), dict) else None,
            cost=result.get("cost") if isinstance(result.get("cost"), dict) else None,
            activity_count=int(result.get("activity_count") or 0),
        )
    except Exception:
        logger.debug("failed to finalize subagent task %s", task_id, exc_info=True)
    if released:
        logger.debug(
            "subagent %s (%s) released file scope: %s", task_id, mode, ", ".join(released)
        )


async def _tool_delegate_task(
    project_id: str,
    args: dict[str, Any],
    *,
    model: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = str(args.get("task") or "").strip()
    if not task:
        return {"ok": False, "error": "empty_task", "message": "Provide a delegated task."}
    if not model:
        return {"ok": False, "error": "model_unavailable", "message": "Subagent model is unavailable."}

    sub_model, routing = await _resolve_subagent_model(
        model, task, mode=str(args.get("mode") or "") or None,
        override_api_key=(context or {}).get("override_api_key"),
    )
    mode = str(routing.get("mode") or "research")
    timeout_s = (
        SUBAGENT_RESEARCH_TIMEOUT_S if mode == "research" else SUBAGENT_TIMEOUT_S
    )
    background = bool(args.get("background"))
    ctx = context or {}
    turso_session_id = ctx.get("turso_session_id")
    session_number = int(ctx.get("session_number") or 0)
    parent_request_id = str(ctx.get("request_id") or "") or None

    # --- File scope: declared before the task is handed over -----------------
    scope_files = normalize_scope_files(args.get("files"))
    writes_files = mode == "implementation"
    if writes_files and not scope_files:
        return {
            "ok": False,
            "error": "missing_file_scope",
            "message": (
                "mode=implementation requires `files`: list every workspace path the "
                "subagent may create or edit. That scope is reserved so no other agent "
                "writes the same file in parallel."
            ),
        }

    import uuid as uuid_mod

    task_id = f"{'bg' if background else 'sub'}-{uuid_mod.uuid4().hex[:12]}"
    reserved: list[str] = []
    if writes_files:
        reserved, conflicts = _reserve_subagent_files(project_id, task_id, scope_files)
        if conflicts:
            return {
                "ok": False,
                "error": "file_scope_conflict",
                "retryable": True,
                "message": (
                    "Another subagent already owns "
                    + ", ".join(f"{c['path']} (task {c['task_id']})" for c in conflicts)
                    + ". Await that subagent first, or delegate a task with a "
                    "non-overlapping file list — never two agents on one file."
                ),
                "conflicts": conflicts,
                "reserved_files": sorted(subagent_file_scope(project_id)),
            }

    # Published to the main chat so the file split is visible before hand-off.
    await record_agent_event(
        project_id,
        "subagent_scope",
        role="system",
        title=f"Delegating {len(scope_files) or 'unscoped'} file(s) to subagent",
        detail=(
            "\n".join(f"- {path}" for path in scope_files)
            if scope_files
            else "No file scope declared (read-only research task)."
        ),
        payload={
            "request_id": parent_request_id,
            "session": session_number,
            "agent": "main",
            "task_id": task_id,
            "task": task[:500],
            "mode": mode,
            "files": scope_files,
            "reserved_files": reserved,
            "locked": bool(reserved),
            "background": background,
        },
        source=CLOUD_RUNTIME,
        turso_session_id=turso_session_id,
    )
    await _persist_subagent_task(
        project_id,
        task_id,
        task,
        turso_session_id=turso_session_id,
        session_number=session_number,
        parent_request_id=parent_request_id or "",
        mode=mode,
        model=sub_model,
        background=background,
        files=scope_files,
    )
    ctx["_subagent_count"] = int(ctx.get("_subagent_count") or 0) + 1

    subagent_ctx = {
        "task_id": task_id,
        "files": scope_files,
        "reserved": reserved,
        "turso_session_id": turso_session_id,
        "session_number": session_number,
        "parent_request_id": parent_request_id,
        "max_steps": args.get("max_steps"),
    }

    if background:
        if _count_background_subagents(project_id) >= MAX_BACKGROUND_SUBAGENTS_PER_PROJECT:
            _release_subagent_files(project_id, task_id)
            return {
                "ok": False,
                "error": "subagent_queue_full",
                "retryable": True,
                "message": (
                    f"Already running {MAX_BACKGROUND_SUBAGENTS_PER_PROJECT} background "
                    "subagents for this project. Await one with await_subagent, or run "
                    "this task in the foreground."
                ),
                "max_background": MAX_BACKGROUND_SUBAGENTS_PER_PROJECT,
            }
        bg_key = f"{project_id}:{task_id}"
        await record_agent_event(
            project_id,
            "subagent_started",
            role="system",
            title="Background subagent started",
            detail=task[:240],
            payload={
                "request_id": parent_request_id,
                "session": session_number,
                "agent": "subagent",
                "task_id": task_id,
                "task": task[:500],
                "mode": mode,
                "profile": sub_model.get("profile"),
                "background": True,
                "files": scope_files,
                "routing": routing,
            },
            source=CLOUD_RUNTIME,
            turso_session_id=turso_session_id,
        )
        bg_task = asyncio.create_task(
            _run_subagent(
                project_id,
                task,
                sub_model,
                mode=mode,
                task_id=task_id,
                background=True,
                parent_request_id=parent_request_id,
                subagent_ctx=subagent_ctx,
            )
        )
        _background_subagents[bg_key] = bg_task

        def _cleanup(done: asyncio.Task[Any], *, key: str = bg_key, tid: str = task_id) -> None:
            _background_subagents.pop(key, None)
            # Idempotent — _run_subagent also releases on its own exit paths.
            _release_subagent_files(project_id, tid)
            if key in _background_subagent_results:
                return
            if done.cancelled():
                _store_subagent_result(
                    key,
                    {
                        "ok": False,
                        "error": "subagent_cancelled",
                        "status": "cancelled",
                        "task_id": tid,
                        "message": "Subagent cancelled",
                    },
                )
                return
            exc = done.exception() if not done.cancelled() else None
            if exc is not None:
                _store_subagent_result(
                    key,
                    {
                        "ok": False,
                        "error": "subagent_failed",
                        "status": "failed",
                        "task_id": tid,
                        "message": str(exc) or type(exc).__name__,
                    },
                )
                return
            try:
                result = done.result()
            except Exception as result_exc:
                result = {
                    "ok": False,
                    "error": "subagent_failed",
                    "status": "failed",
                    "task_id": tid,
                    "message": str(result_exc) or type(result_exc).__name__,
                }
            if isinstance(result, dict):
                _store_subagent_result(key, {**result, "task_id": tid, "status": result.get("status") or ("completed" if result.get("ok") else "failed")})
            else:
                _store_subagent_result(
                    key,
                    {
                        "ok": True,
                        "task_id": tid,
                        "status": "completed",
                        "result": str(result),
                    },
                )

        bg_task.add_done_callback(_cleanup)
        return {
            "ok": True,
            "task_id": task_id,
            "status": "running",
            "message": (
                "Background subagent started — call await_subagent to collect results. "
                f"Reserved files: {', '.join(scope_files) or 'none'} (do not edit them yourself)."
            ),
            "task": task,
            "mode": mode,
            "files": scope_files,
            "reserved_files": reserved,
            "profile": sub_model.get("profile"),
            "routing": routing,
            "timeout_s": timeout_s,
        }

    await record_agent_event(
        project_id,
        "subagent_started",
        role="system",
        title="Subagent started",
        detail=task[:240],
        payload={
            "request_id": parent_request_id,
            "session": session_number,
            "agent": "subagent",
            "task_id": task_id,
            "task": task[:500],
            "mode": mode,
            "profile": sub_model.get("profile"),
            "background": False,
            "files": scope_files,
            "routing": routing,
        },
        source=CLOUD_RUNTIME,
        turso_session_id=turso_session_id,
    )
    try:
        result = await _run_subagent(
            project_id,
            task,
            sub_model,
            mode=mode,
            task_id=task_id,
            parent_request_id=parent_request_id,
            subagent_ctx=subagent_ctx,
        )
    finally:
        _release_subagent_files(project_id, task_id)
    await record_agent_event(
        project_id,
        "subagent_completed" if result.get("ok") else "subagent_failed",
        role="system",
        title="Subagent finished" if result.get("ok") else "Subagent failed",
        detail=str(result.get("error") or result.get("result") or "")[:240],
        payload={
            "request_id": parent_request_id,
            "session": session_number,
            "agent": "subagent",
            "task_id": task_id,
            "mode": mode,
            "profile": sub_model.get("profile"),
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
            "files": scope_files,
            "usage": result.get("usage"),
            "cost": result.get("cost"),
        },
        source=CLOUD_RUNTIME,
        turso_session_id=turso_session_id,
    )
    await _finalize_subagent_task(
        project_id,
        task_id,
        result,
        mode=mode,
        released=scope_files if reserved else [],
    )
    return {
        **result,
        "task_id": task_id,
        "mode": mode,
        "files": scope_files,
        "profile": sub_model.get("profile"),
        "routing": routing,
    }


async def _tool_await_subagent(project_id: str, args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "missing_task_id", "message": "Provide task_id from delegate_task."}
    key = f"{project_id}:{task_id}"
    timeout_s = max(1, min(int(args.get("timeout_s") or 120), 600))

    stored = _background_subagent_results.get(key)
    if stored is not None:
        return {**stored, "task_id": task_id, "awaited": True}

    task = _background_subagents.get(key)
    if task is None:
        # The in-memory result cache is bounded and does not survive a restart.
        # Recover the outcome from the durable local record instead of telling
        # the parent the subagent never existed (DAV-210).
        from syte.subagent_store import TERMINAL_STATUSES, get_task as get_local_subagent_task

        stored_row = await get_local_subagent_task(task_id, project_id)
        if stored_row and stored_row.get("status") in TERMINAL_STATUSES:
            recovered = {
                "ok": stored_row["status"] in {"completed", "partial"},
                "status": stored_row["status"],
                "task_id": task_id,
                "task": stored_row.get("task"),
                "mode": stored_row.get("mode"),
                "files": stored_row.get("files") or [],
                "result": stored_row.get("result") or "",
                "usage": stored_row.get("usage") or {},
                "cost": stored_row.get("cost") or {},
                "recovered_from": "local_store",
            }
            if stored_row.get("error"):
                recovered["error"] = stored_row["error"]
            _store_subagent_result(key, recovered)
            return {**recovered, "awaited": True}
        if stored_row:
            return {
                "ok": False,
                "error": "subagent_lost",
                "retryable": False,
                "status": "lost",
                "message": (
                    f"Subagent '{task_id}' was recorded as still running but its worker is "
                    "gone (service restart or interrupt). Re-delegate the task."
                ),
                "task_id": task_id,
                "task": stored_row.get("task"),
                "files": stored_row.get("files") or [],
            }
        return {
            "ok": False,
            "error": "subagent_not_found",
            "message": (
                f"No background subagent '{task_id}' is running or stored for this project. "
                "It may have been cancelled or the task_id is wrong."
            ),
            "task_id": task_id,
        }

    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": "await_timeout",
            "retryable": True,
            "message": f"Subagent {task_id} still running after {timeout_s}s — call await_subagent again.",
            "task_id": task_id,
            "status": "running",
        }
    except asyncio.CancelledError:
        stored = _background_subagent_results.get(key) or {
            "ok": False,
            "error": "subagent_cancelled",
            "status": "cancelled",
            "message": "Subagent cancelled",
            "task_id": task_id,
        }
        return {**stored, "awaited": True}

    if not isinstance(result, dict):
        result = {"ok": True, "result": str(result), "status": "completed"}
    _store_subagent_result(key, {**result, "task_id": task_id})
    return {**result, "task_id": task_id, "awaited": True}


async def _run_subagent(
    project_id: str,
    task: str,
    model: dict[str, Any],
    *,
    mode: str = "research",
    task_id: str | None = None,
    background: bool = False,
    parent_request_id: str | None = None,
    subagent_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded secondary tool loop and return its findings to the parent.

    Terminal ``subagent_*`` events and stored results are emitted here only for
    *background* runs; foreground delegation reports them from
    :func:`_tool_delegate_task` so the parent never double-reports one task.
    """
    resolved_mode = mode if mode in {"research", "implementation"} else "research"
    timeout_s = (
        SUBAGENT_RESEARCH_TIMEOUT_S if resolved_mode == "research" else SUBAGENT_TIMEOUT_S
    )
    turso_session_id = (subagent_ctx or {}).get("turso_session_id")
    try:
        result = await asyncio.wait_for(
            _run_subagent_loop(
                project_id,
                task,
                model,
                mode=resolved_mode,
                task_id=task_id,
                parent_request_id=parent_request_id,
                subagent_ctx=subagent_ctx,
            ),
            timeout=timeout_s,
        )
        if task_id and background:
            key = f"{project_id}:{task_id}"
            _store_subagent_result(
                key,
                {
                    **result,
                    "task_id": task_id,
                    "status": "completed" if result.get("ok") else "failed",
                },
            )
            await record_agent_event(
                project_id,
                "subagent_completed" if result.get("ok") else "subagent_failed",
                role="system",
                title="Background subagent finished" if result.get("ok") else "Background subagent failed",
                detail=str(result.get("error") or result.get("result") or "")[:240],
                payload={
                    "request_id": parent_request_id,
                    "session": int((subagent_ctx or {}).get("session_number") or 0),
                    "agent": "subagent",
                    "task_id": task_id,
                    "mode": resolved_mode,
                    "profile": model.get("profile"),
                    "ok": bool(result.get("ok")),
                    "error": result.get("error"),
                    "files": (subagent_ctx or {}).get("files") or [],
                    "usage": result.get("usage"),
                    "cost": result.get("cost"),
                },
                source=CLOUD_RUNTIME,
                turso_session_id=turso_session_id,
            )
            await _finalize_subagent_task(
                project_id, task_id, result, mode=resolved_mode,
            )
        return result
    except asyncio.TimeoutError:
        payload = {
            "ok": False,
            "error": "subagent_timeout",
            "status": "timeout",
            "retryable": True,
            "message": (
                f"Subagent timed out after {int(timeout_s)}s "
                f"(mode={resolved_mode}). Narrow the task or raise tool timeouts carefully."
            ),
            "task": task,
            "mode": resolved_mode,
            "timeout_s": timeout_s,
        }
        if task_id and background:
            _store_subagent_result(f"{project_id}:{task_id}", {**payload, "task_id": task_id})
            await record_agent_event(
                project_id,
                "subagent_failed",
                role="system",
                title="Subagent timed out",
                detail=payload["message"],
                payload={
                    "request_id": parent_request_id,
                    "agent": "subagent",
                    "task_id": task_id,
                    "mode": resolved_mode,
                    "error": "subagent_timeout",
                },
                source=CLOUD_RUNTIME,
                turso_session_id=turso_session_id,
            )
            await _finalize_subagent_task(
                project_id, task_id, payload, mode=resolved_mode,
            )
        return payload
    except asyncio.CancelledError:
        payload = {
            "ok": False,
            "error": "subagent_cancelled",
            "status": "cancelled",
            "message": "Subagent cancelled",
            "task": task,
            "mode": resolved_mode,
        }
        if task_id and background:
            _store_subagent_result(f"{project_id}:{task_id}", {**payload, "task_id": task_id})
            await _finalize_subagent_task(
                project_id, task_id, payload, mode=resolved_mode,
            )
        return payload
    finally:
        if task_id:
            _release_subagent_files(project_id, task_id)


async def _run_subagent_loop(
    project_id: str,
    task: str,
    model: dict[str, Any],
    *,
    mode: str = "research",
    task_id: str | None = None,
    parent_request_id: str | None = None,
    subagent_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    research = mode == "research"
    scope = subagent_ctx or {}
    max_steps = MAX_SUBAGENT_RESEARCH_STEPS if research else MAX_SUBAGENT_STEPS
    # Parent may raise the budget for hard implementation tasks (still capped).
    requested_steps = scope.get("max_steps")
    try:
        if requested_steps is not None:
            max_steps = max(4, min(int(requested_steps), 40 if research else 48))
    except (TypeError, ValueError):
        pass
    tools = SUBAGENT_RESEARCH_TOOLS if research else SUBAGENT_TOOLS
    # Keep subagent tool dumps tight for speed/cost.
    tool_chars = min(_model_tool_result_chars(model), 6_000 if research else 8_000)
    scope_files = list(scope.get("files") or [])
    turso_session_id = scope.get("turso_session_id")
    session_number = int(scope.get("session_number") or 0)
    activity_count = 0
    last_substantive = ""

    async def _emit_subagent(
        event_type: str,
        *,
        role: str = "system",
        title: str = "",
        detail: str = "",
        base: dict[str, Any] | None = None,
        tool: str = "",
    ) -> None:
        """Record one subagent activity line (subagent chat tab + Turso)."""
        nonlocal activity_count
        if not task_id:
            return
        activity_count += 1
        payload = {
            "request_id": parent_request_id,
            "session": session_number,
            "agent": "subagent",
            "task_id": task_id,
            "subagent_mode": mode,
            "subagent_task": task[:240],
            **(base or {}),
        }
        try:
            await record_agent_event(
                project_id,
                event_type,
                role=role,
                title=title,
                detail=detail,
                payload=payload,
                source=CLOUD_RUNTIME,
                turso_session_id=turso_session_id,
                agent="subagent",
                subagent_task_id=task_id,
            )
        except Exception:
            logger.debug("subagent activity event failed", exc_info=True)
        try:
            from syte.turso_store import record_subagent_activity

            await record_subagent_activity(
                task_id,
                project_id,
                event_type,
                session_id=turso_session_id,
                parent_request_id=parent_request_id or "",
                tool=tool,
                title=title,
                detail=detail,
                payload=payload,
            )
        except Exception:
            logger.debug("subagent activity mirror failed", exc_info=True)

    file_scope_line = (
        "You own exactly these files and must not create or edit any other file: "
        + ", ".join(scope_files)
        + ". "
        if scope_files
        else ""
    )
    mode_line = (
        "Mode: research (read-only). Do not attempt writes, deletes, shell mutations, or service control."
        if research
        else
        "Mode: implementation. You may edit files. Prefer small, verified changes. Do not deploy "
        "or run production builds."
    )
    instruction = (
        "You are a focused Syte subagent sharing the parent's project workspace. Complete only the "
        "delegated task. Inspect files and use workspace tools as needed. Do not deploy, start, stop, "
        "update, or build the production service. Prefer the isolated preview for visual checks. "
        f"{mode_line} {file_scope_line}"
        f"Budget: finish within {max_steps} tool rounds. Batch reads, avoid exploratory loops, and "
        "return a concise final answer as soon as the task is done — do not keep calling tools after "
        "you have enough evidence.\n"
        "Return concise findings, changes, and verification for the parent agent.\n\n"
        f"Application source: {workspace_path(project_id) / 'app'}"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": task},
    ]
    usage_acc: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "total_tokens": 0,
        "steps": 0,
    }
    for step in range(max_steps):
        assistant = await _provider_completion(model, messages, tools=tools)
        step_usage = assistant.get("_usage") if isinstance(assistant.get("_usage"), dict) else {}
        if step_usage:
            usage_acc["input_tokens"] += int(step_usage.get("input_tokens") or 0)
            usage_acc["output_tokens"] += int(step_usage.get("output_tokens") or 0)
            usage_acc["thinking_tokens"] += int(step_usage.get("thinking_tokens") or 0)
            usage_acc["total_tokens"] += int(step_usage.get("total_tokens") or 0)
            usage_acc["steps"] += 1
        content = str(assistant.get("content") or "")
        if content.strip():
            last_substantive = content.strip()
        calls = assistant.get("tool_calls") or []
        stored_calls = calls if isinstance(calls, list) else []
        next_assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if stored_calls:
            next_assistant["tool_calls"] = stored_calls
        messages.append(next_assistant)
        # Subagent reasoning, annotated with where in its loop it was thinking.
        reasoning = str(assistant.get("reasoning_content") or "").strip()
        if reasoning:
            await _emit_subagent(
                "thinking",
                role="assistant",
                title="Thinking",
                detail=reasoning[:4000],
                base=_thinking_context(
                    step=step + 1,
                    phase="before_tools" if stored_calls else "final_answer",
                    calls=stored_calls,
                    scope_files=scope_files,
                ),
            )
        if not stored_calls:
            cost_info = _estimate_turn_cost(model, usage_acc)
            if content.strip():
                await _emit_subagent(
                    "assistant_message",
                    role="assistant",
                    title="Subagent result",
                    detail=content.strip()[:4000],
                    base={"steps": step + 1, "usage": usage_acc, "cost": cost_info},
                )
            return {
                "ok": True,
                "task": task,
                "mode": mode,
                "result": content.strip() or "Task completed.",
                "steps": step + 1,
                "usage": usage_acc,
                "cost": cost_info,
                "profile": model.get("profile"),
                "parent_request_id": parent_request_id,
                "task_id": task_id,
                "activity_count": activity_count,
                "files": scope_files,
            }
        for call in stored_calls:
            _raise_if_cancelled()
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            try:
                args = json.loads(function.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            await _emit_subagent(
                "tool_call_started",
                title=name,
                detail=json.dumps(args)[:1000],
                base={"tool": name, "arguments": args, "phase": "started", "step": step + 1},
                tool=name,
            )
            if research and name in _SUBAGENT_MUTATING_TOOLS:
                result = {
                    "ok": False,
                    "error": "research_readonly",
                    "message": (
                        f"Tool {name} is blocked in research mode. "
                        "Re-delegate with mode=implementation if edits are required."
                    ),
                }
            elif (
                scope_files
                and name in {"write_file", "delete_file"}
                and _normalize_scope_path(args.get("path")) not in scope_files
            ):
                # Hard scope: a subagent may only touch the files the parent reserved
                # for it, so parallel subagents can never collide on one file.
                result = {
                    "ok": False,
                    "error": "outside_file_scope",
                    "message": (
                        f"{args.get('path')} is outside your delegated file scope "
                        f"({', '.join(scope_files)}). Report the needed change back to the "
                        "parent agent instead of editing it."
                    ),
                    "files": scope_files,
                }
            else:
                try:
                    result = await _execute_tool(
                        project_id,
                        name,
                        args,
                        model=model,
                        context={
                            "max_tool_result_chars": tool_chars,
                            "request_id": parent_request_id,
                            "session_number": session_number,
                            "turso_session_id": turso_session_id,
                            "subagent": True,
                            "subagent_mode": mode,
                            "subagent_task_id": task_id,
                            "subagent_files": scope_files,
                        },
                    )
                except asyncio.CancelledError:
                    result = {
                        "ok": False,
                        "error": "cancelled",
                        "message": f"Tool {name} cancelled",
                    }
                    public = result
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or f"subagent-{step}"),
                        "content": _truncate_tool_payload(public, max_chars=tool_chars),
                    })
                    raise
                except Exception as exc:
                    result = {
                        "ok": False,
                        "error": "tool_failed",
                        "message": str(exc) or type(exc).__name__,
                    }
            public = {k: v for k, v in result.items() if not str(k).startswith("_")}
            encoded = _truncate_tool_payload(public, max_chars=tool_chars)
            await _emit_subagent(
                "tool_call_finished",
                title=name,
                detail=encoded[:4000],
                base={
                    "tool": name,
                    "ok": bool(result.get("ok")),
                    "phase": "finished",
                    "step": step + 1,
                    "path": args.get("path"),
                    "command": args.get("command"),
                    **(
                        {}
                        if result.get("ok")
                        else {
                            "error": str(result.get("error") or "tool_failed"),
                            "message": str(result.get("message") or "")[:1000],
                            "retryable": bool(result.get("retryable")),
                        }
                    ),
                },
                tool=name,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": str(call.get("id") or f"subagent-{step}"),
                "content": encoded,
            })
    cost_info = _estimate_turn_cost(model, usage_acc)
    # Prefer a partial success with whatever the subagent already learned over a
    # hard failure — parents were treating every step_limit as a dead end.
    if last_substantive:
        await _emit_subagent(
            "assistant_message",
            role="assistant",
            title="Subagent partial result",
            detail=last_substantive[:4000],
            base={"steps": max_steps, "usage": usage_acc, "cost": cost_info, "partial": True},
        )
        return {
            "ok": True,
            "partial": True,
            "error": "subagent_step_limit",
            "status": "partial",
            "message": (
                f"Subagent hit the {max_steps}-step budget and returned partial findings. "
                "Re-delegate a narrower follow-up if more work remains."
            ),
            "task": task,
            "mode": mode,
            "result": last_substantive,
            "steps": max_steps,
            "usage": usage_acc,
            "cost": cost_info,
            "profile": model.get("profile"),
            "task_id": task_id,
            "activity_count": activity_count,
            "files": scope_files,
        }
    return {
        "ok": False,
        "error": "subagent_step_limit",
        "status": "step_limit",
        "retryable": True,
        "message": (
            f"Subagent did not finish within {max_steps} steps. "
            "Narrow the task, pass mode=research for exploration, or raise max_steps."
        ),
        "task": task,
        "mode": mode,
        "usage": usage_acc,
        "cost": cost_info,
        "profile": model.get("profile"),
        "task_id": task_id,
        "activity_count": activity_count,
        "files": scope_files,
    }


async def communicate_with_agent(
    project_id: str, message: str, *, model_profile: str | None = None,
    thinking_level: int | str | None = None,
    source: str = "api", auto_start: bool = True, background: bool = False,
    improve_from_screenshot: bool = False,
    visual_analysis_id: str | None = None,
    idempotency_key: str | None = None,
    override_api_key: str | None = None,
) -> dict[str, Any]:
    from syte.model_routing import normalize_explicit_profile, suggest_model_profile

    model_profile = normalize_explicit_profile(model_profile)
    routing = suggest_model_profile(
        message,
        explicit_profile=model_profile,
        thinking_level=thinking_level,
        improve_from_screenshot=improve_from_screenshot or bool(visual_analysis_id),
    )
    if routing.get("auto_applied") and not model_profile:
        model_profile = routing["effective_profile"]

    if background:
        from syte.agent_jobs import submit_agent_request
        result = await submit_agent_request(
            project_id,
            message,
            model_profile=model_profile,
            thinking_level=thinking_level,
            source=source,
            auto_start=auto_start,
            idempotency_key=idempotency_key,
            override_api_key=override_api_key,
        )
        return {**result, "model_routing": routing}
    from syte.agent_jobs import new_request_id, project_agent_lock
    request_id = new_request_id()
    async with project_agent_lock(project_id):
        result = await _communicate_with_agent_impl(
            project_id, message, model_profile=model_profile,
            thinking_level=thinking_level, source=source,
            auto_start=auto_start, request_id=request_id,
            improve_from_screenshot=improve_from_screenshot,
            visual_analysis_id=visual_analysis_id,
            override_api_key=override_api_key,
        )
        return {**result, "model_routing": routing}


async def _communicate_with_agent_impl(
    project_id: str, message: str, *, model_profile: str | None = None,
    thinking_level: int | str | None = None,
    source: str = "api", auto_start: bool = True, emit_request_started: bool = True,
    request_id: str | None = None,
    session_number: int | None = None,
    message_index_start: int = 0,
    turso_session_id: str | None = None,
    improve_from_screenshot: bool = False,
    visual_analysis_id: str | None = None,
    override_api_key: str | None = None,
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

    turn_profile = gen.get("builder_profile") or gen["model_profile"]
    # When an explicit model_profile was passed for this turn, resolve keys for
    # that profile without requiring a separate thinker swap.
    if turn_profile and turn_profile != project.get("agent_model_profile"):
        project = {**project, "agent_model_profile": turn_profile}

    if auto_start and project.get("agent_status") != "running":
        ok, start_message, _ = await start_agent(project_id, override_api_key=override_api_key)
        if not ok:
            return {"ok": False, "error": "agent_start_failed", "message": start_message, "request_id": request_id}
    model = await selected_model_metadata(project)
    if override_api_key and override_api_key.strip():
        model = _apply_api_key_override(model, override_api_key.strip())
    # Selected profile handles both planning and the tool/code loop.
    if not model["api_key"]:
        secret_env = model.get("secret_env") or profile_provider(model.get("profile") or DEFAULT_PROFILE)["secret_env"]
        return {
            "ok": False,
            "error": "api_key_missing",
            "message": (
                f"Provider API key is not configured for {model.get('profile') or 'selected'} "
                f"({model.get('label') or model.get('provider_label') or 'provider'}). "
                f"Save it in AI provider settings or set process env {secret_env}."
            ),
            "request_id": request_id,
            "profile": model.get("profile"),
            "secret_env": secret_env,
        }

    # One durable chat session per project conversation. Follow-up messages
    # reuse the latest session so history accumulates and the UI only streams
    # session=last. Each turn still opens its own Turso UUID for poll clients.
    opened_turso_session = False
    if session_number is None:
        session_number = await ensure_latest_session(project_id, model["profile"])
        message_index = 0
        turso_session_id = await open_turso_session(
            project_id, session_number=session_number, model_profile=model["profile"],
        )
        opened_turso_session = True
        if turso_session_id:
            await set_turso_session_id(project_id, turso_session_id)
        from syte.agent_memory import upsert_session_meta

        await upsert_session_meta(
            project_id,
            session_number,
            turso_session_id=turso_session_id,
            status="open",
            model_profile=model["profile"],
        )
    else:
        message_index = max(0, int(message_index_start or 0))

    thinking_probe = build_model_thinking_params(
        gen,
        provider=str(model.get("provider") or ""),
        model=str(model.get("model") or ""),
        api_base=str(model.get("api_base") or ""),
    )
    if thinking_probe.get("thinking_requested") and not thinking_probe.get("thinking_supported"):
        await record_agent_event(
            project_id,
            "status",
            title="Thinking not supported",
            detail=(
                f"thinking_level={gen.get('thinking_level')} requested, but model "
                f"{model.get('model')} / provider {model.get('provider')} does not "
                "accept native thinking params — slider ignored for this turn."
            ),
            payload={
                "request_id": request_id,
                "session": session_number,
                "thinking_level": gen.get("thinking_level"),
                "thinking_requested": True,
                "thinking_supported": False,
                "model": model.get("model"),
                "provider": model.get("provider"),
            },
            source=source,
            turso_session_id=turso_session_id,
        )

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

    # Durable per-request row: the request text + its timestamp are stored now;
    # activity volume and cost are written back when generation finishes.
    turn_started_at = _now()
    try:
        from syte.turso_store import record_request as record_turso_request

        await record_turso_request(
            request_id,
            project_id,
            message,
            session_id=turso_session_id,
            session_number=int(session_number or 0),
            source=source,
            model_profile=str(model.get("profile") or ""),
            model=str(model.get("model") or ""),
            provider=str(model.get("provider") or ""),
            thinking_level=gen.get("thinking_level"),
            timestamp=turn_started_at,
        )
    except Exception:
        logger.debug("failed to persist request row %s", request_id, exc_info=True)

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

    # Prefer indexed files referenced in the prompt over a full workspace crawl.
    from syte.agent_memory import latest_summary, lookup_workspace_paths, prompt_tags_from_message
    from syte.agent_skills import match_active_skills, skill_hint_block
    from syte.nextjs_layout import is_nextjs_repo
    from syte.site_planner import (
        is_substantive_site_request,
        is_website_request,
        site_request_needs_clarification,
    )

    app_root = workspace_path(project_id) / "app"
    is_website = is_nextjs_repo(app_root) or is_nextjs_repo(workspace_path(project_id))
    website_work = is_website or is_website_request(message)
    site_plan_required = is_substantive_site_request(message)
    site_needs_clarification = site_request_needs_clarification(message)
    site_question_required = bool(site_plan_required and site_needs_clarification and not is_website)
    tags = prompt_tags_from_message(message)

    # Overlap independent reads so first provider byte is not gated on serial I/O.
    history_limit = _model_history_limit(model)
    tool_result_chars = _model_tool_result_chars(model)
    instruction_task = asyncio.create_task(_build_syte_instruction_parts(project_id))
    history_task = asyncio.create_task(
        conversation_messages(project_id, limit=history_limit, last_session_only=True)
    )
    summary_task = asyncio.create_task(latest_summary(project_id))
    skills_task = asyncio.create_task(
        match_active_skills(project_id, message, is_nextjs=website_work)
    )
    lookup_task = (
        asyncio.create_task(lookup_workspace_paths(project_id, tags=tags, limit=12))
        if tags
        else None
    )

    static_instruction, dynamic_instruction = await instruction_task
    history = await history_task
    prior_summary = await summary_task
    matched_skills = await skills_task
    hinted = await lookup_task if lookup_task is not None else []

    turn_hints: list[str] = []
    plan_already_seeded = False
    if site_plan_required:
        turn_hints.append(
            "Substantive website workflow (hard gate): before any file/tool inspection, decide "
            "whether one batched clarification is materially needed. If yes, your FIRST tool call "
            "must be ask_question. If the brief is already sufficient, your FIRST tool call must be "
            "update_plan. After ask_question returns an answer, call update_plan before any other "
            "tool. The plan must address information architecture, visual direction, content/assets, "
            "individual shadcn component mapping (no Blocks), responsive behavior, accessibility and "
            "interaction states, plus desktop/phone verification. Spend the needed reasoning effort "
            "on how the site should look and work before implementation."
        )
        if site_needs_clarification:
            turn_hints.append(
                "The request appears to omit a visual direction for a new site. Ask one concise "
                "choice question using the named themes unless existing context supplies that choice."
            )
    elif gen.get("mandatory_plan"):
        turn_hints.append(
            "Thinking mode: Deep/Max (hard gate). Your FIRST tool call MUST be "
            "update_plan with a concrete ordered plan. Other tools are rejected until "
            "the plan is submitted. After planning, execute the steps."
        )

    if hinted:
        turn_hints.append(
            "## Prompt-matched workspace files (from index)\n"
            + "\n".join(
                f"- {item['path']} [{', '.join(item.get('semantic_tags') or [])}]"
                for item in hinted
            )
        )

    skill_hint = skill_hint_block(matched_skills)
    if skill_hint:
        turn_hints.append(skill_hint)

    # Visual feedback loop: attach structured screenshot analysis as primary critique source.
    analysis_payload = None
    if visual_analysis_id or improve_from_screenshot:
        from syte.agent_memory import (
            get_visual_analysis,
            latest_visual_analysis,
            visual_feedback_prompt,
        )

        analysis_payload = (
            await get_visual_analysis(visual_analysis_id)
            if visual_analysis_id
            else await latest_visual_analysis(project_id)
        )
        if analysis_payload:
            turn_hints.append(visual_feedback_prompt(analysis_payload))

    # Seed .aiignore so lockfiles / build artifacts never enter listings.
    from syte.token_efficiency import ensure_workspace_aiignore

    ensure_workspace_aiignore(workspace_path(project_id) / "app")

    # Complex multi-page site builds: publish a planner decomposition before execution.
    # The selected model plans and builds — no separate thinker.
    from syte.site_planner import is_complex_site_request, order_subtasks, plan_complex_site

    planner_model = model
    if (
        website_work
        and is_complex_site_request(message)
        and not site_needs_clarification
        and not improve_from_screenshot
    ):
        plan = await plan_complex_site(
            project_id,
            message,
            provider_completion=_provider_completion,
            model=planner_model,
        )
        if plan.get("ok") and plan.get("subtasks"):
            ordered = order_subtasks(list(plan["subtasks"]))
            from syte.agent_artifacts import save_plan

            plan_steps = [str(item.get("task") or "") for item in ordered if item.get("task")]
            if plan_steps:
                plan_row = await save_plan(
                    project_id,
                    plan_steps,
                    note=f"planner:{plan.get('planner') or 'llm'}",
                    request_id=request_id,
                    session_number=session_number,
                    turso_session_id=turso_session_id,
                )
                await record_agent_event(
                    project_id,
                    "thinking",
                    role="assistant",
                    title="Site plan",
                    detail="\n".join(f"{i}. {s}" for i, s in enumerate(plan_steps, 1))[:4000],
                    payload=_mark_payload(
                        status="d",
                        kind="plan",
                        base={
                            "plan_id": (plan_row or {}).get("id"),
                            "steps": plan_steps,
                            **_thinking_context(step=0, phase="site_plan"),
                        },
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
                turn_hints.append(
                    "## Planner decomposition (execute in order, respecting deps)\n"
                    + "\n".join(
                        f"{i}. {item.get('task')} "
                        f"(files: {', '.join(item.get('files') or []) or 'n/a'}; "
                        f"deps: {', '.join(item.get('deps') or []) or 'none'})"
                        for i, item in enumerate(ordered, 1)
                    )
                )
                plan_already_seeded = True

    dynamic_instruction = "\n\n".join(
        part for part in [dynamic_instruction, *turn_hints] if part and str(part).strip()
    )

    # Always inject the latest cross-session summary when the live history is thin
    # (new session, or fewer than 6 user/assistant turns) so context survives restarts.
    live_ua = [
        m for m in history
        if m.get("role") in {"user", "assistant"} and str(m.get("content") or "").strip()
    ]
    if prior_summary and (
        prior_summary.get("up_to_session_number", 0) < int(session_number or 0)
        or len(live_ua) < 6
    ):
        decisions = prior_summary.get("key_decisions") or []
        decision_block = ""
        if decisions:
            decision_block = "\nKey decisions:\n- " + "\n- ".join(
                str(d) for d in decisions[:8]
            )
        history = [
            {
                "role": "system",
                "content": (
                    "Cross-session memory (authoritative; do not re-discover):\n"
                    + str(prior_summary.get("summary_text") or "")[:4500]
                    + decision_block
                    + (
                        f"\nTechnical state:\n{prior_summary.get('technical_state')}"
                        if prior_summary.get("technical_state")
                        else ""
                    )
                ),
            },
            *history[-12:],
        ]
    messages = [_system_message_for_provider(static_instruction, dynamic_instruction, model), *history]
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
        from syte.agent_activity import get_delta_batcher

        # Minimal-delta hot path: batch 16–32 tokens / 300–500 chars before
        # one SSE frame so Turso durable writes stay unblocked.
        batcher = get_delta_batcher(
            project_id,
            "token_delta",
            request_id=request_id,
            session=session_number,
            source=source,
        )
        await batcher.push(delta)

    async def _emit_thinking(delta: str) -> None:
        if not delta:
            return
        from syte.agent_activity import get_delta_batcher

        batcher = get_delta_batcher(
            project_id,
            "thinking_delta",
            request_id=request_id,
            session=session_number,
            source=source,
        )
        await batcher.push(delta)

    async def _flush_hot_deltas() -> None:
        from syte.agent_activity import flush_delta_batchers

        await flush_delta_batchers(project_id, request_id=request_id)

    tool_context: dict[str, Any] = {
        "request_id": request_id,
        "session_number": session_number,
        "turso_session_id": turso_session_id,
        "emit_question": _emit_question,
        "thinking_level": gen.get("thinking_level"),
        "mandatory_plan": bool(gen.get("mandatory_plan") or site_plan_required),
        "plan_gate_reason": "website" if site_plan_required else "thinking",
        "question_required": site_question_required,
        "question_answered": False,
        "plan_submitted": bool(plan_already_seeded),
        "model": model,
        "max_tool_result_chars": tool_result_chars,
        "override_api_key": override_api_key,
    }

    max_tool_steps = int(gen.get("max_tool_steps") or 48)
    temperature = float(gen.get("temperature") or 0.2)
    want_stream = bool(gen.get("stream"))
    turn_usage: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "total_tokens": 0,
        "steps": 0,
    }

    try:
        for step in itertools.count():
            _raise_if_cancelled()
            allow_tools = step < max_tool_steps
            assistant = await _provider_completion(
                model,
                messages,
                tools=TOOLS if allow_tools else [],
                temperature=temperature,
                thinking_config=gen,
                stream=want_stream,
                on_token=_emit_token if want_stream else None,
                on_reasoning=_emit_thinking if want_stream else None,
            )
            content = str(assistant.get("content") or "")
            reasoning = assistant.get("reasoning_content")
            if isinstance(reasoning, str) and len(reasoning) > MAX_REASONING_HISTORY_CHARS:
                reasoning = reasoning[:MAX_REASONING_HISTORY_CHARS] + "\n… [thinking truncated]"
            tool_calls = assistant.get("tool_calls") or []
            stored_calls = tool_calls if isinstance(tool_calls, list) else []
            # Cap: ignore tool calls past the thinking-level budget.
            if not allow_tools:
                stored_calls = []
            step_usage = assistant.get("_usage") if isinstance(assistant.get("_usage"), dict) else {}
            if step_usage:
                turn_usage["input_tokens"] += int(step_usage.get("input_tokens") or 0)
                turn_usage["output_tokens"] += int(step_usage.get("output_tokens") or 0)
                turn_usage["thinking_tokens"] += int(step_usage.get("thinking_tokens") or 0)
                turn_usage["total_tokens"] += int(step_usage.get("total_tokens") or 0)
                turn_usage["steps"] += 1
            # Flush batched token/thinking deltas before durable message/tool events.
            await _flush_hot_deltas()
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
            # Preserve exact Vertex parts (thoughtSignature) for the in-turn resume
            # after tools like ask_question — required by Gemini 3.
            if isinstance(assistant.get("_vertex_parts"), list):
                next_assistant["_vertex_parts"] = assistant["_vertex_parts"]
            messages.append(next_assistant)
            # Prefer provider reasoning for thinking events — do not mix answer text in (DAV-136).
            visible_thought = str(reasoning or "").strip()
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
                        base={
                            "plan_id": (plan_row or {}).get("id"),
                            # Where this thought happened (step + what it was about).
                            **_thinking_context(
                                step=step + 1,
                                phase="before_tools",
                                calls=stored_calls,
                            ),
                        },
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
            if not stored_calls:
                # Auto-screenshot after UI edits when the agent forgot to capture one.
                if (
                    tool_context.get("_ui_edit_detected")
                    and not tool_context.get("_screenshot_captured")
                    and is_website
                ):
                    try:
                        shot = await _execute_tool(
                            project_id,
                            "screenshot_preview",
                            {"route": "/", "viewports": ["desktop"]},
                            model=model,
                            context=tool_context,
                        )
                        chat_shots = shot.pop("_chat_screenshots", None) if isinstance(shot, dict) else None
                        if chat_shots:
                            await record_agent_event(
                                project_id,
                                "screenshot",
                                role="assistant",
                                title="Screenshot / (auto)",
                                detail=str(shot.get("message") or "Auto screenshot after UI edits")[:1000],
                                payload=_mark_payload(
                                    status="d",
                                    kind="screenshot",
                                    base={
                                        "route": "/",
                                        "auto": True,
                                        "screenshots": [
                                            {
                                                "id": s.get("id"),
                                                "viewport": s.get("viewport"),
                                                "image_url": s.get("image_url"),
                                                "thumb_url": s.get("thumb_url"),
                                                "ok": s.get("ok"),
                                            }
                                            for s in chat_shots
                                        ],
                                    },
                                ),
                                source=source,
                                turso_session_id=turso_session_id,
                            )
                    except Exception:
                        logging.getLogger(__name__).debug(
                            "auto screenshot after UI edits failed", exc_info=True
                        )

                # Finish preview checks *before* marking the turn complete so the
                # UI never shows "Completed" / session_stopped while work continues.
                if is_website:
                    try:
                        await asyncio.wait_for(
                            _post_turn_preview_checks(
                                project_id,
                                model=model,
                                tool_context=tool_context,
                                source=source,
                                turso_session_id=turso_session_id,
                                request_id=request_id,
                                session_number=session_number,
                            ),
                            timeout=8.0,
                        )
                    except asyncio.TimeoutError:
                        logger.debug("post-turn preview checks timed out")
                    except Exception:
                        logger.debug("post-turn preview checks failed", exc_info=True)

                reply = content.strip() or "Done."
                cost_info = _estimate_turn_cost(model, turn_usage)
                await _flush_hot_deltas()
                await record_agent_event(
                    project_id, "request_completed", role="assistant", title="Done",
                    detail=reply[:4000],
                    payload=_mark_payload(
                        status="d",
                        kind="message",
                        base={
                            "reply": reply,
                            "usage": turn_usage,
                            "cost": cost_info,
                        },
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
                # Cost is only known now that generation finished — close the
                # durable request row with usage, activity volume and USD cost.
                await _finalize_request_row(
                    project_id,
                    request_id,
                    status="completed",
                    reply=reply,
                    usage=turn_usage,
                    cost=cost_info,
                    subagent_count=int(tool_context.get("_subagent_count") or 0),
                )
                if cost_info.get("cost_usd") is not None or turn_usage.get("total_tokens"):
                    await record_agent_event(
                        project_id,
                        "usage",
                        role="system",
                        title="Cost",
                        detail=str(cost_info.get("label") or ""),
                        payload=_mark_payload(
                            status="d",
                            kind="usage",
                            base={
                                "usage": turn_usage,
                                "cost": cost_info,
                            },
                        ),
                        source=source,
                        turso_session_id=turso_session_id,
                    )
                # Close the per-turn Turso UUID for pollers, but do NOT emit
                # session_stopped — that marker is reserved for real stop/interrupt
                # and was confusing clients into thinking the agent was idle while
                # background work (or the next follow-up) continued.
                if opened_turso_session:
                    await close_turso_session(turso_session_id, status="completed")
                # Background memory: summarize long sessions + notify webhooks.
                try:
                    from syte.agent_memory import maybe_summarize_session, upsert_session_meta
                    from syte.webhooks import EVENT_AGENT_SESSION_COMPLETED, emit_webhook

                    await upsert_session_meta(
                        project_id,
                        session_number,
                        turso_session_id=turso_session_id,
                        status="completed",
                        model_profile=model["profile"],
                    )
                    await maybe_summarize_session(
                        project_id,
                        session_number,
                        turso_session_id=turso_session_id,
                        min_messages=2,
                    )
                    try:
                        await _drain_turso_mirrors(timeout_s=2.0)
                        await _resync_unsynced_messages(
                            project_id,
                            session_number=session_number,
                            turso_session_id=turso_session_id,
                        )
                    except Exception:
                        logging.getLogger(__name__).debug(
                            "turso resync at turn end failed", exc_info=True
                        )
                    await emit_webhook(
                        EVENT_AGENT_SESSION_COMPLETED,
                        {
                            "project_id": project_id,
                            "turso_session_id": turso_session_id,
                            "session_number": session_number,
                            "request_id": request_id,
                            "model_profile": model["profile"],
                        },
                    )
                except Exception:
                    logging.getLogger(__name__).debug(
                        "post-turn memory/webhook failed", exc_info=True
                    )
                _write_log(project_id, f"request {request_id} completed in {step + 1} step(s)")
                return {"ok": True, "uuid": project_id, "request_id": request_id,
                        "session": session_number,
                        "turso_session_id": turso_session_id,
                        "conversation_id": f"cloud-{project_id}", "model_profile": model["profile"],
                        "thinking_level": gen.get("thinking_level"),
                        "model": model["model"], "provider": model["provider"], "message": reply,
                        "reply": reply,
                        "visual_analysis_id": (analysis_payload or {}).get("id") if analysis_payload else None,
                        "state": {"execution_status": "finished", "runtime": CLOUD_RUNTIME}}
            for call in stored_calls:
                _raise_if_cancelled()
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
                try:
                    result = await _execute_tool(
                        project_id, name, args, model=model, context=tool_context,
                    )
                except asyncio.CancelledError:
                    # Always leave a tool result so the conversation stays valid (DAV-195).
                    result = {
                        "ok": False,
                        "error": "cancelled",
                        "message": f"Tool {name} cancelled",
                    }
                    encoded = _truncate_tool_payload(result, max_chars=tool_result_chars)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": encoded,
                    })
                    # Close the started tool row so the UI never shows a
                    # permanently spinning tool call after an interrupt.
                    try:
                        await record_agent_event(
                            project_id, "tool_call_finished", title=name,
                            detail=encoded[:4000],
                            payload=_mark_payload(
                                status="d",
                                kind="tool",
                                base={"tool": name, "ok": False, "phase": "finished",
                                      "error_type": "cancelled"},
                            ),
                            source=source,
                            turso_session_id=turso_session_id,
                        )
                    except Exception:
                        logging.getLogger(__name__).debug(
                            "cancelled tool_call_finished event failed", exc_info=True
                        )
                    try:
                        await _persist_message(
                            project_id,
                            request_id,
                            "tool",
                            encoded,
                            session_number=session_number,
                            turso_session_id=turso_session_id,
                            tool_call_id=call_id,
                        )
                    except Exception:
                        logging.getLogger(__name__).debug(
                            "persist cancelled tool result failed", exc_info=True
                        )
                    raise
                except Exception as exc:
                    result = {
                        "ok": False,
                        "error": "tool_failed",
                        "message": str(exc) or type(exc).__name__,
                        "retryable": False,
                    }
                if isinstance(result, dict) and not result.get("ok", True):
                    await record_agent_event(
                        project_id,
                        "tool_error",
                        title=f"Tool {name} failed",
                        detail=str(result.get("message") or result.get("error") or "")[:2000],
                        payload=_mark_payload(
                            status="g",
                            kind="tool",
                            base={
                                "tool": name,
                                "error_type": result.get("error") or "tool_failed",
                                "retryable": bool(result.get("retryable")),
                            },
                        ),
                        source=source,
                        turso_session_id=turso_session_id,
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
                            base={
                                "plan_id": result.get("plan_id"),
                                "steps": plan_steps,
                                **_thinking_context(step=step + 1, phase="planning"),
                            },
                        ),
                        source=source,
                        turso_session_id=turso_session_id,
                    )
                if name in {"screenshot_preview", "inspect_preview"} and chat_shots:
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
                encoded = _truncate_tool_payload(public_result, max_chars=tool_result_chars)
                await _persist_message(
                    project_id, request_id, "tool", encoded,
                    session_number=session_number, turso_session_id=turso_session_id,
                    tool_call_id=call_id,
                )
                messages.append({"role": "tool", "tool_call_id": call_id, "content": encoded})
                _raise_if_cancelled()
                await asyncio.sleep(0)
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
                            # Explicit failure fields so the session failure log
                            # (brain icon, double-click) does not have to parse
                            # the encoded tool payload.
                            **(
                                {}
                                if result.get("ok")
                                else {
                                    "error": str(result.get("error") or "tool_failed"),
                                    "message": str(result.get("message") or "")[:1000],
                                    "retryable": bool(result.get("retryable")),
                                }
                            ),
                            **({"path": args["path"]} if args.get("path") else {}),
                            **({"command": args["command"]} if args.get("command") else {}),
                        },
                    ),
                    source=source,
                    turso_session_id=turso_session_id,
                )
    except asyncio.CancelledError:
        from syte.agent_artifacts import cancel_pending_questions, mark_session_stopped

        await _flush_hot_deltas()
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
        await record_agent_event(
            project_id,
            "session_stopped",
            title="Session stopped",
            detail="interrupted",
            payload=_mark_payload(
                status="d",
                kind="status",
                base={
                    "reason": "interrupted",
                    "stopped_at": stop["stopped_at"],
                    "turso_session_id": turso_session_id,
                },
            ),
            source=source,
            turso_session_id=turso_session_id,
        )
        await _finalize_request_row(
            project_id,
            request_id,
            status="cancelled",
            error="cancelled",
            usage=turn_usage,
            cost=_estimate_turn_cost(model, turn_usage),
            subagent_count=int(tool_context.get("_subagent_count") or 0),
        )
        if opened_turso_session:
            await close_turso_session(turso_session_id, status="cancelled")
        raise
    except Exception as exc:
        error = str(exc) or "Cloud agent request failed"
        _write_log(project_id, f"request {request_id} failed: {error}")
        await _flush_hot_deltas()
        await update_project(project_id, {"agent_last_error": error[:4000]})
        # Structured failure metadata so the UI can render a recoverable banner
        # (e.g. "rate limited, retry in 20s") instead of a dead-end error state.
        # ``error`` stays "cloud_agent_failed" for backwards compatibility.
        failure = _failure_metadata(exc)
        await record_agent_event(
            project_id, "request_failed", title=failure["title"], detail=error[:4000],
            payload=_mark_payload(
                status="d",
                kind="error",
                base={
                    "error": "cloud_agent_failed",
                    "error_type": failure["error_type"],
                    "retryable": failure["retryable"],
                    "retry_message": message[:4000],
                    **failure["detail"],
                },
            ),
            source=source,
            turso_session_id=turso_session_id,
        )
        await _finalize_request_row(
            project_id,
            request_id,
            status="failed",
            error=error,
            usage=turn_usage,
            cost=_estimate_turn_cost(model, turn_usage),
            subagent_count=int(tool_context.get("_subagent_count") or 0),
        )
        if opened_turso_session:
            await close_turso_session(turso_session_id, status="failed")
        return {"ok": False, "request_id": request_id, "session": session_number,
                "turso_session_id": turso_session_id,
                "error": "cloud_agent_failed", "message": error,
                "error_type": failure["error_type"],
                "retryable": failure["retryable"],
                **failure["detail"]}
    finally:
        if _active_turns.get(project_id) is current:
            _active_turns.pop(project_id, None)


async def test_agent(project_id: str, *, source: str = "api", model_profile: str | None = None) -> dict[str, Any]:
    """Provider connectivity probe that does not mutate project conversation/files (DAV-201).

    Runs a single no-tools completion against the selected model. Does not call
    ``communicate_with_agent``, so activity events, session history, and workspace
    files stay untouched.
    """
    project = await get_project(project_id)
    if not project:
        return {
            "ok": False,
            "error": "not_found",
            "message": "Project not found",
            "checks": {"cloud_runtime": False, "backend": False, "communicate": False},
        }
    try:
        if model_profile:
            profile = model_profile.strip() or DEFAULT_PROFILE
            if not await is_available_model_profile(profile):
                raise ValueError(f"Unknown model profile: {profile}")
            project = {**project, "agent_model_profile": profile}
        else:
            # Connectivity tests and preview warmups must stay cheap; Opus is code-generation only.
            project = {**project, "agent_model_profile": "syra-nano"}
        model = await selected_model_metadata(project)
        if not (model.get("api_key") or "").strip():
            raise RuntimeError(f"No API key configured for profile {model.get('profile')}")
    except Exception as exc:
        return {
            "ok": False,
            "error": "model_unavailable",
            "message": str(exc) or "Model unavailable",
            "checks": {"cloud_runtime": True, "backend": False, "communicate": False},
            "isolated": True,
            "source": source,
        }
    try:
        assistant = await _provider_completion(
            model,
            [
                {"role": "system", "content": "Reply with exactly the word ok and nothing else."},
                {"role": "user", "content": "ping"},
            ],
            tools=None,
        )
        reply = str(assistant.get("content") or "").strip()
        passed = "ok" in reply.lower()
        return {
            "ok": passed,
            "reply": reply,
            "message": (
                "Syte cloud agent test passed"
                if passed
                else "Agent did not return expected reply"
            ),
            "model_profile": model.get("profile"),
            "model": model.get("model"),
            "provider": model.get("provider"),
            "isolated": True,
            "source": source,
            "checks": {
                "cloud_runtime": True,
                "backend": passed,
                "communicate": passed,
                "isolated_probe": True,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "provider_failed",
            "message": str(exc) or "Provider probe failed",
            "isolated": True,
            "source": source,
            "checks": {"cloud_runtime": True, "backend": False, "communicate": False},
        }
