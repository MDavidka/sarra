"""Fixed AI provider endpoints for Syra model profiles.

Each public profile is a full think+build model — there is no separate thinker.
- ``syra-nano`` — Go / Gemini 2.5 Flash
- ``syra-ultra`` — Air / Aliyun Qwen
- ``syra-havy`` — Metal / VyceAI Claude Sonnet 4.6
- ``syra-litellm`` — Proxy / LiteLLM Gateway (Docker container on port 4000)
"""

from __future__ import annotations

import json
import re
from typing import Any, NotRequired, TypedDict
from urllib.parse import urlsplit, urlunsplit

from syte.litellm_config import LITELLM_INTERNAL_API_URL

VERTEX_API_BASE = "https://aiplatform.googleapis.com/v1"
# Legacy alias kept for imports/migrations.
VERTED_API_BASE = VERTEX_API_BASE
# AI Studio OpenAI-compat (no longer used by nano/havy — kept for reference/tests).
AI_STUDIO_OPENAI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
ALIYUN_MAAS_API_BASE = (
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
)
# Pay-as-you-go DashScope OpenAI-compat (standard ``sk-`` Aliyun keys — not Token Plan).
ALIYUN_DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VYCEAI_API_BASE = "https://vyceai.com/v1"

LITELLM_API_BASE = LITELLM_INTERNAL_API_URL
# Legacy remote host used while the managed container is disabled.
NINE_ROUTER_API_BASE = "https://9router.sycord.site/v1"
# Managed mode takes ownership of 9router.sycord.site so the dashboard and API use
# the same public origin advertised to the 9Router application.
NINE_ROUTER_MANAGED_API_BASE = "https://9router.sycord.site/v1"
NINE_ROUTER_ENABLED_SETTING = "nine_router_public_enabled"
EXTERNAL_PROVIDER_SETTING = "agent_external_providers"
_EXTERNAL_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

# These endpoints use the existing OpenAI-compatible Agent request path. Anthropic
# publishes an OpenAI SDK compatibility endpoint at the supplied base URL.
EXTERNAL_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "api_base": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-6",
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "api_base": OPENROUTER_API_BASE,
        "default_model": "openai/gpt-4.1-mini",
    },
    "custom": {
        "id": "custom",
        "name": "Custom compatible API",
        "api_base": "",
        "default_model": "",
    },
}


async def resolved_nine_router_api_base() -> str:
    """Return the endpoint that Caddy currently publishes for 9Router."""
    from syte.database import get_setting

    enabled = (await get_setting(NINE_ROUTER_ENABLED_SETTING, "0")).strip() == "1"
    return NINE_ROUTER_MANAGED_API_BASE if enabled else NINE_ROUTER_API_BASE


def external_provider_profile(provider_id: str) -> str:
    """Return an opaque Agent profile for a saved external provider."""
    return f"provider:{provider_id}"


def external_provider_key_setting(provider_id: str) -> str:
    return f"agent_external_provider_{provider_id}_api_key"


def _provider_record(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    provider_id = str(raw.get("id") or "").strip().lower()
    name = " ".join(str(raw.get("name") or "").split())
    api_base = str(raw.get("api_base") or "").strip().rstrip("/")
    default_model = str(raw.get("default_model") or "").strip()
    parsed = urlsplit(api_base)
    if not _EXTERNAL_PROVIDER_ID.fullmatch(provider_id) or not name or not default_model:
        return None
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return {
        "id": provider_id,
        "name": name[:80],
        "api_base": api_base[:500],
        "default_model": default_model[:200],
    }


async def configured_external_providers() -> list[dict[str, str]]:
    """Return normalized external provider metadata without exposing API keys."""
    from syte.database import get_setting

    raw = (await get_setting(EXTERNAL_PROVIDER_SETTING, "")).strip()
    try:
        records = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        records = []
    if not isinstance(records, list):
        return []
    normalized = [_provider_record(record) for record in records]
    return [record for record in normalized if record is not None]


async def save_external_provider(
    *,
    preset: str,
    name: str,
    api_base: str,
    default_model: str,
    api_key: str,
) -> dict[str, str]:
    """Save one operator-provided external provider and its masked server-side key."""
    from syte.database import set_setting

    selected = EXTERNAL_PROVIDER_PRESETS.get(preset)
    if selected is None:
        raise ValueError("Choose a supported provider type.")
    candidate_id = selected["id"]
    candidate = _provider_record({
        "id": candidate_id,
        "name": name or selected["name"],
        "api_base": api_base or selected["api_base"],
        "default_model": default_model or selected["default_model"],
    })
    if candidate is None:
        raise ValueError("Enter an HTTPS-compatible API base URL, provider name, and model ID.")
    if not api_key.strip():
        raise ValueError("Paste an API key before saving this provider.")

    records = await configured_external_providers()
    records = [record for record in records if record["id"] != candidate["id"]]
    records.append(candidate)
    await set_setting(EXTERNAL_PROVIDER_SETTING, json.dumps(records, separators=(",", ":")))
    await set_setting(external_provider_key_setting(candidate["id"]), api_key.strip())
    return candidate


async def external_provider_options() -> list[dict[str, str]]:
    """Return selectable external model profiles with key-status metadata only."""
    from syte.database import get_setting

    options: list[dict[str, str]] = []
    for provider in await configured_external_providers():
        key_set = bool((await get_setting(external_provider_key_setting(provider["id"]), "")).strip())
        options.append({
            **provider,
            "profile": external_provider_profile(provider["id"]),
            "api_key_set": key_set,
        })
    return options


PROFILE_ORDER = (
    "syra-nano",
    "syra-ultra",
    "syra-havy",
    "syra-litellm",
)

# Default / legacy aliases (selected model handles both thinking and building).
DEFAULT_PROFILE = "syra-nano"
BUILDER_PROFILE = DEFAULT_PROFILE
THINKER_PROFILE = DEFAULT_PROFILE  # deprecated — no separate thinker

NANO_MODEL = "gemini-2.5-flash"
PRO_MODEL = "claude-sonnet-4-6"
ULTRA_MODEL = "qwen3.7-plus"

# Backward-compat aliases used by older tests/docs.
BUILDER_MODEL = NANO_MODEL
THINKER_MODEL = ULTRA_MODEL

# ---------------------------------------------------------------------------
# Quota guidance (HTTP 429 RESOURCE_EXHAUSTED avoidance)
#
# Vertex AI Express Mode / pay-as-you-go shares capacity, so Syte:
# - uses the global endpoint (aiplatform.googleapis.com — not regional)
# - paces outbound requests client-side (rpm + concurrency)
# - retries with truncated exponential backoff + full jitter
# Values are deliberately conservative; override globally with
# ``SYRA_QUOTA_RPM`` / ``SYRA_QUOTA_CONCURRENCY``.
# https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deploy/error-code-429
# ---------------------------------------------------------------------------
MODEL_RATE_LIMITS: dict[str, dict[str, int]] = {
    # Slightly below typical Express shared ceilings so bursts smooth out
    # instead of discovering the limit via 429 RESOURCE_EXHAUSTED.
    NANO_MODEL: {"requests_per_minute": 40, "max_concurrency": 2},
    PRO_MODEL: {"requests_per_minute": 15, "max_concurrency": 1},
}

# Swap targets used while a model is quota-parked. Both Vertex models accept the
# same Express Mode API key, so only the model id changes — no re-auth needed.
# Rotation favors availability over price; disable with
# ``SYRA_QUOTA_MODEL_ROTATION=0``.
MODEL_FALLBACKS: dict[str, tuple[str, ...]] = {
    NANO_MODEL: (PRO_MODEL,),
    PRO_MODEL: (NANO_MODEL,),
}


def model_rate_limit(model: str) -> dict[str, int]:
    """Client-side pacing limits for a model (empty dict-safe defaults)."""
    return dict(MODEL_RATE_LIMITS.get((model or "").strip(), {}))


def model_fallback_chain(model: str) -> tuple[str, ...]:
    """Same-provider models to try when ``model`` is quota-exhausted."""
    return MODEL_FALLBACKS.get((model or "").strip(), ())


class ProfileProvider(TypedDict):
    profile: str
    label: str
    display_name: str
    provider: str
    api_base: str
    model: str
    setting_key: str
    secret_env: str
    role: NotRequired[str]  # "fast" | "ultra" | "metal"
    local: NotRequired[bool]
    # Estimated USD per 1M tokens (public list prices; for UI guidance only).
    input_price_per_mtok: NotRequired[float]
    output_price_per_mtok: NotRequired[float]
    # Optional cost caps — omit to use Syte global defaults.
    max_tokens: NotRequired[int]
    completion_token_param: NotRequired[str]
    max_history_messages: NotRequired[int]
    max_tool_result_chars: NotRequired[int]


PROFILE_PROVIDERS: dict[str, ProfileProvider] = {
    "syra-nano": {
        "profile": "syra-nano",
        "label": "Gemini",
        "display_name": "go",
        "provider": "openai",
        "api_base": VERTEX_API_BASE,
        "model": NANO_MODEL,
        "role": "fast",
        "input_price_per_mtok": 0.25,
        "output_price_per_mtok": 1.50,
        "setting_key": "agent_syra_nano_api_key",
        "secret_env": "SYRA_NANO_API_KEY",
    },
    "syra-havy": {
        "profile": "syra-havy",
        "label": "VyceAI",
        "display_name": "metal",
        "provider": "openai",
        "api_base": VYCEAI_API_BASE,
        "model": PRO_MODEL,
        "role": "metal",
        "input_price_per_mtok": 3.00,
        "output_price_per_mtok": 15.00,
        "setting_key": "agent_syra_havy_api_key",
        "secret_env": "SYRA_HAVY_API_KEY",
    },
    "syra-ultra": {
        "profile": "syra-ultra",
        "label": "Aliyun",
        "display_name": "Air",
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
    "syra-litellm": {
        "profile": "syra-litellm",
        "label": "LiteLLM",
        "display_name": "proxy",
        "provider": "openai",
        "api_base": LITELLM_API_BASE,
        "model": "gpt-4o",  # Default model, can be changed via LiteLLM UI
        "role": "proxy",
        "input_price_per_mtok": 0.0,
        "output_price_per_mtok": 0.0,
        "setting_key": "agent_litellm_api_key",
        "secret_env": "LITELLM_API_KEY",
    },
}


NINE_ROUTER_PROFILE_SPEC: ProfileProvider = {
    "profile": "9router",
    "label": "9Router",
    "display_name": "Model",
    "provider": "openai",
    "api_base": NINE_ROUTER_API_BASE,
    "model": "",
    "role": "custom",
    "setting_key": "agent_9router_api_key",
    "secret_env": "NINE_ROUTER_API_KEY",
}


def profile_provider(profile: str) -> ProfileProvider:
    if profile == "9router":
        return NINE_ROUTER_PROFILE_SPEC
    return PROFILE_PROVIDERS.get(profile, PROFILE_PROVIDERS[DEFAULT_PROFILE])


def normalize_provider_api_base(api_base: str) -> str:
    """Normalize known provider base URL aliases before adding API paths.

    AgentRouter's OpenAI-compatible endpoint is rooted at ``/v1``.  Its
    legacy-looking ``/api/v1`` spelling is accepted by some clients as a base
    URL, but produces a 404 when the client appends ``/chat/completions``.
    Keep this correction scoped to AgentRouter so other providers' paths are
    left untouched.
    """
    base = (api_base or "").strip().rstrip("/")
    parts = urlsplit(base)
    if parts.hostname and parts.hostname.lower() in {
        "agentrouter.org",
        "www.agentrouter.org",
    }:
        if parts.path.rstrip("/") == "/api/v1":
            return urlunsplit(parts._replace(path="/v1"))
    return base


def provider_chat_completion_url(api_base: str) -> str:
    """Return the OpenAI-compatible chat completions URL for a provider."""
    return f"{normalize_provider_api_base(api_base)}/chat/completions"


def looks_like_google_auth_key(api_key: str | None) -> bool:
    """Google AI Studio Auth keys (``AQ.…``) — preferred over legacy ``AIza…`` traffic keys."""
    from syte.gemini_native import looks_like_google_auth_key as _looks

    return _looks(api_key)


def looks_like_google_ai_studio_key(api_key: str | None) -> bool:
    """Accept legacy ``AIza…`` and new ``AQ.…`` Google Gemini / Vertex Express keys."""
    from syte.gemini_native import looks_like_google_ai_studio_key as _looks

    return _looks(api_key)


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
    if profile == "syra-nano":
        lower = key.lower()
        if lower.startswith("sk-") or looks_like_openrouter_key(key):
            return (
                "This looks like an OpenAI-style key. syra-nano needs a Google Cloud "
                "Vertex AI Express Mode API key from Cloud Console → Credentials "
                "(not a DeepSeek/Aliyun/OpenRouter sk- key)."
            )
        # Express Mode keys have varied prefixes — accept any non-sk key.
        return ""
    if profile == "syra-havy":
        return "VyceAI expects an API key for the Claude Sonnet 4.6 endpoint at vyceai.com."
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
        if spec.get("local"):
            entry["local"] = True
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
        if "completion_token_param" in spec:
            entry["completion_token_param"] = str(spec["completion_token_param"])
        if "max_history_messages" in spec:
            entry["max_history_messages"] = int(spec["max_history_messages"])
        if "max_tool_result_chars" in spec:
            entry["max_tool_result_chars"] = int(spec["max_tool_result_chars"])
        rows.append(entry)
    return rows
