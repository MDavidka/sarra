"""Syra — a local LiteLLM proxy tuned for Syte's three model profiles.

The Syra tab in the GUI is responsible for loading a working LiteLLM proxy
(https://docs.litellm.ai/docs/proxy/docker_quick_start) that fronts the same
``syra-nano`` / ``syra-ultra`` / ``syra-havy`` profiles Syte already knows about.

Design goals ("optimized to Sarra"):
- One OpenAI-compatible gateway for all three providers so the agent only ever
  speaks ``/v1/chat/completions`` against ``127.0.0.1``.
- Config is generated from the live provider keys — no hand-written YAML.
- Router settings mirror the client-side pacing in :mod:`syte.ai_providers`
  (retries, cooldowns, cross-profile fallbacks) so a single 429 does not stall
  a build.
- The proxy runs as a managed subprocess; the GUI can start/stop it and flip
  agent traffic through it with a single toggle.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

from syte.ai_providers import (
    AI_STUDIO_OPENAI_API_BASE,
    NANO_MODEL,
    PROFILE_ORDER,
    normalize_provider_api_base,
)
from syte.config import settings
from syte.database import get_setting, set_setting

# ---------------------------------------------------------------------------
# Paths / defaults
# ---------------------------------------------------------------------------
DEFAULT_PORT = int(os.environ.get("SYRA_LITELLM_PORT", "4000") or "4000")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_MASTER_KEY = "sk-syra-local"

SETTING_ENABLED = "syra_litellm_enabled"
SETTING_MASTER_KEY = "syra_litellm_master_key"
SETTING_PORT = "syra_litellm_port"


def _proxy_dir() -> Path:
    path = settings.data_dir / "litellm"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return _proxy_dir() / "config.yaml"


def _pid_path() -> Path:
    return _proxy_dir() / "litellm.pid"


def log_path() -> Path:
    return _proxy_dir() / "litellm.log"


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------
async def proxy_port() -> int:
    raw = (await get_setting(SETTING_PORT, "")).strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_PORT


async def master_key() -> str:
    key = (await get_setting(SETTING_MASTER_KEY, "")).strip()
    if not key:
        key = DEFAULT_MASTER_KEY
        await set_setting(SETTING_MASTER_KEY, key)
    return key


async def routing_enabled() -> bool:
    return (await get_setting(SETTING_ENABLED, "")).strip() in {"1", "true", "on", "yes"}


async def set_routing_enabled(enabled: bool) -> None:
    await set_setting(SETTING_ENABLED, "1" if enabled else "")


async def proxy_base_url() -> str:
    return f"http://{DEFAULT_HOST}:{await proxy_port()}/v1"


# ---------------------------------------------------------------------------
# Per-profile LiteLLM mapping
#
# LiteLLM talks to every provider through its OpenAI-compatible surface. nano is
# special-cased to the AI Studio OpenAI-compat base (Vertex Express keys work
# there with a Bearer token); ultra/havy already expose OpenAI-compatible bases.
# ---------------------------------------------------------------------------
def _litellm_params_for(profile: str, spec: dict[str, Any]) -> dict[str, Any]:
    model = str(spec.get("model") or "")
    api_key = str(spec.get("api_key") or "")
    if profile == "syra-nano" or model == NANO_MODEL:
        api_base = AI_STUDIO_OPENAI_API_BASE
    else:
        api_base = normalize_provider_api_base(str(spec.get("api_base") or ""))
    params: dict[str, Any] = {
        # ``openai/`` = OpenAI-compatible passthrough to an explicit api_base.
        "model": f"openai/{model}",
        "api_base": api_base,
        "api_key": api_key,
    }
    if spec.get("max_tokens"):
        params["max_tokens"] = int(spec["max_tokens"])
    return params


async def build_litellm_config() -> dict[str, Any]:
    """Build a LiteLLM proxy config from the live Syte profiles."""
    from syte.cloud_agent import bridge_settings

    bridge = await bridge_settings()
    profiles: dict[str, Any] = bridge["profiles"]

    model_list: list[dict[str, Any]] = []
    available: list[str] = []
    for name in PROFILE_ORDER:
        spec = profiles.get(name) or {}
        if not str(spec.get("api_key") or "").strip():
            continue  # skip profiles without a key — nothing to route
        available.append(name)
        model_list.append(
            {
                "model_name": name,
                "litellm_params": _litellm_params_for(name, spec),
            }
        )

    # Cross-profile fallbacks: if one provider is exhausted, LiteLLM retries the
    # next configured Syra profile instead of surfacing a 429 to the agent.
    fallbacks = []
    for idx, name in enumerate(available):
        rest = [n for j, n in enumerate(available) if j != idx]
        if rest:
            fallbacks.append({name: rest})

    return {
        "model_list": model_list,
        "litellm_settings": {
            "drop_params": True,
            "num_retries": 3,
            "request_timeout": 600,
            "telemetry": False,
            **({"fallbacks": fallbacks} if fallbacks else {}),
        },
        "router_settings": {
            "routing_strategy": "usage-based-routing-v2",
            "num_retries": 3,
            "timeout": 600,
            "allowed_fails": 2,
            "cooldown_time": 30,
            "retry_after": 5,
        },
        "general_settings": {
            "master_key": await master_key(),
        },
    }


async def write_litellm_config() -> Path:
    import yaml  # provided by litellm[proxy] / pyyaml

    cfg = await build_litellm_config()
    path = config_path()
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    try:
        path.chmod(0o600)  # config embeds resolved provider keys
    except OSError:
        pass
    return path


async def config_preview() -> str:
    """Render the generated config with secrets masked for the GUI."""
    import yaml

    from syte.cloud_agent import mask_secret

    cfg = await build_litellm_config()
    for entry in cfg.get("model_list", []):
        params = entry.get("litellm_params", {})
        if params.get("api_key"):
            params["api_key"] = mask_secret(str(params["api_key"]))
    gs = cfg.get("general_settings", {})
    if gs.get("master_key"):
        gs["master_key"] = mask_secret(str(gs["master_key"]))
    return yaml.safe_dump(cfg, sort_keys=False)


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------
def _read_pid() -> int | None:
    try:
        raw = _pid_path().read_text().strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _port_healthy(port: int) -> bool:
    import httpx

    url = f"http://{DEFAULT_HOST}:{port}/health/liveliness"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def proxy_status() -> dict[str, Any]:
    pid = _read_pid()
    port = await proxy_port()
    alive = _pid_alive(pid)
    healthy = await _port_healthy(port) if alive else False
    profiles = await _profiles()
    configured = [
        name
        for name in PROFILE_ORDER
        if str((profiles.get(name) or {}).get("api_key") or "").strip()
    ]
    return {
        "installed": _litellm_installed(),
        "running": alive,
        "healthy": healthy,
        "pid": pid,
        "port": port,
        "host": DEFAULT_HOST,
        "base_url": await proxy_base_url(),
        "routing_enabled": await routing_enabled(),
        "configured_profiles": configured,
        "master_key_hint": _mask(await master_key()),
    }


async def _profiles() -> dict[str, Any]:
    from syte.cloud_agent import bridge_settings

    return (await bridge_settings())["profiles"]


def _mask(value: str) -> str:
    from syte.cloud_agent import mask_secret

    return mask_secret(value)


def _litellm_installed() -> bool:
    from importlib.util import find_spec

    return find_spec("litellm") is not None


async def start_proxy() -> dict[str, Any]:
    if not _litellm_installed():
        return {
            "ok": False,
            "error": "litellm is not installed. Install with: pip install 'litellm[proxy]'",
        }
    status = await proxy_status()
    if status["running"] and status["healthy"]:
        return {"ok": True, "message": "Syra proxy already running", "status": status}

    # Stale pid without a live process — clean up before restart.
    if status["running"] and not status["healthy"]:
        await stop_proxy()

    port = await proxy_port()
    path = await write_litellm_config()
    log = log_path().open("a", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "litellm",
        "--config",
        str(path),
        "--host",
        DEFAULT_HOST,
        "--port",
        str(port),
        "--num_workers",
        "1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    except OSError as exc:
        log.close()
        return {"ok": False, "error": f"Failed to launch LiteLLM: {exc}"}

    _pid_path().write_text(str(proc.pid), encoding="utf-8")

    # Wait for the gateway to become live (up to ~20s).
    for _ in range(40):
        await asyncio.sleep(0.5)
        if await _port_healthy(port):
            break

    status = await proxy_status()
    if not status["healthy"]:
        return {
            "ok": False,
            "error": "LiteLLM started but did not become healthy — check the Syra proxy log.",
            "status": status,
        }
    return {"ok": True, "message": f"Syra proxy running on {status['base_url']}", "status": status}


async def stop_proxy() -> dict[str, Any]:
    pid = _read_pid()
    if not _pid_alive(pid):
        _pid_path().unlink(missing_ok=True)
        return {"ok": True, "message": "Syra proxy is not running", "status": await proxy_status()}
    try:
        # Kill the whole process group started with start_new_session=True.
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(20):
        await asyncio.sleep(0.25)
        if not _pid_alive(_read_pid()):
            break
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass
    _pid_path().unlink(missing_ok=True)
    return {"ok": True, "message": "Syra proxy stopped", "status": await proxy_status()}


def proxy_log_tail(lines: int = 200) -> str:
    path = log_path()
    if not path.exists():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-max(1, lines):])


# ---------------------------------------------------------------------------
# Routing hook — rewrite profiles so agent traffic flows through the proxy
# ---------------------------------------------------------------------------
async def apply_proxy_routing(profiles: dict[str, Any]) -> bool:
    """When routing is enabled and the proxy is healthy, point every profile at
    the local LiteLLM gateway. Mutates *profiles* in place; returns whether the
    rewrite was applied."""
    if not await routing_enabled():
        return False
    port = await proxy_port()
    if not await _port_healthy(port):
        return False
    base = await proxy_base_url()
    key = await master_key()
    for name, spec in profiles.items():
        if not str(spec.get("api_key") or "").strip():
            continue  # no upstream key -> not part of the proxy config
        spec["api_base"] = base
        spec["api_key"] = key
        spec["model"] = name  # LiteLLM model_name == profile name
        spec["provider"] = "openai"
        spec["routed_via_proxy"] = True
    return True
