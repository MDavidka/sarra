"""Fixed AI provider endpoints for Syra model profiles."""

from __future__ import annotations

from typing import TypedDict

VERTED_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
# Forge OpenAI-compatible gateway (https://www.forge-ai.space/#integration).
FORGE_API_BASE = "https://forge-gateway-api.fly.dev/v1"

PROFILE_ORDER = ("syra-nano", "syra-base", "syra-havy", "syra-ultra")


class ProfileProvider(TypedDict):
    profile: str
    label: str
    provider: str
    api_base: str
    model: str
    setting_key: str
    secret_env: str


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
        "label": "Forge",
        "provider": "openai",
        "api_base": FORGE_API_BASE,
        "model": "grok-4.5",
        "setting_key": "agent_syra_ultra_api_key",
        "secret_env": "SYRA_ULTRA_API_KEY",
    },
}


def profile_provider(profile: str) -> ProfileProvider:
    return PROFILE_PROVIDERS.get(profile, PROFILE_PROVIDERS["syra-base"])


def provider_catalog() -> list[dict[str, str]]:
    return [
        {
            "profile": spec["profile"],
            "label": spec["label"],
            "api_base": spec["api_base"],
            "model": spec["model"],
            "secret_env": spec["secret_env"],
        }
        for spec in (PROFILE_PROVIDERS[p] for p in PROFILE_ORDER)
    ]
