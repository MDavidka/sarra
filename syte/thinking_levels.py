"""Per-request thinking depth (Fast → Deep Think) for Syte cloud agent turns.

``thinking_level`` is independent of the project's persistent ``model_profile``.
When provided on ``agent_change`` / ``agent_communicate`` / GUI chat, it selects
temperature, top_p, optional native thinking budgets, tool-step cap, and
(optionally) which Syra profile to use for that turn only.
"""

from __future__ import annotations

from typing import Any

THINKING_LEVEL_MIN = 1
THINKING_LEVEL_MAX = 5
DEFAULT_THINKING_LEVEL = 3

# level → generation settings applied for a single request
THINKING_LEVELS: dict[int, dict[str, Any]] = {
    1: {
        "label": "Instant",
        "model_profile": "syra-nano",
        "temperature": 0.1,
        "top_p": 0.85,
        "reasoning_effort": "low",
        "thinking_enabled": False,
        "thinking_budget_tokens": 0,
        "max_tool_steps": 3,
        "stream": True,
        "mandatory_plan": False,
        "reflection": False,
    },
    2: {
        "label": "Fast",
        "model_profile": "syra-nano",
        "temperature": 0.2,
        "top_p": 0.90,
        "reasoning_effort": "low",
        "thinking_enabled": False,
        "thinking_budget_tokens": 0,
        "max_tool_steps": 8,
        "stream": True,
        "mandatory_plan": False,
        "reflection": False,
    },
    3: {
        "label": "Balanced",
        "model_profile": "syra-base",
        "temperature": 0.2,
        "top_p": 0.95,
        "reasoning_effort": "medium",
        "thinking_enabled": True,
        "thinking_budget_tokens": 1024,
        "max_tool_steps": 24,
        "stream": True,
        "mandatory_plan": False,
        "reflection": False,
    },
    4: {
        "label": "Deep",
        "model_profile": "syra-base",
        "temperature": 0.3,
        "top_p": 0.98,
        "reasoning_effort": "high",
        "thinking_enabled": True,
        "thinking_budget_tokens": 4096,
        "max_tool_steps": 40,
        "stream": True,
        "mandatory_plan": True,
        "reflection": False,
    },
    5: {
        "label": "Max",
        "model_profile": "syra-havy",
        "temperature": 0.4,
        "top_p": 1.0,
        "reasoning_effort": "high",
        "thinking_enabled": True,
        "thinking_budget_tokens": 8192,
        "max_tool_steps": 60,
        "stream": True,
        "mandatory_plan": True,
        "reflection": True,
    },
}


def normalize_thinking_level(value: int | str | None) -> int | None:
    """Return a clamped level int, or None when the caller omitted the slider."""
    if value is None or value == "":
        return None
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"thinking_level must be an integer {THINKING_LEVEL_MIN}–{THINKING_LEVEL_MAX}"
        ) from exc
    if level < THINKING_LEVEL_MIN or level > THINKING_LEVEL_MAX:
        raise ValueError(
            f"thinking_level must be between {THINKING_LEVEL_MIN} and {THINKING_LEVEL_MAX}"
        )
    return level


def resolve_thinking_config(
    thinking_level: int | str | None,
    *,
    fallback_profile: str | None = None,
) -> dict[str, Any]:
    """Build the per-request generation config.

    When ``thinking_level`` is omitted, keep the project's profile and use
    conservative defaults (temperature 0.2, streaming on, no native thinking).
    """
    level = normalize_thinking_level(thinking_level)
    if level is None:
        profile = (fallback_profile or "syra-base").strip() or "syra-base"
        return {
            "thinking_level": None,
            "label": "Default",
            "model_profile": profile,
            "temperature": 0.2,
            "top_p": 0.95,
            "reasoning_effort": "medium",
            "thinking_enabled": False,
            "thinking_budget_tokens": 0,
            "max_tool_steps": 48,
            "stream": True,
            "mandatory_plan": False,
            "reflection": False,
            "override_profile": False,
        }
    config = dict(THINKING_LEVELS[level])
    config["thinking_level"] = level
    config["override_profile"] = True
    return config


def deepseek_thinking_payload(config: dict[str, Any]) -> dict[str, Any] | None:
    """Payload fragment for DeepSeek ``thinking`` when the host is deepseek.com.

    Returns ``None`` when thinking is disabled so callers can omit the field
    entirely (some gateways reject ``thinking: disabled`` on non-R1 models).
    """
    if not config.get("thinking_enabled"):
        return None
    budget = int(config.get("thinking_budget_tokens") or 0)
    payload: dict[str, Any] = {"type": "enabled"}
    if budget > 0:
        payload["budget_tokens"] = budget
    return payload


def model_supports_native_thinking(
    *,
    provider: str = "",
    model: str = "",
    api_base: str = "",
) -> bool:
    """Return True when the provider/model accepts native thinking payloads."""
    provider_l = (provider or "").lower()
    model_l = (model or "").lower()
    api_l = (api_base or "").lower()
    is_deepseek = "deepseek.com" in api_l or "deepseek" in provider_l or "deepseek" in model_l
    is_anthropic = "anthropic" in provider_l or "claude" in model_l
    is_openai_reasoning = any(x in model_l for x in ("o1", "o3", "o4", "gpt-5"))
    return bool(is_deepseek or is_anthropic or is_openai_reasoning)


def build_model_thinking_params(
    thinking_config: dict[str, Any] | None,
    *,
    provider: str = "",
    model: str = "",
    api_base: str = "",
) -> dict[str, Any]:
    """Map a thinking config to provider-specific inference params.

    Always returns temperature + top_p. Native thinking payloads are attached
    only when thinking is enabled **and** the provider/model supports them.
    Instant/Fast (thinking_enabled=False) never injects reasoning keys.

    Extra metadata keys (stripped before provider payload assembly):
    - ``thinking_requested``: user/config asked for thinking
    - ``thinking_supported``: provider/model can accept thinking params
    - ``thinking_applied``: a native thinking key was attached
    """
    cfg = thinking_config or {}
    params: dict[str, Any] = {
        "temperature": float(cfg.get("temperature") if cfg.get("temperature") is not None else 0.2),
        "top_p": float(cfg.get("top_p") if cfg.get("top_p") is not None else 0.95),
    }

    provider_l = (provider or "").lower()
    model_l = (model or "").lower()
    api_l = (api_base or "").lower()
    budget = int(cfg.get("thinking_budget_tokens") or 0)
    enabled = bool(cfg.get("thinking_enabled"))
    supported = model_supports_native_thinking(
        provider=provider, model=model, api_base=api_base,
    )
    params["thinking_requested"] = enabled
    params["thinking_supported"] = supported
    params["thinking_applied"] = False

    is_deepseek = "deepseek.com" in api_l or "deepseek" in provider_l or "deepseek" in model_l
    is_anthropic = "anthropic" in provider_l or "claude" in model_l
    is_openai_reasoning = any(x in model_l for x in ("o1", "o3", "o4", "gpt-5"))

    # DeepSeek prefix cache is safe even without thinking mode.
    if is_deepseek:
        params["cache_prompt"] = True
        if enabled:
            thinking = deepseek_thinking_payload(cfg)
            if thinking is not None:
                params["thinking"] = thinking
                params["thinking_applied"] = True

    if is_anthropic and enabled and budget > 0:
        params["thinking"] = {
            "type": "enabled",
            "budget_tokens": max(1024, budget),
        }
        params["thinking_applied"] = True

    # Only attach OpenAI reasoning_effort for models known to accept it.
    if is_openai_reasoning and enabled:
        effort = str(cfg.get("reasoning_effort") or "").strip()
        if effort:
            params["reasoning_effort"] = effort
            params["thinking_applied"] = True

    return params


def apply_prompt_cache_markers(
    messages: list[dict[str, Any]],
    *,
    provider: str = "",
    model: str = "",
    api_base: str = "",
) -> list[dict[str, Any]]:
    """Annotate the system prompt for provider-native prompt caching when supported.

    OpenAI-compatible Syra endpoints (DeepSeek / Gemini) rely on stable system-first
    prefixes + DeepSeek ``cache_prompt``. Anthropic-native cache_control content
    blocks are only applied when the provider/model is actually Anthropic/Claude.

    If the system message is already a list of content blocks (static/dynamic split
    with cache_control on the static prefix), leave it unchanged.
    """
    if not messages:
        return messages
    provider_l = (provider or "").lower()
    model_l = (model or "").lower()
    out = [dict(m) for m in messages]
    system = out[0]
    if system.get("role") != "system":
        return out

    content = system.get("content")
    if isinstance(content, list):
        # Already structured (e.g. static + dynamic with cache breakpoint).
        out[0] = system
        _ = api_base
        return out

    if "anthropic" in provider_l or "claude" in model_l:
        if isinstance(content, str):
            system["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        out[0] = system

    # DeepSeek / Gemini / OpenAI: keep plain string system content (cache via prefix + cache_prompt).
    _ = api_base
    return out


def thinking_levels_spec() -> dict[str, Any]:
    """Public API/docs description of the slider."""
    return {
        "parameter": "thinking_level",
        "range": [THINKING_LEVEL_MIN, THINKING_LEVEL_MAX],
        "default_when_omitted": "project model_profile + temperature 0.2",
        "levels": {
            str(level): {
                "label": cfg["label"],
                "model_profile": cfg["model_profile"],
                "temperature": cfg["temperature"],
                "top_p": cfg["top_p"],
                "reasoning_effort": cfg["reasoning_effort"],
                "thinking_budget_tokens": cfg["thinking_budget_tokens"],
                "max_tool_steps": cfg["max_tool_steps"],
            }
            for level, cfg in THINKING_LEVELS.items()
        },
        "note": (
            "thinking_level configures the turn only — it does not persist the "
            "project's model_profile setting. Temperature/top_p apply to all "
            "providers; native thinking budgets apply when the provider supports them."
        ),
    }
