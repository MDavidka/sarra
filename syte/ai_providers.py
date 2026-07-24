"""Fixed AI provider endpoints for Syra model profiles.

Each profile is a full think+build model — there is no separate thinker.
- ``syra-nano`` — Vertex AI Gemini 3.1 Flash Lite (fast)
- ``syra-base`` — DeepSeek V4 Flash (default)
- ``syra-havy`` (pro) — Vertex AI Gemini 3.6 Flash
- ``syra-ultra`` — Aliyun Qwen3.7-Plus (qwen3.7-plus, cost-capped)
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
# Pay-as-you-go DashScope OpenAI-compat (standard ``sk-`` Aliyun keys — not Token Plan).
ALIYUN_DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"

PROFILE_ORDER = ("syra-nano", "syra-base", "syra-havy", "syra-ultra")

# Default / legacy aliases (selected model handles both thinking and building).
DEFAULT_PROFILE = "syra-base"
BUILDER_PROFILE = DEFAULT_PROFILE
THINKER_PROFILE = DEFAULT_PROFILE  # deprecated — no separate thinker

NANO_MODEL = "gemini-3.1-flash-lite"
BASE_MODEL = "deepseek-v4-flash"
PRO_MODEL = "gemini-3.6-flash"
ULTRA_MODEL = "qwen3.7-plus"

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
        # Cost-oriented caps from PR #142: shorter context in + bounded completion out.
        "max_tokens": 4096,
        "max_history_messages": 40,
        "max_tool_result_chars": 6000,
        "setting_key": "agent_syra_ultra_api_key",
        "secret_env": "SYRA_ULTRA_API_KEY",
    },
}


def profile_provider(profile: str) -> ProfileProvider:
    return PROFILE_PROVIDERS.get(profile, PROFILE_PROVIDERS[DEFAULT_PROFILE])


def looks_like_openrouter_key(api_key: str | None) -> bool:
    """OpenRouter keys are ``sk-or-…`` (legacy syra-ultra before the Aliyun swap)."""
    return (api_key or "").strip().lower().startswith("sk-or-")


def looks_like_aliyun_token_plan_key(api_key: str | None) -> bool:
    """Aliyun Token Plan keys must start with ``sk-sp-`` (not interchangeable with ``sk-``)."""
    return (api_key or "").strip().lower().startswith("sk-sp-")


def looks_like_aliyun_payg_key(api_key: str | None) -> bool:
    """Standard Model Studio / DashScope pay-as-you-go keys (``sk-`` but not ``sk-or-`` / ``sk-sp-``)."""
    key = (api_key or "").strip().lower()
    if not key.startswith("sk-"):
        return False
    if looks_like_openrouter_key(key) or looks_like_aliyun_token_plan_key(key):
        return False
    # DeepSeek keys are also sk-… — treat only keys that are clearly NOT deepseek-shaped
    # as Aliyun PAYG when they contain aliyun-ish markers, otherwise leave ambiguous.
    # Prefer explicit Token Plan (sk-sp-) for ultra; PAYG detection is best-effort for routing.
    return "aliyun" in key or "dashscope" in key


def looks_like_deepseek_key(api_key: str | None) -> bool:
    """DeepSeek platform keys are ``sk-…`` and are not OpenRouter / Aliyun Token Plan."""
    key = (api_key or "").strip().lower()
    if not key.startswith("sk-"):
        return False
    if looks_like_openrouter_key(key) or looks_like_aliyun_token_plan_key(key):
        return False
    return True


def aliyun_api_base_for_key(api_key: str | None) -> str:
    """Pick the Aliyun OpenAI-compat base URL that matches the key billing mode.

    Token Plan (``sk-sp-``) must use the token-plan host; standard ``sk-`` Model Studio
    keys must use DashScope. Mixing them returns HTTP 401. OpenRouter ``sk-or-`` keys
    are not Aliyun — callers should reject them before probing.
    """
    if looks_like_aliyun_token_plan_key(api_key):
        return ALIYUN_MAAS_API_BASE
    key = (api_key or "").strip().lower()
    if key.startswith("sk-") and not looks_like_openrouter_key(key):
        # Ambiguous sk- key: prefer DashScope PAYG so a normal Model Studio key works.
        # Token Plan users must use sk-sp- (documented in the UI / probe hints).
        return ALIYUN_DASHSCOPE_API_BASE
    return ALIYUN_MAAS_API_BASE


def key_mismatch_hint(profile: str, api_key: str | None) -> str:
    """Return a short actionable hint when a saved key does not match the profile."""
    key = (api_key or "").strip()
    if not key:
        return ""
    if profile == "syra-ultra":
        if looks_like_openrouter_key(key):
            return (
                "This looks like an OpenRouter key (sk-or-…). syra-ultra now uses Aliyun: "
                "paste a Token Plan key starting with sk-sp- from the Aliyun Token Plan console "
                "(or a standard Model Studio sk- key for DashScope pay-as-you-go)."
            )
        if looks_like_aliyun_token_plan_key(key):
            return ""
        if key.lower().startswith("sk-"):
            return (
                "Using DashScope pay-as-you-go for this sk- key. "
                "For Token Plan billing, use a key that starts with sk-sp-."
            )
        return (
            "syra-ultra expects an Aliyun key: Token Plan sk-sp-… or Model Studio sk-… "
            "(not an OpenRouter / DeepSeek / Gemini key)."
        )
    if profile == "syra-base":
        if looks_like_aliyun_token_plan_key(key) or looks_like_openrouter_key(key):
            return (
                "This key is not a DeepSeek key. syra-base needs a DeepSeek API key "
                "(https://platform.deepseek.com/). Put Aliyun Token Plan keys on syra-ultra."
            )
        return ""
    if profile in {"syra-nano", "syra-havy"}:
        lower = key.lower()
        if lower.startswith("sk-") or looks_like_openrouter_key(key):
            return (
                "This looks like an OpenAI-style key. syra-nano/havy need a Google AI Studio "
                "Gemini API key (usually starts with AIza…)."
            )
        if lower.startswith("aq."):
            return (
                "This key prefix (AQ.) is unusual for Google AI Studio. Use an AI Studio "
                "Gemini API key (AIza…) with API access enabled; unrestricted keys may get HTTP 403."
            )
        return ""
    return ""


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
