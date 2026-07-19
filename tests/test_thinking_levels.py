"""Tests for the Fast → Deep Think slider mapping."""

import pytest

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
    assert cfg["temperature"] == 0.2
    assert cfg["thinking_enabled"] is False


def test_resolve_level_maps_profile_and_budget() -> None:
    instant = resolve_thinking_config(1)
    assert instant["model_profile"] == "syra-nano"
    assert instant["max_tool_steps"] == 3
    assert instant["override_profile"] is True

    deep = resolve_thinking_config(4)
    assert deep["model_profile"] == "syra-base"
    assert deep["thinking_budget_tokens"] == 4096
    assert deep["mandatory_plan"] is True

    maximum = resolve_thinking_config(5)
    assert maximum["model_profile"] == "syra-havy"
    assert maximum["temperature"] == 0.4
    assert maximum["reflection"] is True


def test_thinking_levels_spec_shape() -> None:
    spec = thinking_levels_spec()
    assert spec["parameter"] == "thinking_level"
    assert spec["range"] == [1, 5]
    assert "3" in spec["levels"]
