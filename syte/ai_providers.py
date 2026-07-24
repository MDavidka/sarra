"""Fixed AI provider endpoints for Syra model profiles.

Builder vs thinker use **different APIs** and keys:
- ``syra-base`` (builder) — Aliyun MaaS ``qwen3.5-flash`` for code edits / tool loops
- ``syra-ultra`` (thinker) — OpenRouter ``nvidia/nemotron-3-ultra-550b-a55b:free`` for plans
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

VERTED_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
ALIYUN_MAAS_API_BASE = (
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
)
# Legacy DeepSeek endpoint kept for docs/migrations only.
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"

PROFILE_ORDER = ("syra-nano", "syra-base", "syra-havy", "syra-ultra")

# Role aliases used by thinking_levels / cloud_agent routing.
BUILDER_PROFILE = "syra-base"
THINKER_PROFILE = "syra-ultra"

BUILDER_MODEL = "qwen3.5-flash"
THINKER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


class ProfileProvider(TypedDict):
    profile: str
    label: str
    provider: str
    api_base: str
    model: str
    setting_key: str
    secret_env: str
    role: NotRequired[str]  # "build" | "think" | "fast" | "vision"
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
        "role": "fast",
        "setting_key": "agent_syra_nano_api_key",
        "secret_env": "SYRA_NANO_API_KEY",
    },
    "syra-base": {
        "profile": "syra-base",
        "label": "Aliyun",
        "provider": "openai",
        "api_base": ALIYUN_MAAS_API_BASE,
        "model": BUILDER_MODEL,
        "role": "build",
        # Cheap builder: keep completions + tool dumps bounded.
        "max_tokens": 8192,
        "max_history_messages": 48,
        "max_tool_result_chars": 8000,
        "setting_key": "agent_syra_base_api_key",
        "secret_env": "SYRA_BASE_API_KEY",
    },
    "syra-havy": {
        "profile": "syra-havy",
        "label": "Verted",
        "provider": "openai",
        "api_base": VERTED_API_BASE,
        "model": "gemini-2.5-pro",
        "role": "vision",
        "setting_key": "agent_syra_havy_api_key",
        "secret_env": "SYRA_HAVY_API_KEY",
    },
    "syra-ultra": {
        "profile": "syra-ultra",
        "label": "OpenRouter",
        "provider": "openai",
        "api_base": OPENROUTER_API_BASE,
        "model": THINKER_MODEL,
        "role": "think",
        # Thinker plans only — short structured output, not long code dumps.
        "max_tokens": 4096,
        "max_history_messages": 24,
        "max_tool_result_chars": 6000,
        "setting_key": "agent_syra_ultra_api_key",
        "secret_env": "SYRA_ULTRA_API_KEY",
    },
}


def profile_provider(profile: str) -> ProfileProvider:
    return PROFILE_PROVIDERS.get(profile, PROFILE_PROVIDERS[BUILDER_PROFILE])


def builder_profile_name() -> str:
    return BUILDER_PROFILE


def thinker_profile_name() -> str:
    return THINKER_PROFILE


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
        if spec.get("role"):
            entry["role"] = str(spec["role"])
        if "max_tokens" in spec:
            entry["max_tokens"] = int(spec["max_tokens"])
        if "max_history_messages" in spec:
            entry["max_history_messages"] = int(spec["max_history_messages"])
        if "max_tool_result_chars" in spec:
            entry["max_tool_result_chars"] = int(spec["max_tool_result_chars"])
        rows.append(entry)
    return rows
