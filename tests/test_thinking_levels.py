"""Tests for the Fast → Deep Think slider mapping."""

import pytest

from syte.ai_providers import BUILDER_PROFILE, THINKER_PROFILE
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


def test_resolve_level_maps_builder_and_thinker() -> None:
    instant = resolve_thinking_config(1)
    assert instant["model_profile"] == BUILDER_PROFILE
    assert instant["builder_profile"] == BUILDER_PROFILE
    assert instant["thinker_profile"] is None
    assert instant["thinking_enabled"] is False
    assert instant["max_tool_steps"] == 10
    assert instant["override_profile"] is True

    balanced = resolve_thinking_config(3)
    assert balanced["builder_profile"] == BUILDER_PROFILE
    assert balanced["thinker_profile"] == THINKER_PROFILE
    assert balanced["thinking_enabled"] is True

    deep = resolve_thinking_config(4)
    assert deep["model_profile"] == BUILDER_PROFILE
    assert deep["thinker_profile"] == THINKER_PROFILE
    assert deep["thinking_budget_tokens"] == 4096
    assert deep["mandatory_plan"] is True

    maximum = resolve_thinking_config(5)
    assert maximum["model_profile"] == BUILDER_PROFILE
    assert maximum["thinker_profile"] == THINKER_PROFILE
    assert maximum["temperature"] == 0.4
    assert maximum["reflection"] is True


def test_thinking_levels_spec_shape() -> None:
    spec = thinking_levels_spec()
    assert spec["parameter"] == "thinking_level"
    assert spec["range"] == [1, 5]
    assert spec["builder_profile"] == BUILDER_PROFILE
    assert spec["thinker_profile"] == THINKER_PROFILE
    assert "3" in spec["levels"]
    assert "top_p" in spec["levels"]["3"]
    assert "reasoning_effort" in spec["levels"]["3"]
    assert spec["levels"]["3"]["thinker_profile"] == THINKER_PROFILE


def test_resolve_includes_top_p() -> None:
    cfg = resolve_thinking_config(2)
    assert cfg["top_p"] == 0.90
    assert cfg["reasoning_effort"] == "low"
    assert cfg["builder_profile"] == BUILDER_PROFILE
    assert cfg["thinker_profile"] is None
