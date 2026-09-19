"""OpenRouter Free Models Discovery and Auto-Routing Pool.

Discovers free models from OpenRouter (pricing prompt == 0 and completion == 0 or :free suffix),
caches the active list, and provides multi-model fallback routing for virtual model `openrouter:free`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("syte.ai.openrouter_free")

FALLBACK_FREE_MODELS: List[str] = [
    "qwen/qwen3.8-27b:free",
    "deepseek/deepseek-v4-flash-0731:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nex-agi/nex-n2.5-pro:free",
    "inclusionai/ling-3.0-flash-vl:free",
    "liquid/lfm-2.5-2.6b:free",
    "thinkingmachines/inkling:free",
    "cohere/north-mini-code:free",
    "dots-studio/dots-3-note-preview:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

_cached_free_models: List[Dict[str, Any]] = []
_cached_free_model_ids: List[str] = []
_last_fetch_time: float = 0.0
_FETCH_TTL_SECONDS: float = 900.0  # 15 minutes


async def discover_openrouter_free_models(force: bool = False) -> List[Dict[str, Any]]:
    """Discover all free models from OpenRouter API with in-memory caching."""
    global _cached_free_models, _cached_free_model_ids, _last_fetch_time

    now = time.time()
    if not force and _cached_free_models and (now - _last_fetch_time < _FETCH_TTL_SECONDS):
        return _cached_free_models

    url = "https://openrouter.ai/api/v1/models"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Syte-AI-Router/1.0"})
            if resp.status_code == 200:
                data = resp.json()
                models_list = data.get("data", [])
                discovered: List[Dict[str, Any]] = []
                discovered_ids: List[str] = []

                for m in models_list:
                    if not isinstance(m, dict):
                        continue
                    m_id = str(m.get("id", ""))
                    pricing = m.get("pricing") or {}
                    try:
                        p_in = float(pricing.get("prompt", 0) or 0)
                        p_out = float(pricing.get("completion", 0) or 0)
                    except (ValueError, TypeError):
                        p_in, p_out = 1.0, 1.0

                    if (p_in == 0 and p_out == 0) or ":free" in m_id:
                        entry = {
                            "id": m_id,
                            "name": m.get("name") or m_id,
                            "context_length": m.get("context_length") or 128000,
                            "description": m.get("description") or "OpenRouter Free Model",
                        }
                        discovered.append(entry)
                        discovered_ids.append(m_id)

                if discovered:
                    _cached_free_models = discovered
                    _cached_free_model_ids = discovered_ids
                    _last_fetch_time = now
                    logger.info("Discovered %d free models on OpenRouter", len(discovered))
                    return discovered
    except Exception as exc:
        logger.warning("Failed to discover OpenRouter free models: %s", exc)

    if not _cached_free_models:
        _cached_free_models = [{"id": mid, "name": mid, "context_length": 128000} for mid in FALLBACK_FREE_MODELS]
        _cached_free_model_ids = list(FALLBACK_FREE_MODELS)
        _last_fetch_time = now

    return _cached_free_models


def get_cached_free_model_ids() -> List[str]:
    """Return list of free model IDs (fallback to predefined if empty)."""
    if _cached_free_model_ids:
        return list(_cached_free_model_ids)
    return list(FALLBACK_FREE_MODELS)
