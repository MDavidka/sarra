"""Validated, provider-neutral controls for one Syte Agent turn.

The chat API and workspace composer use this module so a requested context window,
output cap, memory depth, planning mode, and verification preference all follow the
same bounded contract. These are per-turn controls: they never silently rewrite a
project's configured provider or framework.
"""

from __future__ import annotations

from typing import Any

CONTEXT_WINDOW_CHOICES = (8_000, 16_000, 32_000, 64_000, 128_000)
STREAM_TOKEN_CHOICES = (512, 1_024, 2_048, 4_096, 8_192, 16_384)
MEMORY_DEPTHS = ("focused", "balanced", "deep")
PLAN_MODES = ("auto", "always", "off")
AGENT_MODES = ("build", "plan")
STEP_BUDGET_CHOICES = (4, 8, 12, 16)

_MEMORY_PROFILES: dict[str, dict[str, int]] = {
    "focused": {
        "history_messages": 18,
        "summary_chars": 1_600,
        "active_file_limit": 6,
        "workspace_map_limit": 10,
        "index_scan_files": 0,
    },
    "balanced": {
        "history_messages": 40,
        "summary_chars": 3_500,
        "active_file_limit": 12,
        "workspace_map_limit": 20,
        "index_scan_files": 0,
    },
    "deep": {
        "history_messages": 80,
        "summary_chars": 6_500,
        "active_file_limit": 24,
        "workspace_map_limit": 40,
        "index_scan_files": 800,
    },
}


def _nearest_choice(value: int | str | None, choices: tuple[int, ...], default: int) -> int:
    if value in (None, ""):
        return default
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected one of: {', '.join(str(item) for item in choices)}") from exc
    return min(choices, key=lambda item: abs(item - numeric))


def normalize_turn_controls(
    *,
    context_window_tokens: int | str | None = None,
    stream_max_tokens: int | str | None = None,
    memory_depth: str | None = None,
    plan_mode: str | None = None,
    deployment_readiness: bool | None = None,
    agent_mode: str | None = None,
    max_steps: int | str | None = None,
) -> dict[str, Any]:
    """Return a safe, explicit turn-control object suitable for persistence.

    Values are normalized to a small set of provider-neutral choices. This keeps
    the browser, API callers, and background job runner from requesting unsafe
    arbitrary limits while still allowing operators to choose the trade-off.
    """
    depth = str(memory_depth or "balanced").strip().lower()
    if depth not in MEMORY_DEPTHS:
        raise ValueError(f"memory_depth must be one of: {', '.join(MEMORY_DEPTHS)}")
    mode = str(plan_mode or "auto").strip().lower()
    if mode not in PLAN_MODES:
        raise ValueError(f"plan_mode must be one of: {', '.join(PLAN_MODES)}")
    context_window = _nearest_choice(context_window_tokens, CONTEXT_WINDOW_CHOICES, 32_000)
    stream_limit = _nearest_choice(stream_max_tokens, STREAM_TOKEN_CHOICES, 4_096)
    execution_mode = str(agent_mode or "build").strip().lower()
    if execution_mode not in AGENT_MODES:
        raise ValueError(f"agent_mode must be one of: {', '.join(AGENT_MODES)}")
    step_budget = _nearest_choice(max_steps, STEP_BUDGET_CHOICES, 16)
    return {
        "context_window_tokens": context_window,
        "stream_max_tokens": stream_limit,
        "memory_depth": depth,
        "plan_mode": mode,
        "deployment_readiness": True if deployment_readiness is None else bool(deployment_readiness),
        "agent_mode": execution_mode,
        "max_steps": step_budget,
        **_MEMORY_PROFILES[depth],
    }


def requires_plan_before_actions(message: str, controls: dict[str, Any]) -> bool:
    """Decide whether a request should checkpoint a plan before tool execution.

    Auto mode protects materially multi-step work without adding a planning turn
    to a simple question. Website work has additional hard gates elsewhere.
    """
    mode = str(controls.get("plan_mode") or "auto")
    if mode == "always":
        return True
    if mode == "off":
        return False
    text = (message or "").strip().lower()
    if not text:
        return False
    action_words = (
        "build", "create", "implement", "refactor", "redesign", "fix", "debug",
        "deploy", "migrate", "integrate", "test", "audit", "review", "upgrade",
    )
    multi_step_markers = (
        " then ", " after ", " and ", "multi-step", "multiple", "all pages",
        "end-to-end", "deploy", "production", "without errors",
    )
    return any(word in text for word in action_words) and (
        len(text) >= 80 or any(marker in text for marker in multi_step_markers)
    )


def trim_history_to_context_budget(
    messages: list[dict[str, Any]],
    *,
    context_window_tokens: int,
    reserved_output_tokens: int,
) -> list[dict[str, Any]]:
    """Keep the newest coherent history portion inside an approximate token budget.

    Provider tokenizers differ, so this uses a deliberately conservative
    four-characters-per-token estimate. The system instruction is always kept;
    callers sanitize tool-call/tool-result pairs after trimming.
    """
    if not messages:
        return []
    available_chars = max(4_000, (int(context_window_tokens) - int(reserved_output_tokens)) * 4)
    system_messages = [dict(message) for message in messages if message.get("role") == "system"]
    non_system = [dict(message) for message in messages if message.get("role") != "system"]
    static_chars = sum(len(str(message.get("content") or "")) for message in system_messages)
    remaining = max(2_000, available_chars - static_chars)
    selected: list[dict[str, Any]] = []
    used = 0
    for message in reversed(non_system):
        size = len(str(message.get("content") or ""))
        size += len(str(message.get("tool_calls") or ""))
        size += len(str(message.get("reasoning_content") or ""))
        if used + size > remaining:
            # Continue scanning older entries only when this single message is
            # oversized: a recent compact tool/result pair may still fit behind
            # it. The newest request was already considered first.
            continue
        selected.append(message)
        used += size
    selected.reverse()
    return [*system_messages, *selected]


def agent_turn_controls_spec() -> dict[str, Any]:
    """Public API metadata for the Agent composer and external callers."""
    return {
        "context_window_tokens": list(CONTEXT_WINDOW_CHOICES),
        "stream_max_tokens": list(STREAM_TOKEN_CHOICES),
        "memory_depth": list(MEMORY_DEPTHS),
        "plan_mode": list(PLAN_MODES),
        "agent_mode": list(AGENT_MODES),
        "max_steps": list(STEP_BUDGET_CHOICES),
        "deployment_readiness": True,
    }
