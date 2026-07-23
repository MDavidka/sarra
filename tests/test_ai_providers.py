"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    XAI_API_BASE,
    profile_provider,
    provider_catalog,
)


def test_syra_ultra_xai_grok_profile() -> None:
    assert "syra-ultra" in PROFILE_ORDER
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "xAI"
    assert ultra["provider"] == "openai"
    assert ultra["api_base"] == XAI_API_BASE
    assert ultra["api_base"] == "https://api.x.ai/v1"
    assert ultra["model"] == "grok-4.5"
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    assert ultra["secret_env"] == "SYRA_ULTRA_API_KEY"
    assert profile_provider("syra-ultra")["model"] == "grok-4.5"
    catalog = provider_catalog()
    assert any(
        entry["profile"] == "syra-ultra"
        and entry["model"] == "grok-4.5"
        and entry["api_base"] == XAI_API_BASE
        for entry in catalog
    )
