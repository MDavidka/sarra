"""Tests for per-request generation depth (legacy thinking_level)."""

import pytest

from syte.ai_providers import DEFAULT_PROFILE
from syte.thinking_levels import (
    normalize_thinking_level,
    resolve_thinking_config,
    thinking_levels_spec,
)


def test_normalize_thinking_level_range() -> None:
    assert normalize_thinking_level(None) is None
    assert normalize_thinking_level(3) == 3
    assert normalize_thinking_level("5") == 5
    with pytest.raises(ValueError):
        normalize_thinking_level(0)
    with pytest.raises(ValueError):
        normalize_thinking_level(6)
    with pytest.raises(ValueError):
        normalize_thinking_level("deep")


def test_resolve_default_keeps_fallback_profile() -> None:
    cfg = resolve_thinking_config(None, fallback_profile="syra-havy")
    assert cfg["override_profile"] is False
    assert cfg["model_profile"] == "syra-havy"
    assert cfg["builder_profile"] == "syra-havy"
    assert cfg["thinker_profile"] is None
    assert cfg["temperature"] == 0.2
    assert cfg["thinking_enabled"] is False


def test_resolve_level_uses_selected_profile_only() -> None:
    instant = resolve_thinking_config(1, fallback_profile="syra-nano")
    assert instant["model_profile"] == "syra-nano"
    assert instant["builder_profile"] == "syra-nano"
    assert instant["thinker_profile"] is None
    assert instant["thinking_enabled"] is False
    assert instant["max_tool_steps"] == 10
    assert instant["override_profile"] is False

    balanced = resolve_thinking_config(3, fallback_profile="syra-base")
    assert balanced["builder_profile"] == "syra-base"
    assert balanced["thinker_profile"] is None
    assert balanced["thinking_enabled"] is True

    deep = resolve_thinking_config(4, fallback_profile="syra-ultra")
    assert deep["model_profile"] == "syra-ultra"
    assert deep["thinker_profile"] is None
    assert deep["thinking_budget_tokens"] == 4096
    assert deep["mandatory_plan"] is True

    maximum = resolve_thinking_config(5, fallback_profile=DEFAULT_PROFILE)
    assert maximum["model_profile"] == DEFAULT_PROFILE
    assert maximum["thinker_profile"] is None
    assert maximum["temperature"] == 0.4
    assert maximum["reflection"] is True


def test_thinking_levels_spec_shape() -> None:
    spec = thinking_levels_spec()
    assert spec["parameter"] == "thinking_level"
    assert spec["range"] == [1, 5]
    assert spec["thinker_profile"] is None
    assert "3" in spec["levels"]
    assert "top_p" in spec["levels"]["3"]
    assert "reasoning_effort" in spec["levels"]["3"]
    assert "thinker_profile" not in spec["levels"]["3"]


def test_resolve_includes_top_p() -> None:
    cfg = resolve_thinking_config(2, fallback_profile="syra-base")
    assert cfg["top_p"] == 0.90
    assert cfg["reasoning_effort"] == "low"
    assert cfg["builder_profile"] == "syra-base"
    assert cfg["thinker_profile"] is None
