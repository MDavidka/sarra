"""Tests for fixed Syra provider profiles."""

from syte.ai_providers import (
    ALIYUN_MAAS_API_BASE,
    BASE_MODEL,
    DEEPSEEK_API_BASE,
    DEFAULT_PROFILE,
    NANO_MODEL,
    PROFILE_ORDER,
    PROFILE_PROVIDERS,
    PRO_MODEL,
    ULTRA_MODEL,
    VERTEX_API_BASE,
    format_price_per_mtok,
    profile_provider,
    provider_catalog,
)


def test_nano_vertex_gemini_flash_lite() -> None:
    nano = PROFILE_PROVIDERS["syra-nano"]
    assert nano["label"] == "Vertex AI"
    assert nano["api_base"] == VERTEX_API_BASE
    assert nano["model"] == NANO_MODEL == "gemini-3.1-flash-lite"
    assert nano["role"] == "fast"
    assert nano["input_price_per_mtok"] == 0.25
    assert nano["output_price_per_mtok"] == 1.50
    assert nano["setting_key"] == "agent_syra_nano_api_key"


def test_base_deepseek_v4_flash() -> None:
    assert DEFAULT_PROFILE == "syra-base"
    base = PROFILE_PROVIDERS["syra-base"]
    assert base["label"] == "DeepSeek"
    assert base["api_base"] == DEEPSEEK_API_BASE
    assert base["model"] == BASE_MODEL == "deepseek-v4-flash"
    assert base["role"] == "build"
    assert base["input_price_per_mtok"] == 0.14
    assert base["output_price_per_mtok"] == 0.28
    assert base["setting_key"] == "agent_syra_base_api_key"
    assert base["secret_env"] == "SYRA_BASE_API_KEY"
    assert base["max_tokens"] == 8192
    assert profile_provider("syra-base")["model"] == BASE_MODEL


def test_pro_vertex_gemini_36_flash() -> None:
    assert "syra-havy" in PROFILE_ORDER
    pro = PROFILE_PROVIDERS["syra-havy"]
    assert pro["display_name"] == "pro"
    assert pro["label"] == "Vertex AI"
    assert pro["api_base"] == VERTEX_API_BASE
    assert pro["model"] == PRO_MODEL == "gemini-3.6-flash"
    assert pro["role"] == "pro"
    assert pro["input_price_per_mtok"] == 1.50
    assert pro["output_price_per_mtok"] == 7.50


def test_ultra_aliyun_qwen_flash() -> None:
    ultra = PROFILE_PROVIDERS["syra-ultra"]
    assert ultra["label"] == "Aliyun"
    assert ultra["api_base"] == ALIYUN_MAAS_API_BASE
    assert ultra["model"] == ULTRA_MODEL == "qwen3.5-flash"
    assert ultra["role"] == "ultra"
    assert ultra["input_price_per_mtok"] == 0.17
    assert ultra["output_price_per_mtok"] == 1.02
    assert ultra["setting_key"] == "agent_syra_ultra_api_key"
    # Ultra is a full think+build profile — not a separate thinker endpoint.
    assert ultra["role"] != "think"


def test_provider_catalog_includes_prices() -> None:
    catalog = provider_catalog()
    assert len(catalog) == 4
    by_profile = {row["profile"]: row for row in catalog}
    assert by_profile["syra-nano"]["input_price_label"] == "$0.25"
    assert by_profile["syra-nano"]["output_price_label"] == "$1.50"
    assert by_profile["syra-base"]["model"] == "deepseek-v4-flash"
    assert by_profile["syra-havy"]["display_name"] == "pro"
    assert by_profile["syra-ultra"]["api_base"] == ALIYUN_MAAS_API_BASE
    assert format_price_per_mtok(0.14) == "$0.14"
    assert format_price_per_mtok(7.5) == "$7.50"


def test_no_separate_thinker_role() -> None:
    roles = {spec.get("role") for spec in PROFILE_PROVIDERS.values()}
    assert "think" not in roles
