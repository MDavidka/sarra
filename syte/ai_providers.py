"""Fixed AI provider endpoints for Syra model profiles."""

from __future__ import annotations

from typing import NotRequired, TypedDict

VERTED_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
# Aliyun MaaS OpenAI-compatible token-plan endpoint (ap-southeast-1).
ALIYUN_MAAS_API_BASE = (
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
)

PROFILE_ORDER = ("syra-nano", "syra-base", "syra-havy", "syra-ultra")


class ProfileProvider(TypedDict):
    profile: str
    label: str
    provider: str
    api_base: str
    model: str
    setting_key: str
    secret_env: str
    # Optional cost caps — omit to use Syte global defaults.
    max_tokens: NotRequired[int]
    max_history_messages: NotRequired[int]
    max_tool_result_chars: NotRequired[int]


PROFILE_PROVIDERS: dict[str, ProfileProvider] = {
    "syra-nano": {
        "profile": "syra-nano",
        "label": "Verted",
        "provider": "openai",
        "api_base": VERTED_API_BASE,
        "model": "gemini-2.5-flash",
        "setting_key": "agent_syra_nano_api_key",
        "secret_env": "SYRA_NANO_API_KEY",
    },
    "syra-base": {
        "profile": "syra-base",
        "label": "DeepSeek",
        "provider": "openai",
        "api_base": DEEPSEEK_API_BASE,
        "model": "deepseek-chat",
        "setting_key": "agent_syra_base_api_key",
        "secret_env": "SYRA_BASE_API_KEY",
    },
    "syra-havy": {
        "profile": "syra-havy",
        "label": "Verted",
        "provider": "openai",
        "api_base": VERTED_API_BASE,
        "model": "gemini-2.5-pro",
        "setting_key": "agent_syra_havy_api_key",
        "secret_env": "SYRA_HAVY_API_KEY",
    },
    "syra-ultra": {
        "profile": "syra-ultra",
        "label": "Aliyun",
        "provider": "openai",
        "api_base": ALIYUN_MAAS_API_BASE,
        "model": "qwen3.7-plus",
        # Cost-oriented caps: shorter context in + bounded completion out.
        "max_tokens": 4096,
        "max_history_messages": 40,
        "max_tool_result_chars": 6000,
        "setting_key": "agent_syra_ultra_api_key",
        "secret_env": "SYRA_ULTRA_API_KEY",
    },
}


def profile_provider(profile: str) -> ProfileProvider:
    return PROFILE_PROVIDERS.get(profile, PROFILE_PROVIDERS["syra-base"])


def provider_catalog() -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for name in PROFILE_ORDER:
        spec = PROFILE_PROVIDERS[name]
        entry: dict[str, str | int] = {
            "profile": spec["profile"],
            "label": spec["label"],
            "api_base": spec["api_base"],
            "model": spec["model"],
            "secret_env": spec["secret_env"],
        }
        if "max_tokens" in spec:
            entry["max_tokens"] = int(spec["max_tokens"])
        if "max_history_messages" in spec:
            entry["max_history_messages"] = int(spec["max_history_messages"])
        if "max_tool_result_chars" in spec:
            entry["max_tool_result_chars"] = int(spec["max_tool_result_chars"])
        rows.append(entry)
    return rows
