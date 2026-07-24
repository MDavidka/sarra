"""Fixed AI provider endpoints for Syra model profiles.

Each profile is a full think+build model — there is no separate thinker.
- ``syra-nano`` — Vertex AI Gemini 3.1 Flash Lite (fast)
- ``syra-base`` — DeepSeek V4 Flash (default)
- ``syra-havy`` (pro) — Vertex AI Gemini 3.6 Flash
- ``syra-ultra`` — Aliyun Qwen 3.6 (qwen3.5-flash)
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

VERTEX_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
# Legacy alias kept for imports/migrations.
VERTED_API_BASE = VERTEX_API_BASE
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
ALIYUN_MAAS_API_BASE = (
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
)
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"

PROFILE_ORDER = ("syra-nano", "syra-base", "syra-havy", "syra-ultra")

# Default / legacy aliases (selected model handles both thinking and building).
DEFAULT_PROFILE = "syra-base"
BUILDER_PROFILE = DEFAULT_PROFILE
THINKER_PROFILE = DEFAULT_PROFILE  # deprecated — no separate thinker

NANO_MODEL = "gemini-3.1-flash-lite"
BASE_MODEL = "deepseek-v4-flash"
PRO_MODEL = "gemini-3.6-flash"
ULTRA_MODEL = "qwen3.5-flash"

# Backward-compat aliases used by older tests/docs.
BUILDER_MODEL = BASE_MODEL
THINKER_MODEL = ULTRA_MODEL


class ProfileProvider(TypedDict):
    profile: str
    label: str
    display_name: str
    provider: str
    api_base: str
    model: str
    setting_key: str
    secret_env: str
    role: NotRequired[str]  # "fast" | "build" | "pro" | "ultra"
    # Estimated USD per 1M tokens (public list prices; for UI guidance only).
    input_price_per_mtok: NotRequired[float]
    output_price_per_mtok: NotRequired[float]
    # Optional cost caps — omit to use Syte global defaults.
    max_tokens: NotRequired[int]
    max_history_messages: NotRequired[int]
    max_tool_result_chars: NotRequired[int]


PROFILE_PROVIDERS: dict[str, ProfileProvider] = {
    "syra-nano": {
        "profile": "syra-nano",
        "label": "Vertex AI",
        "display_name": "nano",
        "provider": "openai",
        "api_base": VERTEX_API_BASE,
        "model": NANO_MODEL,
        "role": "fast",
        "input_price_per_mtok": 0.25,
        "output_price_per_mtok": 1.50,
        "setting_key": "agent_syra_nano_api_key",
        "secret_env": "SYRA_NANO_API_KEY",
    },
    "syra-base": {
        "profile": "syra-base",
        "label": "DeepSeek",
        "display_name": "base",
        "provider": "openai",
        "api_base": DEEPSEEK_API_BASE,
        "model": BASE_MODEL,
        "role": "build",
        "input_price_per_mtok": 0.14,
        "output_price_per_mtok": 0.28,
        # Default builder: keep completions + tool dumps bounded.
        "max_tokens": 8192,
        "max_history_messages": 48,
        "max_tool_result_chars": 8000,
        "setting_key": "agent_syra_base_api_key",
        "secret_env": "SYRA_BASE_API_KEY",
    },
    "syra-havy": {
        "profile": "syra-havy",
        "label": "Vertex AI",
        "display_name": "pro",
        "provider": "openai",
        "api_base": VERTEX_API_BASE,
        "model": PRO_MODEL,
        "role": "pro",
        "input_price_per_mtok": 1.50,
        "output_price_per_mtok": 7.50,
        "setting_key": "agent_syra_havy_api_key",
        "secret_env": "SYRA_HAVY_API_KEY",
    },
    "syra-ultra": {
        "profile": "syra-ultra",
        "label": "Aliyun",
        "display_name": "ultra",
        "provider": "openai",
        "api_base": ALIYUN_MAAS_API_BASE,
        "model": ULTRA_MODEL,
        "role": "ultra",
        "input_price_per_mtok": 0.17,
        "output_price_per_mtok": 1.02,
        "max_tokens": 8192,
        "max_history_messages": 48,
        "max_tool_result_chars": 8000,
        "setting_key": "agent_syra_ultra_api_key",
        "secret_env": "SYRA_ULTRA_API_KEY",
    },
}


def profile_provider(profile: str) -> ProfileProvider:
    return PROFILE_PROVIDERS.get(profile, PROFILE_PROVIDERS[DEFAULT_PROFILE])


def builder_profile_name() -> str:
    return DEFAULT_PROFILE


def thinker_profile_name() -> str:
    """Deprecated — selected model handles thinking + building."""
    return DEFAULT_PROFILE


def format_price_per_mtok(value: float | None) -> str:
    """Format a $/1M token estimate for UI (e.g. ``$0.25``)."""
    if value is None:
        return "—"
    amount = float(value)
    if amount >= 1:
        return f"${amount:.2f}"
    text = f"{amount:.4f}".rstrip("0").rstrip(".")
    if "." not in text:
        text = f"{text}.00"
    elif len(text.split(".", 1)[1]) == 1:
        text = f"{text}0"
    return f"${text}"


def provider_catalog() -> list[dict[str, str | int | float]]:
    rows: list[dict[str, str | int | float]] = []
    for name in PROFILE_ORDER:
        spec = PROFILE_PROVIDERS[name]
        entry: dict[str, str | int | float] = {
            "profile": spec["profile"],
            "label": spec["label"],
            "display_name": spec["display_name"],
            "api_base": spec["api_base"],
            "model": spec["model"],
            "secret_env": spec["secret_env"],
        }
        if spec.get("role"):
            entry["role"] = str(spec["role"])
        if "input_price_per_mtok" in spec:
            entry["input_price_per_mtok"] = float(spec["input_price_per_mtok"])
            entry["input_price_label"] = format_price_per_mtok(
                float(spec["input_price_per_mtok"])
            )
        if "output_price_per_mtok" in spec:
            entry["output_price_per_mtok"] = float(spec["output_price_per_mtok"])
            entry["output_price_label"] = format_price_per_mtok(
                float(spec["output_price_per_mtok"])
            )
        if "max_tokens" in spec:
            entry["max_tokens"] = int(spec["max_tokens"])
        if "max_history_messages" in spec:
            entry["max_history_messages"] = int(spec["max_history_messages"])
        if "max_tool_result_chars" in spec:
            entry["max_tool_result_chars"] = int(spec["max_tool_result_chars"])
        rows.append(entry)
    return rows
