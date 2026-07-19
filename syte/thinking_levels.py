"""Per-request thinking depth (Fast → Deep Think) for Syte cloud agent turns.

``thinking_level`` is independent of the project's persistent ``model_profile``.
When provided on ``agent_change`` / ``agent_communicate`` / GUI chat, it selects
temperature, optional DeepSeek thinking budget, tool-step cap, and (optionally)
which Syra profile to use for that turn only.
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
    conservative defaults (temperature 0.2, streaming on, no DeepSeek thinking).
    """
    level = normalize_thinking_level(thinking_level)
    if level is None:
        profile = (fallback_profile or "syra-base").strip() or "syra-base"
        return {
            "thinking_level": None,
            "label": "Default",
            "model_profile": profile,
            "temperature": 0.2,
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


def deepseek_thinking_payload(config: dict[str, Any]) -> dict[str, Any]:
    """Payload fragment for DeepSeek ``thinking`` when the host is deepseek.com."""
    if not config.get("thinking_enabled"):
        return {"type": "disabled"}
    budget = int(config.get("thinking_budget_tokens") or 0)
    payload: dict[str, Any] = {"type": "enabled"}
    if budget > 0:
        payload["budget_tokens"] = budget
    return payload


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
                "thinking_budget_tokens": cfg["thinking_budget_tokens"],
                "max_tool_steps": cfg["max_tool_steps"],
            }
            for level, cfg in THINKING_LEVELS.items()
        },
        "note": (
            "thinking_level configures the turn only — it does not persist the "
            "project's model_profile setting."
        ),
    }
