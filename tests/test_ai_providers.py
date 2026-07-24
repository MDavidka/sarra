"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    ALIYUN_MAAS_API_BASE,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    profile_provider,
    provider_catalog,
)


def test_syra_ultra_aliyun_qwen_profile() -> None:
    assert "syra-ultra" in PROFILE_ORDER
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "Aliyun"
    assert ultra["provider"] == "openai"
    assert ultra["api_base"] == ALIYUN_MAAS_API_BASE
    assert ultra["api_base"] == (
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert ultra["model"] == "qwen3.7-plus"
    assert ultra["max_tokens"] == 4096
    assert ultra["max_history_messages"] == 40
    assert ultra["max_tool_result_chars"] == 6000
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    assert ultra["secret_env"] == "SYRA_ULTRA_API_KEY"
    assert profile_provider("syra-ultra")["model"] == "qwen3.7-plus"
    catalog = provider_catalog()
    assert any(
        entry["profile"] == "syra-ultra"
        and entry["model"] == "qwen3.7-plus"
        and entry["api_base"] == ALIYUN_MAAS_API_BASE
        and entry.get("max_tokens") == 4096
        and entry.get("max_history_messages") == 40
        and entry.get("max_tool_result_chars") == 6000
        for entry in catalog
    )
