"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    ALIYUN_MAAS_API_BASE,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    profile_provider,
    provider_catalog,
)


def test_syra_ultra_aliyun_glm_profile() -> None:
    assert "syra-ultra" in PROFILE_ORDER
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "Aliyun"
    assert ultra["provider"] == "openai"
    assert ultra["api_base"] == ALIYUN_MAAS_API_BASE
    assert ultra["api_base"] == (
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert ultra["model"] == "glm-5.2"
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    assert ultra["secret_env"] == "SYRA_ULTRA_API_KEY"
    assert profile_provider("syra-ultra")["model"] == "glm-5.2"
    catalog = provider_catalog()
    assert any(
        entry["profile"] == "syra-ultra"
        and entry["model"] == "glm-5.2"
        and entry["api_base"] == ALIYUN_MAAS_API_BASE
        for entry in catalog
    )
