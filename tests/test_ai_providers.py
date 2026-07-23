"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    FORGE_API_BASE,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    profile_provider,
    provider_catalog,
)


def test_syra_ultra_forge_grok_profile() -> None:
    assert "syra-ultra" in PROFILE_ORDER
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "Forge"
    assert ultra["provider"] == "openai"
    assert ultra["api_base"] == FORGE_API_BASE
    assert ultra["api_base"] == "https://forge-gateway-api.fly.dev/v1"
    assert ultra["model"] == "grok-4.5"
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    assert ultra["secret_env"] == "SYRA_ULTRA_API_KEY"
    assert profile_provider("syra-ultra")["model"] == "grok-4.5"
    catalog = provider_catalog()
    assert any(
        entry["profile"] == "syra-ultra"
        and entry["model"] == "grok-4.5"
        and entry["api_base"] == FORGE_API_BASE
        for entry in catalog
    )
