"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    FIREWORKS_API_BASE,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    profile_provider,
    provider_catalog,
)


def test_syra_ultra_fireworks_minimax_profile() -> None:
    assert "syra-ultra" in PROFILE_ORDER
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "Fireworks"
    assert ultra["provider"] == "openai"
    assert ultra["api_base"] == FIREWORKS_API_BASE
    assert ultra["api_base"] == "https://api.fireworks.ai/inference/v1"
    assert ultra["model"] == "accounts/fireworks/models/minimax-m3"
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    assert ultra["secret_env"] == "SYRA_ULTRA_API_KEY"
    assert profile_provider("syra-ultra")["model"] == "accounts/fireworks/models/minimax-m3"
    catalog = provider_catalog()
    assert any(
        entry["profile"] == "syra-ultra"
        and entry["model"] == "accounts/fireworks/models/minimax-m3"
        and entry["api_base"] == FIREWORKS_API_BASE
        for entry in catalog
    )
