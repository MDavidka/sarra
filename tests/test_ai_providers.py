"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    ALIYUN_MAAS_API_BASE,
    BUILDER_MODEL,
    BUILDER_PROFILE,
    OPENROUTER_API_BASE,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    THINKER_MODEL,
    THINKER_PROFILE,
    profile_provider,
    provider_catalog,
)


def test_builder_aliyun_qwen_flash() -> None:
    assert BUILDER_PROFILE == "syra-base"
    base = PROFILE_PROVIDERS["syra-base"]
    assert base["label"] == "Aliyun"
    assert base["api_base"] == ALIYUN_MAAS_API_BASE
    assert base["model"] == BUILDER_MODEL
    assert base["model"] == "qwen3.5-flash"
    assert base["role"] == "build"
    assert base["setting_key"] == "agent_syra_base_api_key"
    assert base["secret_env"] == "SYRA_BASE_API_KEY"
    assert base["max_tokens"] == 8192
    assert profile_provider("syra-base")["model"] == BUILDER_MODEL


def test_thinker_openrouter_nemotron_separate_api() -> None:
    assert THINKER_PROFILE == "syra-ultra"
    assert "syra-ultra" in PROFILE_ORDER
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    base = PROFILE_PROVIDERS["syra-base"]
    assert ultra["label"] == "OpenRouter"
    assert ultra["api_base"] == OPENROUTER_API_BASE
    assert ultra["model"] == THINKER_MODEL
    assert ultra["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert ultra["role"] == "think"
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    assert ultra["secret_env"] == "SYRA_ULTRA_API_KEY"
    # Builder and thinker must not share API base or setting key.
    assert ultra["api_base"] != base["api_base"]
    assert ultra["setting_key"] != base["setting_key"]
    assert ultra["secret_env"] != base["secret_env"]
    catalog = provider_catalog()
    assert any(
        entry["profile"] == "syra-ultra"
        and entry["model"] == THINKER_MODEL
        and entry["api_base"] == OPENROUTER_API_BASE
        and entry.get("role") == "think"
        for entry in catalog
    )
    assert any(
        entry["profile"] == "syra-base"
        and entry["model"] == BUILDER_MODEL
        and entry["api_base"] == ALIYUN_MAAS_API_BASE
        and entry.get("role") == "build"
        for entry in catalog
    )
