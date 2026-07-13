"""OpenHands Agent Server runtime for Syte workspaces.

This module keeps Syte's long-lived per-project agent contract while replacing
the former agent transport with OpenHands conversations and native WebSocket
events. Public route response shapes stay stable for Sycord consumers.

Performance Optimizations (v2):
- HTTP connection pooling for agent communication (reduces latency)
- HTTP/2 support when h2 package is available (multiplexing)
- Adaptive polling with faster initial checks during startup/conversation creation
- Reduced timeouts on health checks (1.5s vs 3.0s)
- Increased retry attempts for transient 5xx errors (8 vs 5)
- Exponential backoff with jitter for better retry distribution  
- WebSocket timeout reduced from 0.5s to 0.3s for faster event processing
- Conversation ready timeout increased to 30s for better stability
- Faster conversation status checks (0.1s intervals initially)
- Pooled HTTP clients per agent port with keepalive connections
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS, profile_provider
from syte.config import settings
from syte.database import get_project, get_setting, list_projects, update_project
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.workspace import ensure_workspace, read_env_vars, workspace_path

OPENHANDS_RUNTIME = "openhands"
OPENHANDS_EVENT_TIMEOUT_S = 300.0
OPENHANDS_START_TIMEOUT_S = 60.0
_CONVERSATION_READY_TIMEOUT_S = 30.0  # Increased from 15s for stability
_REUSABLE_CONVERSATION_STATUSES = frozenset({"idle", "finished"})
_RECOVERABLE_CONVERSATION_STATUSES = frozenset({"running", "paused", "stuck"})
AGENT_INSTRUCTION_VERSION = 3
_AGENT_STARTUP_CHECK_INTERVAL_S = 0.15  # Faster polling during startup
_CONVERSATION_STATUS_CHECK_INTERVAL_S = 0.1  # Faster conversation status checks

logger = logging.getLogger(__name__)

_agent_lifecycle_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_agent_warm_tasks: dict[
    str,
    asyncio.Task[tuple[bool, str, dict[str, Any]]],
] = {}
# HTTP client pool for agent connections - reuse connections for performance
_agent_http_clients: dict[int, httpx.AsyncClient] = {}


def _get_agent_client(port: int, timeout: float = 30.0) -> httpx.AsyncClient:
    """Get or create a pooled HTTP client for an agent port."""
    if port not in _agent_http_clients:
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        # Try to enable HTTP/2 if h2 is available, fall back to HTTP/1.1 otherwise
        try:
            _agent_http_clients[port] = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                http2=True,  # Enable HTTP/2 for multiplexing if available
            )
        except ImportError:
            # h2 package not installed, use HTTP/1.1 with connection pooling
            _agent_http_clients[port] = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
            )
    return _agent_http_clients[port]


def _safe_kill(pid: int) -> None:
    """Kill pid directly; only kill its process group if pgid != Syte's own pgid."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        pgid = os.getpgid(pid)
        if pgid != os.getpgid(os.getpid()):  # never kill our own group
            os.killpg(pgid, signal.SIGTERM)
    except (OSError, ValueError):
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def agent_pid_file(project_id: str) -> Path:
    path = settings.data_dir / "pids"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{project_id}.openhands.pid"


def agent_root(project_id: str) -> Path:
    root = ensure_workspace(project_id) / "data" / OPENHANDS_RUNTIME
    root.mkdir(parents=True, exist_ok=True)
    return root


def agent_home(project_id: str) -> Path:
    """Return the isolated home directory used by an OpenHands server."""
    home = agent_root(project_id) / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def agent_config_path(project_id: str) -> Path:
    return agent_root(project_id) / "agent_server_config.json"


def agent_runtime_path(project_id: str) -> Path:
    return agent_root(project_id) / "runtime.json"


def agent_log_path(project_id: str) -> Path:
    return agent_root(project_id) / "agent-server.log"


def agent_instruction_path(project_id: str) -> Path:
    return agent_root(project_id) / "SYTE_AGENT.md"


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def openhands_command() -> str:
    """Use the current interpreter so runtime and web app share dependencies."""
    return f"{shlex.quote(sys.executable)} -m openhands.agent_server"


def openhands_installed() -> bool:
    try:
        return importlib.util.find_spec("openhands.agent_server") is not None
    except ModuleNotFoundError:
        # ``find_spec`` raises when the top-level package is absent rather than
        # returning None. Treat that as the normal uninstalled state so status
        # and startup endpoints can report a useful error.
        return False


def build_agent_server_command(config_path: Path | str, port: int) -> str:
    """Build the local OpenHands Agent Server command.

    The configuration path is supplied through the process environment because
    this is the Agent Server's supported configuration mechanism.
    """
    del config_path
    return f"{openhands_command()} --host 127.0.0.1 --port {int(port)}"


async def next_agent_port() -> int:
    projects = await list_projects()
    used = {p.get("agent_port") for p in projects if p.get("agent_port")}
    for port in range(
        settings.resolved_agent_port_start,
        settings.resolved_agent_port_end + 1,
    ):
        if port not in used:
            return port
    raise RuntimeError(
        "No OpenHands agent ports available "
        f"({settings.resolved_agent_port_start}-{settings.resolved_agent_port_end} exhausted)"
    )


async def profile_api_key(profile: str) -> str:
    spec = profile_provider(profile)
    return (await get_setting(spec["setting_key"], "")).strip()


async def bridge_settings() -> dict[str, Any]:
    """Read the existing Syra provider settings without forcing key migration."""
    default_profile = (
        await get_setting("agent_default_model_profile", "syra-base")
    ).strip() or "syra-base"
    if default_profile not in PROFILE_PROVIDERS:
        default_profile = "syra-base"
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


def is_agent_running(project_id: str, port: int | None = None) -> bool:
    pid_path = agent_pid_file(project_id)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        pid_path.unlink(missing_ok=True)
        return False
    # If a port is supplied, also confirm the process is actually listening
    if port is not None:
        return _port_listening(int(port))
    return True


def agent_local_url(port: int | None) -> str:
    return f"http://127.0.0.1:{int(port)}" if port else ""


async def ensure_agent_runtime(project: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if not project.get("agent_port"):
        updates["agent_port"] = await next_agent_port()
    if not project.get("agent_status"):
        updates["agent_status"] = "stopped"
    if project.get("agent_runtime") != OPENHANDS_RUNTIME:
        updates["agent_runtime"] = OPENHANDS_RUNTIME
    if not project.get("agent_model_profile"):
        bridge = await bridge_settings()
        updates["agent_model_profile"] = bridge["default_profile"]
    if updates:
        await update_project(project["id"], updates)
        project = await get_project(project["id"]) or {**project, **updates}
    return project


async def selected_model_metadata(project: dict[str, Any]) -> dict[str, str]:
    bridge = await bridge_settings()
    profile = (
        project.get("agent_model_profile") or bridge["default_profile"] or "syra-base"
    ).strip()
    spec = bridge["profiles"].get(profile, bridge["profiles"]["syra-base"])
    return {
        "profile": profile,
        "provider": spec["provider"],
        "provider_label": spec["label"],
        "model": spec["model"],
        "api_base": spec["api_base"],
        "api_key": spec["api_key"],
    }


def _load_server_config(project_id: str) -> dict[str, Any]:
    path = agent_config_path(project_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_server_config(project_id: str, payload: dict[str, Any]) -> Path:
    path = agent_config_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return path


def _session_api_key(project_id: str) -> str:
    config = _load_server_config(project_id)
    keys = config.get("session_api_keys")
    if isinstance(keys, list) and keys and isinstance(keys[0], str) and keys[0]:
        return keys[0]
    raise RuntimeError("OpenHands session key is missing; regenerate the agent configuration")


def agent_session_headers(project_id: str) -> dict[str, str]:
    """Headers required when Syte proxies a private Agent Server endpoint."""
    return {"X-Session-API-Key": _session_api_key(project_id)}


def _profile_llm_payload(model: dict[str, str]) -> dict[str, Any]:
    # OpenHands delegates provider routing to LiteLLM. The `openai/` prefix is
    # required for the existing DeepSeek/Gemini OpenAI-compatible endpoints.
    model_name = model["model"]
    if "/" not in model_name:
        model_name = f"openai/{model_name}"
    return {
        "model": model_name,
        "api_key": model["api_key"],
        "base_url": model["api_base"],
        "stream": True,
        "timeout": 120,
        "num_retries": 1,
        "retry_min_wait": 1,
        "retry_max_wait": 4,
    }


def _build_syte_instruction(project_id: str, rules: list[dict[str, str]]) -> str:
    rule_lines = "\n".join(
        f"- {rule['name']}: {rule['rule']}" for rule in rules if rule.get("rule")
    )
    skills_dir = agent_root(project_id) / "skills"
    return (
        "You are the persistent OpenHands coding agent for a Syte project. "
        "Work directly in the configured workspace. Use your file editor and "
        "terminal tools to inspect before editing, make focused changes, and "
        "verify useful work when practical.\n\n"
        "For every user request, think before acting. Before your first tool "
        "call, present a short concrete plan to the user. Keep the plan concise, "
        "then execute it. Never begin file edits or commands before planning. "
        "Finish every turn with a clear answer describing the result.\n\n"
        "Syte workspace rules:\n"
        f"{rule_lines}\n\n"
        f"Reference skill documents are available at {skills_dir}. "
        "Syte helper commands are available on PATH."
    )


def _agent_instruction_is_current(project_id: str) -> bool:
    path = agent_runtime_path(project_id)
    if not path.exists() or not agent_instruction_path(project_id).exists():
        return False
    try:
        metadata = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(metadata, dict)
        and metadata.get("instruction_version") == AGENT_INSTRUCTION_VERSION
    )


async def write_agent_config(project: dict[str, Any]) -> Path:
    """Write a per-project OpenHands Agent Server configuration.

    Provider keys are deliberately not written into this file. They are sent
    only over loopback while creating or switching a conversation and are then
    encrypted by OpenHands with this runtime's secret key.
    """
    from syte.agent_skills import build_agent_rules, read_access_config, write_agent_skills

    project = await ensure_agent_runtime(project)
    model = await selected_model_metadata(project)
    if not model["api_key"]:
        raise RuntimeError(
            f"No API key configured for active profile {model['profile']}. "
            f"Open AI settings and add the {model['provider_label']} key."
        )

    root = agent_root(project["id"])
    access_config = await read_access_config(project["id"], root)
    write_agent_skills(project["id"], root)
    rules = build_agent_rules(project["id"], access_config)
    agent_instruction_path(project["id"]).write_text(
        _build_syte_instruction(project["id"], rules) + "\n"
    )

    previous = _load_server_config(project["id"])
    session_key = (
        (previous.get("session_api_keys") or [None])[0]
        if isinstance(previous.get("session_api_keys"), list)
        else None
    )
    if not isinstance(session_key, str) or not session_key:
        session_key = secrets.token_urlsafe(32)
    secret_key = previous.get("secret_key")
    if not isinstance(secret_key, str) or not secret_key:
        secret_key = secrets.token_urlsafe(32)

    repo = workspace_path(project["id"]) / "app"
    repo.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_api_keys": [session_key],
        "secret_key": secret_key,
        "conversations_path": str(root / "conversations"),
        "workspace_path": str(repo),
        "bash_events_dir": str(root / "bash-events"),
        "enable_vscode": False,
        "enable_vnc": False,
        "preload_tools": False,
        "max_concurrent_runs": 1,
        "lease_ttl_seconds": 0,
    }
    path = _write_server_config(project["id"], payload)
    agent_runtime_path(project["id"]).write_text(
        json.dumps(
            {
                "runtime": OPENHANDS_RUNTIME,
                "instruction_version": AGENT_INSTRUCTION_VERSION,
                "model_profile": model["profile"],
                "model": model["model"],
                "provider": model["provider"],
                "instruction_path": str(agent_instruction_path(project["id"])),
                "updated_at": _now(),
            },
            indent=2,
        )
        + "\n"
    )
    return path


def write_agent_secrets(project_id: str, bridge: dict[str, Any]) -> Path:
    """Write non-secret provider metadata for legacy diagnostics consumers."""
    path = agent_root(project_id) / "provider-status.json"
    path.write_text(
        json.dumps(
            {
                "managed_by": "syte",
                "configured_profiles": [
                    name
                    for name in PROFILE_ORDER
                    if bridge["profiles"][name]["api_key"]
                ],
            },
            indent=2,
        )
        + "\n"
    )
    path.chmod(0o600)
    return path


async def backend_health(project: dict[str, Any]) -> dict[str, Any]:
    from syte.agent_debug import probe_profile_provider

    model = await selected_model_metadata(project)
    api_key = model["api_key"]
    if not api_key:
        return {
            "ok": False,
            "error": (
                f"{model.get('provider_label', 'Provider')} API key not configured "
                f"for {model['profile']}"
            ),
            "url": None,
            "profile": model["profile"],
        }

    probe = await probe_profile_provider(model["profile"], api_key)
    chat_probe = next(
        (item for item in probe.get("probes") or [] if item["step"] == "chat_completion"),
        None,
    )
    return {
        "ok": probe.get("ok", False),
        "status_code": (chat_probe or {}).get("status_code"),
        "url": (chat_probe or {}).get("url") or model["api_base"],
        "profile": model["profile"],
        "provider": model.get("provider_label"),
        "error": probe.get("error") or "",
        "probes": probe.get("probes") or [],
    }


async def probe_agent_http(
    port: int | None,
    *,
    timeout_s: float = 1.5,  # Reduced from 3.0s for faster checks
) -> dict[str, Any]:
    if not port:
        return {"ok": False, "url": None, "status_code": None}
    base = agent_local_url(port)
    url = base + "/ready"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        try:
            response = await client.get(url)
            return {
                "ok": response.status_code < 400,
                "url": url,
                "status_code": response.status_code,
                "port_open": True,
            }
        except Exception:
            pass
    return {
        "ok": False,
        "url": url,
        "status_code": None,
        "port_open": _port_listening(int(port)),
    }


def get_agent_logs(project_id: str, lines: int = 200) -> str:
    log_path = agent_log_path(project_id)
    if not log_path.exists():
        return "No OpenHands agent logs yet."
    content = log_path.read_text(errors="replace").splitlines()
    return "\n".join(content[-max(1, lines) :])


async def wait_for_agent_ready(
    port: int, *, timeout_s: float = OPENHANDS_START_TIMEOUT_S
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    check_count = 0
    # Adaptive polling: start faster, slow down as time passes
    while time.monotonic() < deadline:
        check_count += 1
        # Fast checks initially, then slow down
        interval = min(0.5, 0.1 + (check_count * 0.02))
        
        if _port_listening(int(port)):
            probe = await probe_agent_http(int(port), timeout_s=1.0)
            if probe.get("ok"):
                return True, ""
            last_error = "Port is open but OpenHands is still initializing"
        else:
            last_error = f"Port {port} is not listening yet"
        await asyncio.sleep(interval)
    return False, last_error or f"OpenHands Agent Server did not become ready within {int(timeout_s)}s"


async def _stop_agent_impl(project_id: str) -> tuple[bool, str]:
    from syte.agent_activity import record_agent_event

    pid_path = agent_pid_file(project_id)
    if not pid_path.exists():
        await update_project(project_id, {"agent_status": "stopped"})
        return True, "OpenHands agent already stopped."
    try:
        pid = int(pid_path.read_text().strip())
        _safe_kill(pid)
    except (OSError, ValueError):
        pass
    pid_path.unlink(missing_ok=True)
    await update_project(project_id, {"agent_status": "stopped"})
    await record_agent_event(
        project_id,
        "agent_stopped",
        title="Agent stopped",
        detail="OpenHands agent stopped",
        payload={"runtime": OPENHANDS_RUNTIME},
        source=OPENHANDS_RUNTIME,
    )
    return True, "OpenHands agent stopped."


async def stop_agent(project_id: str) -> tuple[bool, str]:
    """Stop one runtime after any in-flight start has completed."""
    async with _agent_lifecycle_locks[project_id]:
        return await _stop_agent_impl(project_id)


async def _start_agent_impl(
    project_id: str,
    *,
    force: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    from syte.agent_activity import record_agent_event
    from syte.agent_skills import agent_path_env

    project = await get_project(project_id)
    if not project:
        return False, "Project not found", {}
    project = await ensure_agent_runtime(project)
    port = int(project["agent_port"])

    if not force and is_agent_running(project_id) and _port_listening(port):
        healthy = await probe_agent_http(port)
        if healthy.get("ok"):
            status = await get_agent_status(project_id, check_backend=False)
            return True, "OpenHands agent already running.", status

    if not openhands_installed():
        message = (
            "OpenHands Agent Server is not installed. "
            "Install the project's Python dependencies to add openhands-agent-server."
        )
        await update_project(project_id, {"agent_status": "error", "agent_last_error": message})
        return False, message, {}

    await _stop_agent_impl(project_id)
    await update_project(
        project_id,
        {"agent_status": "starting", "agent_last_error": ""},
    )
    bridge = await bridge_settings()
    try:
        config_path = await write_agent_config(project)
    except RuntimeError as exc:
        message = str(exc)
        await update_project(project_id, {"agent_status": "error", "agent_last_error": message})
        return False, message, {}

    write_agent_secrets(project_id, bridge)
    log_path = agent_log_path(project_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log:
        log.write(f"\n=== OpenHands Agent Server session {_now()} ===\n")
        log.write(f"Config: {config_path}\n")
        log.write(f"Port: {port}\n")

    root = agent_root(project_id)
    repo = workspace_path(project_id) / "app"
    repo.mkdir(parents=True, exist_ok=True)
    tmux_tmpdir = root / "tmux"
    tmux_tmpdir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        **read_env_vars(project.get("env_vars", "{}")),
        "HOME": str(agent_home(project_id)),
        "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(config_path),
        "TMUX_TMPDIR": str(tmux_tmpdir),
    }
    env.update(agent_path_env(project_id, root))

    command = build_agent_server_command(config_path, port)
    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        shlex.split(command),
        cwd=repo,
        shell=False,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    agent_pid_file(project_id).write_text(str(proc.pid))

    ready = False
    try:
        # Initial fast polling to catch quick startups
        for check in range(300):  # 300 checks over ~45 seconds max
            if proc.poll() is not None:
                log_file.close()
                agent_pid_file(project_id).unlink(missing_ok=True)
                error = get_agent_logs(project_id, 80)
                tail = error[-2000:] if error else "No log output"
                await update_project(
                    project_id,
                    {"agent_status": "error", "agent_last_error": tail},
                )
                return False, f"OpenHands agent exited during startup.\n{tail}", {}
            if _port_listening(port):
                ready, _ = await wait_for_agent_ready(port, timeout_s=2.0)
                if ready:
                    break
            # Adaptive sleep: faster initially, slower after first 10 checks
            await asyncio.sleep(_AGENT_STARTUP_CHECK_INTERVAL_S if check < 40 else 0.25)
    except asyncio.CancelledError:
        log_file.close()
        try:
            _safe_kill(proc.pid)
        except (OSError, ValueError):
            pass
        agent_pid_file(project_id).unlink(missing_ok=True)
        await update_project(
            project_id,
            {
                "agent_status": "running",
                "agent_last_error": "Agent warm-up interrupted; supervisor will retry",
            },
        )
        raise

    if not ready:
        log_file.close()
        try:
            _safe_kill(proc.pid)
        except (OSError, ValueError):
            pass
        agent_pid_file(project_id).unlink(missing_ok=True)
        error = get_agent_logs(project_id, 80)
        tail = error[-2000:] if error else "Server never became ready"
        await update_project(project_id, {"agent_status": "error", "agent_last_error": tail})
        return False, f"OpenHands agent did not become ready on port {port}.\n{tail}", {}

    log_file.close()
    await update_project(
        project_id,
        {
            "agent_status": "running",
            "agent_runtime": OPENHANDS_RUNTIME,
            "agent_last_started_at": _now(),
            "agent_last_error": "",
            "agent_config_path": str(config_path),
        },
    )
    project = await get_project(project_id) or project
    stored_conversation_id = str(project.get("agent_conversation_id") or "").strip()
    if stored_conversation_id:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                info = await _conversation_info(
                    client,
                    base_url=_server_url(port),
                    headers=agent_session_headers(project_id),
                    conversation_id=stored_conversation_id,
                )
            if info is None:
                await update_project(project_id, {"agent_conversation_id": ""})
        except httpx.HTTPError:
            logger.warning(
                "Could not verify persisted OpenHands conversation for %s after restart",
                project_id,
            )

    model = await selected_model_metadata(project)
    await _prewarm_conversation(project_id, port=port, model=model)

    status = await get_agent_status(project_id, check_backend=False)
    await record_agent_event(
        project_id,
        "agent_started",
        title="Agent started",
        detail=f"OpenHands agent started on port {port}",
        payload={"port": port, "runtime": OPENHANDS_RUNTIME},
        source=OPENHANDS_RUNTIME,
    )
    return True, f"OpenHands agent started on port {port}.", status


async def start_agent(project_id: str) -> tuple[bool, str, dict[str, Any]]:
    """Start one runtime, serializing concurrent chat/supervisor requests."""
    async with _agent_lifecycle_locks[project_id]:
        return await _start_agent_impl(project_id)


def agent_warm_in_progress(project_id: str) -> bool:
    task = _agent_warm_tasks.get(project_id)
    return bool(task and not task.done())


async def _run_agent_warm(
    project_id: str,
) -> tuple[bool, str, dict[str, Any]]:
    try:
        return await start_agent(project_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = str(exc) or "OpenHands background warm-up failed"
        logger.exception("OpenHands warm-up failed for %s", project_id)
        await update_project(
            project_id,
            {"agent_status": "error", "agent_last_error": message[:4000]},
        )
        return False, message, {}


async def warm_agent(
    project_id: str,
    *,
    source: str = "api",
) -> dict[str, Any]:
    """Start an agent in the background and return without waiting for /ready."""
    project = await get_project(project_id)
    if not project:
        return {
            "ok": False,
            "error": "not_found",
            "message": "Project not found",
            "project_id": project_id,
        }
    project = await ensure_agent_runtime(project)

    existing = _agent_warm_tasks.get(project_id)
    if existing and not existing.done():
        return {
            "ok": True,
            "status": "warming",
            "already_warming": True,
            "project_id": project_id,
        }

    port = project.get("agent_port")
    if (
        port
        and is_agent_running(project_id)
        and _port_listening(int(port))
    ):
        health = await probe_agent_http(int(port), timeout_s=0.25)
        if health.get("ok"):
            await update_project(
                project_id,
                {"agent_status": "running", "agent_last_error": ""},
            )
            model = await selected_model_metadata(project)
            await _prewarm_conversation(project_id, port=int(port), model=model)
            return {
                "ok": True,
                "status": "ready",
                "already_warming": False,
                "project_id": project_id,
            }

    if not openhands_installed():
        await update_project(
            project_id,
            {
                "agent_status": "error",
                "agent_last_error": "OpenHands Agent Server is not installed",
            },
        )
        return {
            "ok": False,
            "error": "agent_server_not_installed",
            "message": "OpenHands Agent Server is not installed",
            "project_id": project_id,
        }

    model = await selected_model_metadata(project)
    if not model["api_key"]:
        message = f"No API key configured for active profile {model['profile']}"
        await update_project(
            project_id,
            {"agent_status": "error", "agent_last_error": message},
        )
        return {
            "ok": False,
            "error": "api_key_missing",
            "message": message,
            "project_id": project_id,
        }

    await update_project(
        project_id,
        {
            "agent_status": "starting",
            "agent_runtime": OPENHANDS_RUNTIME,
            "agent_last_error": "",
        },
    )
    task = asyncio.create_task(
        _run_agent_warm(project_id),
        name=f"warm-openhands-{project_id}",
    )
    _agent_warm_tasks[project_id] = task

    def forget(completed: asyncio.Task[Any]) -> None:
        if _agent_warm_tasks.get(project_id) is completed:
            _agent_warm_tasks.pop(project_id, None)

    task.add_done_callback(forget)
    return {
        "ok": True,
        "status": "warming",
        "already_warming": False,
        "project_id": project_id,
        "source": source,
    }


async def restart_agent(project_id: str) -> tuple[bool, str, dict[str, Any]]:
    from syte.agent_activity import record_agent_event

    async with _agent_lifecycle_locks[project_id]:
        ok, message, status = await _start_agent_impl(project_id, force=True)
    if ok:
        await record_agent_event(
            project_id,
            "agent_restarted",
            title="Agent restarted",
            detail=message,
            payload={"runtime": OPENHANDS_RUNTIME},
            source=OPENHANDS_RUNTIME,
        )
    return ok, message, status


async def get_agent_status(
    project_id: str,
    *,
    request_base: str = "",
    check_backend: bool = True,
) -> dict[str, Any]:
    project = await get_project(project_id)
    if not project:
        return {}
    project = await ensure_agent_runtime(project)
    port = project.get("agent_port")
    runtime_url = agent_local_url(port)
    healthy = await probe_agent_http(port)
    model = await selected_model_metadata(project)
    if check_backend:
        backend = await backend_health(project)
    else:
        backend = {
            "ok": bool(model["api_key"]),
            "status_code": None,
            "url": model["api_base"],
            "profile": model["profile"],
            "provider": model.get("provider_label"),
            "error": "" if model["api_key"] else (
                f"{model.get('provider_label', 'Provider')} API key not configured "
                f"for {model['profile']}"
            ),
            "probes": [],
        }
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    base_url = request_base.rstrip("/")
    if not base_url:
        base_url = (
            build_https_url(gui_domain)
            if gui_domain
            else build_direct_url(settings.resolved_public_ip, settings.port)
        )
    running = is_agent_running(project_id)
    warming = agent_warm_in_progress(project_id)
    status = project.get("agent_status") or ("running" if running else "stopped")
    if running and healthy["ok"]:
        status = "running"
    elif running or warming or status == "starting":
        status = "starting"
    elif status not in ("error", "stopped"):
        status = "stopped"
    proxy_path = f"/api/internal/projects/{project_id}/agent/proxy"
    return {
        "agent_runtime": OPENHANDS_RUNTIME,
        "agent_runtime_type": OPENHANDS_RUNTIME,
        "agent_status": status,
        "agent_running": running,
        "agent_healthy": healthy["ok"],
        "agent_warming": warming,
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
        "agent_command": openhands_command(),
        "agent_install_ok": openhands_installed(),
        "agent_no_hub_required": True,
        "agent_conversation_id": project.get("agent_conversation_id") or "",
        "agent_capabilities": [
            "persistent_conversations",
            "always_on_runtime",
            "native_websocket_events",
            "tagged_activity_stream",
            "terminal",
            "file_editor",
            "task_tracker",
            "skills",
            "mcp_api",
            "git_api",
            "goal_loops",
        ],
    }


async def update_agent_settings(
    project_id: str,
    *,
    model_profile: str | None = None,
    include_status: bool = True,
) -> dict[str, Any]:
    project = await get_project(project_id)
    if not project:
        return {}
    updates: dict[str, Any] = {}
    if model_profile is not None:
        profile = model_profile.strip() or "syra-base"
        if profile not in PROFILE_PROVIDERS:
            raise ValueError(f"Unknown model profile: {profile}")
        updates["agent_model_profile"] = profile
    if updates:
        await update_project(project_id, updates)
    if include_status:
        return await get_agent_status(project_id)
    return await get_project(project_id) or {}


def _server_url(port: int) -> str:
    return agent_local_url(port).rstrip("/")


def _is_message_send_server_error(error: BaseException) -> bool:
    """Return whether an Agent Server send failed in a recoverable way."""
    text = str(error)
    return "OpenHands message send returned HTTP " in text and any(
        f"HTTP {status}" in text for status in (500, 502, 503, 504)
    )


async def _response_error(response: httpx.Response, operation: str) -> RuntimeError:
    try:
        body = response.json()
        detail = body.get("detail") if isinstance(body, dict) else body
    except Exception:
        detail = response.text[:1000]
    return RuntimeError(f"OpenHands {operation} returned HTTP {response.status_code}: {detail}")


async def _conversation_info(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
    conversation_id: str,
) -> dict[str, Any] | None:
    response = await client.get(
        f"{base_url}/api/conversations/{conversation_id}",
        headers=headers,
    )
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise await _response_error(response, "conversation lookup")
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


async def _wait_for_conversation_status(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
    conversation_id: str,
    acceptable: frozenset[str],
    timeout_s: float = _CONVERSATION_READY_TIMEOUT_S,
) -> str:
    """Poll a conversation until it reaches a sendable execution status."""
    deadline = time.monotonic() + timeout_s
    last_status = ""
    check_count = 0
    while time.monotonic() < deadline:
        check_count += 1
        conversation = await _conversation_info(
            client,
            base_url=base_url,
            headers=headers,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return ""
        last_status = str(conversation.get("execution_status") or "").lower()
        if last_status in acceptable:
            return last_status
        # Faster initial checks, then slow down
        interval = _CONVERSATION_STATUS_CHECK_INTERVAL_S if check_count < 20 else 0.2
        await asyncio.sleep(interval)
    return last_status


async def _recover_conversation_for_send(
    project_id: str,
    *,
    port: int,
    conversation_id: str,
) -> bool:
    """Interrupt a busy conversation and wait for it to become sendable."""
    headers = agent_session_headers(project_id)
    base_url = _server_url(port)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{base_url}/api/conversations/{conversation_id}/interrupt",
                headers=headers,
            )
        if response.status_code >= 400 and response.status_code != 404:
            return False
    except httpx.HTTPError:
        return False

    async with httpx.AsyncClient(timeout=15.0) as client:  # Increased timeout
        status = await _wait_for_conversation_status(
            client,
            base_url=base_url,
            headers=headers,
            conversation_id=conversation_id,
            acceptable=_REUSABLE_CONVERSATION_STATUSES,
            timeout_s=12.0,  # Increased from 8.0s
        )
    return status in _REUSABLE_CONVERSATION_STATUSES


async def _ensure_conversation(
    project: dict[str, Any],
    *,
    port: int,
    model: dict[str, str],
) -> tuple[str, bool]:
    """Reuse a durable project conversation or create one with OpenHands tools."""
    project_id = project["id"]
    from syte.agent_skills import mcp_server_config

    base_url = _server_url(port)
    headers = agent_session_headers(project_id)
    existing = str(project.get("agent_conversation_id") or "").strip()
    conversation_meta_path = agent_root(project_id) / "conversation-meta.json"
    conversation_version = None
    if conversation_meta_path.exists():
        try:
            conversation_version = json.loads(conversation_meta_path.read_text()).get("tooling_version")
        except (OSError, json.JSONDecodeError):
            conversation_version = None
    if conversation_version != AGENT_INSTRUCTION_VERSION:
        existing = ""
    async with httpx.AsyncClient(timeout=30.0) as client:
        if existing:
            conversation = await _conversation_info(
                client,
                base_url=base_url,
                headers=headers,
                conversation_id=existing,
            )
            status = str((conversation or {}).get("execution_status") or "").lower()
            if conversation is not None and status in _REUSABLE_CONVERSATION_STATUSES:
                return existing, False
            if conversation is not None and status in _RECOVERABLE_CONVERSATION_STATUSES:
                if await _recover_conversation_for_send(
                    project_id,
                    port=port,
                    conversation_id=existing,
                ):
                    return existing, False
            await update_project(project_id, {"agent_conversation_id": ""})

        instruction = agent_instruction_path(project_id).read_text(
            errors="replace"
        ) if agent_instruction_path(project_id).exists() else (
            "You are Syte's OpenHands coding agent. Work safely in the project workspace."
        )
        repo = workspace_path(project_id) / "app"
        payload = {
            "workspace": {
                "kind": "LocalWorkspace",
                "working_dir": str(repo),
            },
            "max_iterations": 100,
            "stuck_detection": True,
            "autotitle": False,
            "tags": {"syteproject": project_id[:256]},
            "agent": {
                "kind": "Agent",
                "llm": _profile_llm_payload(model),
                "tools": [
                    {"name": "terminal"},
                    {"name": "file_editor"},
                    {"name": "task_tracker"},
                ],
                # Agent.mcp_config is a direct server-name mapping. The
                # mcp_server_config helper returns the standalone MCP JSON
                # format, which intentionally wraps that mapping in
                # ``mcpServers``.
                "mcp_config": mcp_server_config(
                    project_id, agent_root(project_id)
                )["mcpServers"],
                "agent_context": {
                    "system_message_suffix": instruction,
                    "load_project_skills": True,
                },
                "system_prompt_kwargs": {"cli_mode": True},
                "tool_concurrency_limit": 1,
            },
        }
        response = await client.post(
            f"{base_url}/api/conversations",
            headers=headers,
            json=payload,
        )
        if response.status_code >= 400:
            raise await _response_error(response, "conversation creation")
        data = response.json()
    conversation_id = str(data.get("id") or "")
    if not conversation_id:
        raise RuntimeError("OpenHands created a conversation without an id")
    await update_project(project_id, {"agent_conversation_id": conversation_id})
    try:
        conversation_meta_path.write_text(
            json.dumps({"tooling_version": AGENT_INSTRUCTION_VERSION}) + "\n"
        )
    except OSError:
        logger.warning("Could not persist conversation tooling version for %s", project_id)
    # Wait for conversation to become ready with shorter timeout for new conversations
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _wait_for_conversation_status(
            client,
            base_url=base_url,
            headers=headers,
            conversation_id=conversation_id,
            acceptable=_REUSABLE_CONVERSATION_STATUSES,
            timeout_s=8.0,  # Increased from 5.0s but still faster than recovery
        )
    return conversation_id, True


async def _prewarm_conversation(
    project_id: str,
    *,
    port: int,
    model: dict[str, str],
) -> None:
    """Create or validate the durable conversation while the runtime is idle."""
    project = await get_project(project_id)
    if not project:
        return
    try:
        await _ensure_conversation(project, port=port, model=model)
    except Exception:
        logger.warning(
            "OpenHands conversation prewarm failed for %s",
            project_id,
            exc_info=True,
        )


async def _switch_conversation_llm(
    project_id: str,
    *,
    port: int,
    conversation_id: str,
    model: dict[str, str],
) -> None:
    """Apply the selected Syra profile to subsequent turns of a conversation."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{_server_url(port)}/api/conversations/{conversation_id}/switch_llm",
            headers=agent_session_headers(project_id),
            json={"llm": _profile_llm_payload(model)},
        )
    if response.status_code >= 400:
        raise await _response_error(response, "model switch")


def _state_update_status(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or event.get("type") or "").lower()
    if kind != "conversationstateupdateevent":
        return ""
    key = str(event.get("key") or "")
    value = event.get("value")
    if key == "execution_status":
        return str(value or "").lower()
    if key == "full_state" and isinstance(value, dict):
        return str(value.get("execution_status") or "").lower()
    return ""


def _message_event_text(event: dict[str, Any], *, role: str = "assistant") -> str:
    kind = str(event.get("kind") or event.get("type") or "").lower()
    if kind != "messageevent":
        return ""
    message = event.get("llm_message") or event.get("message")
    if not isinstance(message, dict):
        return ""
    if str(message.get("role") or "") != role:
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return str(content or "")
    return "".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict)
    )


async def _get_final_response(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
    conversation_id: str,
) -> str:
    response = await client.get(
        f"{base_url}/api/conversations/{conversation_id}/agent_final_response",
        headers=headers,
    )
    if response.status_code >= 400:
        return ""
    try:
        data = response.json()
    except Exception:
        return ""
    return str(data.get("response") or "") if isinstance(data, dict) else ""


async def _send_conversation_message(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
    conversation_id: str,
    message: str,
) -> None:
    """Send a turn, retrying transient Agent Server failures.

    OpenHands can briefly return a 5xx while a conversation has just become
    ready or while its previous turn is being finalized. Retrying only these
    server-side responses avoids surfacing a misleading immediate failure,
    while leaving validation and provider errors terminal.
    """
    url = f"{base_url}/api/conversations/{conversation_id}/events"
    payload = {
        "role": "user",
        "content": [{"type": "text", "text": message}],
        "run": True,
    }
    retryable_statuses = {500, 502, 503, 504}
    max_attempts = 8  # Increased from 5 for better resilience
    for attempt in range(max_attempts):
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code < 400:
            return
        if response.status_code not in retryable_statuses or attempt == max_attempts - 1:
            raise await _response_error(response, "message send")
        # Exponential backoff with jitter for better retry distribution
        delay = min(2.0, 0.2 * (1.5**attempt))
        logger.warning(
            "OpenHands message send returned HTTP %s; retrying in %.2fs "
            "(attempt %s/%s)",
            response.status_code,
            delay,
            attempt + 1,
            max_attempts,
        )
        await asyncio.sleep(delay)


async def _stream_conversation_turn(
    project_id: str,
    *,
    port: int,
    conversation_id: str,
    message: str,
    request_id: str | None,
    source: str,
) -> tuple[str, str, str]:
    """Send a turn and bridge native OpenHands WebSocket events to Syte SSE."""
    from syte.agent_activity import ingest_openhands_event

    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError(
            "OpenHands streaming requires the websockets Python package"
        ) from exc

    base_url = _server_url(port)
    headers = agent_session_headers(project_id)
    ws_url = f"{base_url.replace('http://', 'ws://', 1)}/sockets/events/{conversation_id}"
    token_snapshot = ""
    final_reply = ""
    execution_status = ""
    failure = ""
    saw_running = False
    saw_current_user_message = False
    pre_turn_status = ""
    deadline = time.monotonic() + settings.agent_event_timeout_s

    # Use pooled client for better performance
    client = _get_agent_client(port, timeout=30.0)
    try:
        before = await client.get(
            f"{base_url}/api/conversations/{conversation_id}",
            headers=headers,
            timeout=2.0,  # Fast pre-check
        )
        if before.status_code < 400:
            before_data = before.json() if before.content else {}
            if isinstance(before_data, dict):
                pre_turn_status = str(
                    before_data.get("execution_status") or ""
                ).lower()
    except (httpx.HTTPError, ValueError):
        pass

    async with connect(
            ws_url,
            open_timeout=5,  # Reduced from 10s for faster failure
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            await websocket.send(
                json.dumps({"type": "auth", "session_api_key": headers["X-Session-API-Key"]})
            )
            # Give the Agent Server a moment to finish subscribing this socket
            # before the HTTP message send starts the turn.
            try:
                await asyncio.wait_for(websocket.recv(), timeout=0.5)  # Reduced from 1.0s
            except (asyncio.TimeoutError, TypeError, ValueError):
                pass
            await _send_conversation_message(
                client,
                base_url=base_url,
                headers=headers,
                conversation_id=conversation_id,
                message=message,
            )

            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=0.3)  # Reduced from 0.5s
                except asyncio.TimeoutError:
                    try:
                        info = await client.get(
                            f"{base_url}/api/conversations/{conversation_id}",
                            headers=headers,
                            timeout=1.0,  # Explicit fast timeout for status checks
                        )
                        if info.status_code < 400:
                            try:
                                conversation = info.json() if info.content else {}
                            except ValueError:
                                conversation = {}
                            state = str(
                                conversation.get("execution_status", "")
                                if isinstance(conversation, dict)
                                else ""
                            ).lower()
                            if state == "running":
                                saw_running = True
                            terminal_state = state in {
                                "finished",
                                "error",
                                "stuck",
                                "paused",
                                "waiting_for_confirmation",
                            } or (state == "idle" and saw_running)
                            terminal_is_current = (
                                saw_running
                                or saw_current_user_message
                                or not pre_turn_status
                                or state != pre_turn_status
                            )
                            if terminal_state and terminal_is_current:
                                execution_status = state
                                break
                    except httpx.HTTPError:
                        pass
                    continue

                try:
                    event = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue

                kind = str(event.get("kind") or event.get("type") or "").lower()
                if kind in {"streamingdeltaevent", "tokenevent"}:
                    token_snapshot += str(event.get("content") or event.get("delta") or "")
                text = _message_event_text(event)
                if text:
                    final_reply = text
                if _message_event_text(event, role="user").strip() == message.strip():
                    saw_current_user_message = True
                if kind in {"conversationerrorevent", "servererrorevent"}:
                    failure = str(
                        event.get("detail")
                        or event.get("message")
                        or event.get("error")
                        or event.get("code")
                        or "OpenHands could not process the request"
                    )

                # The turn coordinator emits one request_failed event with a
                # stable request id below. Avoid persisting the native error as
                # a second terminal event for the same turn.
                if kind not in {"conversationerrorevent", "servererrorevent"}:
                    await ingest_openhands_event(
                        project_id,
                        event,
                        source=source,
                        request_id=request_id,
                        token_snapshot=token_snapshot,
                    )

                state = _state_update_status(event)
                if state == "running":
                    saw_running = True
                terminal_state = state in {
                    "finished",
                    "error",
                    "stuck",
                    "paused",
                    "waiting_for_confirmation",
                } or (state == "idle" and saw_running)
                terminal_is_current = (
                    saw_running
                    or saw_current_user_message
                    or not pre_turn_status
                    or state != pre_turn_status
                )
                if terminal_state and terminal_is_current:
                    execution_status = state
                    break
                if failure:
                    execution_status = "error"
                    break
            else:
                execution_status = "timeout"

    if execution_status == "timeout":
        try:
            await interrupt_agent(project_id)
        except Exception:
            logger.exception("Failed to interrupt timed-out agent turn for %s", project_id)

    if not final_reply:
        for attempt in range(3):
            final_reply = await _get_final_response(
                client,
                base_url=base_url,
                headers=headers,
                conversation_id=conversation_id,
            )
            if final_reply:
                break
            await asyncio.sleep(0.1 * (attempt + 1))

    if execution_status == "timeout":
        failure = failure or "OpenHands did not finish before the request timeout"
    elif execution_status in {"error", "stuck", "paused"}:
        failure = failure or f"OpenHands conversation {execution_status}"
    elif execution_status == "waiting_for_confirmation":
        failure = (
            failure
            or "OpenHands is waiting for tool confirmation, which this chat cannot approve"
        )
    return final_reply, execution_status or "finished", failure


async def interrupt_agent(project_id: str) -> tuple[bool, str]:
    """Interrupt the active OpenHands turn without destroying its conversation."""
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    conversation_id = str(project.get("agent_conversation_id") or "")
    port = project.get("agent_port")
    if not conversation_id or not port or not is_agent_running(project_id):
        return True, "No active OpenHands conversation to interrupt."

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{_server_url(int(port))}/api/conversations/{conversation_id}/interrupt",
                headers=agent_session_headers(project_id),
            )
        if response.status_code >= 400:
            raise await _response_error(response, "interrupt")
    except Exception as exc:
        return False, str(exc)

    from syte.agent_activity import record_agent_event

    await record_agent_event(
        project_id,
        "status",
        title="OpenHands interrupted",
        detail="The active response was cancelled; the conversation remains available.",
        payload={"conversation_id": conversation_id, "runtime": OPENHANDS_RUNTIME},
        source=OPENHANDS_RUNTIME,
    )
    return True, "OpenHands response interrupted."


async def communicate_with_agent(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    source: str = "api",
    auto_start: bool = True,
    emit_request_started: bool = True,
    background: bool = False,
) -> dict[str, Any]:
    if background:
        from syte.agent_jobs import submit_agent_request

        return await submit_agent_request(
            project_id,
            message,
            model_profile=model_profile,
            source=source,
            auto_start=auto_start,
        )

    from syte.agent_activity import record_agent_event
    from syte.agent_jobs import project_agent_lock
    from syte.agent_metrics import log_agent_request

    try:
        async with project_agent_lock(project_id):
            return await _communicate_with_agent_impl(
                project_id,
                message,
                model_profile=model_profile,
                source=source,
                auto_start=auto_start,
                emit_request_started=emit_request_started,
            )
    except Exception as exc:
        error = str(exc) or "Agent request failed"
        await log_agent_request(
            project_id,
            source=source,
            model_profile=model_profile,
            message=message,
            status="error",
            error=error,
        )
        await record_agent_event(
            project_id,
            "request_failed",
            title="Request failed",
            detail=error[:4000],
            payload={
                "error": "agent_communicate_failed",
                "message": error,
                "request_id": "",
                "retry_message": message.strip()[:4000],
                "runtime": OPENHANDS_RUNTIME,
            },
            source=source,
        )
        return {
            "ok": False,
            "error": "agent_communicate_failed",
            "message": error,
            "request_id": None,
        }


async def _communicate_with_agent_impl(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    source: str = "api",
    auto_start: bool = True,
    emit_request_started: bool = True,
    request_id: str | None = None,
    _recovered_connection: bool = False,
) -> dict[str, Any]:
    from syte.agent_activity import record_agent_event
    from syte.agent_metrics import log_agent_request

    message = message.strip()

    async def fail(
        error_code: str,
        text: str,
        *,
        log_request: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Finish every failed turn with one request-scoped activity event."""
        error_text = str(text or "Agent request failed")
        if log_request:
            await log_agent_request(
                project_id,
                source=source,
                model_profile=model_profile,
                message=message,
                status="error",
                error=error_text,
            )
        event_payload = {
            "error": error_code,
            "message": error_text,
            "request_id": request_id or "",
            "retry_message": message[:4000],
            "runtime": OPENHANDS_RUNTIME,
            **(payload or {}),
        }
        await record_agent_event(
            project_id,
            "request_failed",
            title="Request failed",
            detail=error_text[:4000],
            payload=event_payload,
            source=source,
        )
        return {
            "ok": False,
            "error": error_code,
            "message": error_text,
            "request_id": request_id,
            **(payload or {}),
        }

    project = await get_project(project_id)
    if not project:
        return await fail("not_found", "Project not found")
    if not message:
        return await fail("invalid_message", "Message cannot be empty")

    if emit_request_started:
        await record_agent_event(
            project_id,
            "request_started",
            role="user",
            title="Request",
            detail=message[:4000],
            payload={
                "message": message,
                "model_profile": model_profile,
                "request_id": request_id or "",
                "runtime": OPENHANDS_RUNTIME,
            },
            source=source,
        )

    if not openhands_installed():
        text = (
            "OpenHands Agent Server is not installed. "
            "Install the project's Python dependencies to add it."
        )
        return await fail("agent_server_not_installed", text)

    if model_profile:
        try:
            await update_agent_settings(
                project_id,
                model_profile=model_profile,
                include_status=False,
            )
        except ValueError as exc:
            return await fail("invalid_model_profile", str(exc))
        project = await get_project(project_id) or project

    project = await ensure_agent_runtime(project)
    model = await selected_model_metadata(project)
    if not model["api_key"]:
        text = (
            f"No API key configured for active profile {model['profile']}. "
            f"Open AI settings and add the {model['provider_label']} key."
        )
        await update_project(
            project_id,
            {"agent_status": "error", "agent_last_error": text},
        )
        return await fail("api_key_missing", text)

    # Refresh the durable conversation only when Syte's system instruction
    # changes. Rewriting all runtime files on every turn adds latency and does
    # not update an already-created OpenHands conversation.
    if not _agent_instruction_is_current(project_id):
        try:
            await write_agent_config(project)
        except Exception as exc:
            text = str(exc) or "Could not prepare the OpenHands configuration"
            await update_project(
                project_id,
                {"agent_status": "error", "agent_last_error": text},
            )
            return await fail("agent_config_failed", text)
        if project.get("agent_conversation_id"):
            await update_project(project_id, {"agent_conversation_id": ""})
            project = await get_project(project_id) or project

    status = await get_agent_status(project_id, check_backend=False)
    if not status.get("agent_running") or not status.get("agent_healthy"):
        if not auto_start:
            error = "OpenHands agent is not running"
            return await fail(
                "agent_not_running",
                error,
                log_request=True,
            )
        ok, start_message, status = await start_agent(project_id)
        if not ok:
            return await fail(
                "agent_start_failed",
                start_message,
                log_request=True,
            )

    port = status.get("agent_port")
    if not port:
        error = "Agent has no allocated port"
        return await fail(
            "agent_no_port",
            error,
            log_request=True,
        )
    # A healthy status probe and a successful start both require /ready=200,
    # so a second readiness loop here only delays the first response.
    model = status.get("agent_model") or model
    try:
        latest_project = await get_project(project_id) or project
        conversation_id, created = await _ensure_conversation(
            latest_project,
            port=int(port),
            model=model,
        )
        for conversation_attempt in range(2):
            try:
                if not created:
                    await _switch_conversation_llm(
                        project_id,
                        port=int(port),
                        conversation_id=conversation_id,
                        model=model,
                    )
                reply, execution_status, failure = await _stream_conversation_turn(
                    project_id,
                    port=int(port),
                    conversation_id=conversation_id,
                    message=message,
                    request_id=request_id,
                    source=source,
                )
                break
            except RuntimeError as exc:
                if conversation_attempt or not _is_message_send_server_error(exc):
                    raise
                # A conversation can remain persisted after its event service
                # has failed during initialization or shutdown. Retrying the
                # same id only repeats the 500, so start a clean conversation
                # once and resend the current user turn.
                logger.warning(
                    "OpenHands message send failed for conversation %s; "
                    "recreating the conversation and waiting for it to become ready",
                    conversation_id,
                )
                await update_project(project_id, {"agent_conversation_id": ""})
                latest_project = await get_project(project_id) or latest_project
                conversation_id, created = await _ensure_conversation(
                    latest_project,
                    port=int(port),
                    model=model,
                )
                # Wait for the freshly created conversation's event service to
                # finish initializing before the retry send. Without this wait
                # the immediate follow-up POST still returns HTTP 500 because
                # the conversation is in a transient "starting" state.
                base_url = _server_url(int(port))
                headers = agent_session_headers(project_id)
                async with httpx.AsyncClient(timeout=15.0) as _client:  # Increased timeout
                    ready_status = await _wait_for_conversation_status(
                        _client,
                        base_url=base_url,
                        headers=headers,
                        conversation_id=conversation_id,
                        acceptable=_REUSABLE_CONVERSATION_STATUSES,
                        timeout_s=_CONVERSATION_READY_TIMEOUT_S,
                    )
                if ready_status not in _REUSABLE_CONVERSATION_STATUSES:
                    logger.warning(
                        "Recreated conversation %s did not reach a sendable status "
                        "(last: %s); attempting send anyway",
                        conversation_id,
                        ready_status,
                    )
                created = True
        if failure:
            error_code = (
                "agent_interrupted"
                if execution_status == "paused"
                else "agent_runtime_error"
            )
            return await fail(
                error_code,
                failure,
                log_request=True,
                payload={
                    "conversation_id": conversation_id,
                    "execution_status": execution_status,
                },
            )

        if reply:
            await record_agent_event(
                project_id,
                "message_snapshot",
                role="assistant",
                title="Assistant",
                detail=reply[:4000],
                payload={
                    "request_id": request_id or "",
                    "content": reply,
                    "conversation_id": conversation_id,
                },
                source=source,
            )
        await log_agent_request(
            project_id,
            source=source,
            model_profile=model.get("profile"),
            message=message,
            status="ok",
        )
        await record_agent_event(
            project_id,
            "request_completed",
            role="assistant",
            title="Completed",
            detail=(reply or "Request finished")[:4000],
            payload={
                "reply": reply,
                "model_profile": model.get("profile"),
                "request_id": request_id or "",
                "conversation_id": conversation_id,
                "execution_status": execution_status,
            },
            source=source,
        )
        return {
            "ok": True,
            "uuid": project_id,
            "request_id": request_id,
            "conversation_id": conversation_id,
            "model_profile": model.get("profile"),
            "model": model.get("model"),
            "provider": model.get("provider"),
            "message": message,
            "reply": reply,
            "state": {
                "conversation_id": conversation_id,
                "execution_status": execution_status,
                "runtime": OPENHANDS_RUNTIME,
            },
        }
    except httpx.HTTPError as exc:
        # The Agent Server is a child process. If it exits between the health
        # probe above and the first conversation request, the client otherwise
        # returns a generic connection error and leaves the user to retry
        # manually. Restart once in this request; a second failure is returned
        # with the normal startup diagnostics.
        if (
            not _recovered_connection
            and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            and auto_start
        ):
            logger.warning(
                "OpenHands connection failed for %s; restarting the agent once",
                project_id,
            )
            restarted, restart_message, _ = await restart_agent(project_id)
            if restarted:
                return await _communicate_with_agent_impl(
                    project_id,
                    message,
                    model_profile=model_profile,
                    source=source,
                    auto_start=auto_start,
                    emit_request_started=False,
                    request_id=request_id,
                    _recovered_connection=True,
                )
            return await fail(
                "agent_start_failed",
                restart_message,
                log_request=True,
            )
        error = f"Could not reach OpenHands agent: {exc}"
    except Exception as exc:
        error = str(exc) or "OpenHands agent request failed"

    return await fail(
        "agent_communicate_failed",
        error,
        log_request=True,
        payload={"model_profile": model.get("profile")},
    )


async def test_agent(
    project_id: str,
    *,
    source: str = "api",
    model_profile: str | None = None,
) -> dict[str, Any]:
    from syte.agent_debug import build_ai_debug_report
    from syte.agent_metrics import log_agent_request

    async def fail(**payload: Any) -> dict[str, Any]:
        if not payload.get("ok", False):
            payload["debug"] = await build_ai_debug_report(
                project_id, model_profile=model_profile
            )
        return payload

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}
    if model_profile:
        try:
            await update_agent_settings(project_id, model_profile=model_profile)
        except ValueError as exc:
            return await fail(
                ok=False,
                error="invalid_model_profile",
                message=str(exc),
                checks={"agent_server": openhands_installed(), "backend": False, "agent": False},
            )

    project = await get_project(project_id) or project
    try:
        await write_agent_config(project)
    except RuntimeError as exc:
        return await fail(
            ok=False,
            error="api_key_missing",
            message=str(exc),
            checks={"agent_server": openhands_installed(), "backend": False, "agent": False},
        )

    status = await get_agent_status(project_id)
    backend = status.get("agent_backend") or {}
    install_ok = status.get("agent_install_ok", openhands_installed())
    if not install_ok:
        return await fail(
            ok=False,
            error="agent_server_not_installed",
            message="OpenHands Agent Server is not installed. Install Python dependencies.",
            checks={"agent_server": False, "backend": backend.get("ok", False), "agent": False},
        )
    if not backend.get("ok"):
        await log_agent_request(
            project_id,
            source=source,
            status="error",
            error=backend.get("error") or "backend_unreachable",
        )
        return await fail(
            ok=False,
            error="backend_unreachable",
            message=backend.get("error") or "Provider API unreachable",
            checks={"agent_server": True, "backend": False, "agent": status.get("agent_running", False)},
            backend=backend,
        )

    if not (status.get("agent_running") and status.get("agent_healthy")):
        ok, start_message, _ = await start_agent(project_id)
        if not ok:
            await log_agent_request(project_id, source=source, status="error", error=start_message)
            return await fail(
                ok=False,
                error="agent_start_failed",
                message=start_message,
                checks={"agent_server": True, "backend": True, "agent": False},
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
            "message": "OpenHands agent test passed",
            "checks": {
                "agent_server": True,
                "backend": True,
                "agent": True,
                "communicate": True,
            },
            "reply": result.get("reply", ""),
            "model": result.get("model"),
            "provider": result.get("provider"),
        }
    return await fail(
        ok=False,
        error=result.get("error") or "test_reply_invalid",
        message=result.get("message") or "Agent did not return expected reply",
        checks={
            "agent_server": True,
            "backend": True,
            "agent": True,
            "communicate": result.get("ok", False),
        },
        reply=result.get("reply", ""),
        model=result.get("model"),
        provider=result.get("provider"),
    )
