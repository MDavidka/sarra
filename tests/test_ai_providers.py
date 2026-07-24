"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    BUILDER_PROFILE,
    OPENROUTER_API_BASE,
    OPENROUTER_BUILDER_MODEL,
    OPENROUTER_THINKER_MODEL,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    THINKER_PROFILE,
    profile_provider,
    provider_catalog,
)


def test_builder_openrouter_qwen_flash() -> None:
    assert BUILDER_PROFILE == "syra-base"
    base = PROFILE_PROVIDERS["syra-base"]
    assert base["label"] == "OpenRouter"
    assert base["api_base"] == OPENROUTER_API_BASE
    assert base["model"] == OPENROUTER_BUILDER_MODEL
    assert base["model"] == "qwen/qwen3.5-flash-02-23"
    assert base["role"] == "build"
    assert base["setting_key"] == "agent_openrouter_api_key"
    assert base["secret_env"] == "OPENROUTER_API_KEY"
    assert base["max_tokens"] == 8192
    assert profile_provider("syra-base")["model"] == OPENROUTER_BUILDER_MODEL


def test_thinker_openrouter_nemotron() -> None:
    assert THINKER_PROFILE == "syra-ultra"
    assert "syra-ultra" in PROFILE_ORDER
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "OpenRouter"
    assert ultra["api_base"] == OPENROUTER_API_BASE
    assert ultra["model"] == OPENROUTER_THINKER_MODEL
    assert ultra["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert ultra["role"] == "think"
    assert ultra["setting_key"] == "agent_openrouter_api_key"
    assert ultra["max_tokens"] == 4096
    assert profile_provider("syra-ultra")["model"] == OPENROUTER_THINKER_MODEL
    catalog = provider_catalog()
    assert any(
        entry["profile"] == "syra-ultra"
        and entry["model"] == OPENROUTER_THINKER_MODEL
        and entry["api_base"] == OPENROUTER_API_BASE
        and entry.get("role") == "think"
        for entry in catalog
    )
    assert any(
        entry["profile"] == "syra-base"
        and entry["model"] == OPENROUTER_BUILDER_MODEL
        and entry.get("role") == "build"
        for entry in catalog
    )
