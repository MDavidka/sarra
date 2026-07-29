"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    ALIYUN_MAAS_API_BASE,
    DEFAULT_PROFILE,
    NANO_MODEL,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    PRO_MODEL,
    ULTRA_MODEL,
    VERTEX_API_BASE,
    VYCEAI_API_BASE,
    format_price_per_mtok,
    normalize_provider_api_base,
    profile_provider,
    provider_chat_completion_url,
    provider_catalog,
)


def test_go_gemini_flash() -> None:
    nano = PROFILE_PROVIDERS["syra-nano"]
    assert DEFAULT_PROFILE == "syra-nano"
    assert nano["label"] == "Gemini"
    assert nano["display_name"] == "go"
    assert nano["api_base"] == VERTEX_API_BASE
    assert nano["model"] == NANO_MODEL == "gemini-2.5-flash"
    assert nano["role"] == "fast"
    assert nano["setting_key"] == "agent_syra_nano_api_key"


def test_agentrouter_api_base_uses_openai_v1_root() -> None:
    assert normalize_provider_api_base("https://agentrouter.org/api/v1") == (
        "https://agentrouter.org/v1"
    )
    assert normalize_provider_api_base("https://agentrouter.org/api/v1/") == (
        "https://agentrouter.org/v1"
    )
    assert provider_chat_completion_url("https://agentrouter.org/api/v1") == (
        "https://agentrouter.org/v1/chat/completions"
    )


def test_provider_api_base_normalization_does_not_change_other_hosts() -> None:
    base = "https://api.deepseek.com/v1"
    assert normalize_provider_api_base(base) == base
    assert provider_chat_completion_url(base) == f"{base}/chat/completions"


def test_metal_vyceai_claude_sonnet() -> None:
    assert "syra-havy" in PROFILE_ORDER
    pro = PROFILE_PROVIDERS["syra-havy"]
    assert pro["display_name"] == "metal"
    assert pro["label"] == "VyceAI"
    assert pro["api_base"] == VYCEAI_API_BASE
    assert pro["model"] == PRO_MODEL == "claude-sonnet-4-6"
    assert pro["role"] == "metal"


def test_ultra_aliyun_qwen_plus_cost_caps() -> None:
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "Aliyun"
    assert ultra["api_base"] == ALIYUN_MAAS_API_BASE
    assert ultra["model"] == ULTRA_MODEL == "qwen3.7-plus"
    assert ultra["role"] == "ultra"
    assert ultra["input_price_per_mtok"] == 0.17
    assert ultra["output_price_per_mtok"] == 1.02
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    assert ultra["max_tokens"] == 4096
    assert ultra["max_history_messages"] == 40
    assert ultra["max_tool_result_chars"] == 6000
    # Ultra is a full think+build profile — not a separate thinker endpoint.
    assert ultra["role"] != "think"


def test_provider_catalog_includes_prices() -> None:
    catalog = provider_catalog()
    assert len(catalog) == 3
    by_profile = {row["profile"]: row for row in catalog}
    assert by_profile["syra-nano"]["display_name"] == "go"
    assert by_profile["syra-nano"]["model"] == "gemini-2.5-flash"
    assert by_profile["syra-havy"]["display_name"] == "metal"
    assert by_profile["syra-ultra"]["api_base"] == ALIYUN_MAAS_API_BASE
    assert by_profile["syra-ultra"]["display_name"] == "Air"
    assert set(by_profile) == {"syra-nano", "syra-ultra", "syra-havy"}
    assert format_price_per_mtok(0.14) == "$0.14"
    assert format_price_per_mtok(7.5) == "$7.50"


def test_no_separate_thinker_role() -> None:
    roles = {spec.get("role") for spec in PROFILE_PROVIDERS.values()}
    assert "think" not in roles
    assert roles == {"fast", "metal", "ultra"}
