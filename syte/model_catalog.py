"""Persistent 9Router model catalog shared by the API and agent runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from syte.database import get_setting


MODEL_CATALOG_SETTING = "agent_9router_models"
DEFAULT_MODEL_PROVIDER = "9Router"

# Live router catalog (GET {api_base}/models). Cached because it is read on the
# agent hot path when resolving a selected model profile.
ROUTER_MODELS_TTL_SECONDS = 120.0
# Failures are cached for a shorter window so an outage recovers quickly without
# making every picker read pay the HTTP timeout.
ROUTER_MODELS_FAILURE_TTL_SECONDS = 30.0
# Floor between forced refreshes (the refresh endpoint is same-origin/public).
ROUTER_MODELS_MIN_REFRESH_SECONDS = 5.0
# Kept short: this runs inside a request that renders the model picker, so a
# slow router must not stall the Models tab or the chat panel.
ROUTER_MODELS_TIMEOUT_SECONDS = 5.0
_router_cache: dict[str, Any] = {
    "fetched_at": 0.0,
    "models": [],
    "ok": False,
    "error": "",
}
_log = logging.getLogger(__name__)

# One lock per event loop. A single module-level asyncio.Lock() binds to the
# first loop that uses it, so a later loop (uvicorn reload, or the fresh loop
# pytest creates per test) can block forever waiting on a lock owned by a loop
# that no longer runs.
_router_fetch_locks: dict[int, asyncio.Lock] = {}


def _router_fetch_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = id(loop)
    lock = _router_fetch_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _router_fetch_locks[key] = lock
        if len(_router_fetch_locks) > 8:
            for stale in [k for k in _router_fetch_locks if k != key][:4]:
                _router_fetch_locks.pop(stale, None)
    return lock


def normalize_provider(value: str) -> str:
    """Return a comparison-safe provider name."""
    return " ".join((value or "").split()).casefold()


def inferred_provider(name: str) -> str:
    """Keep older ``provider/model`` entries grouped usefully in the UI."""
    raw = (name or "").strip()
    if "/" not in raw:
        # A bare id like "gpt-4o" has no provider segment. Title-casing the model
        # name itself produced junk groups ("Gpt-4O") that also broke dedupe
        # against curated rows with a hand-set provider.
        return DEFAULT_MODEL_PROVIDER
    prefix = raw.split("/", 1)[0].strip()
    return prefix.title() if prefix else DEFAULT_MODEL_PROVIDER


def model_profile(model_id: str) -> str:
    """Return the stable agent-profile value for a configured 9Router model."""
    return f"9router:{model_id}"


def new_model_id(name: str, provider: str = "") -> str:
    """Create a stable, opaque identifier without exposing model names in IDs."""
    source = f"{normalize_provider(provider)}\0{name.strip().casefold()}"
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def _levels(value: Any) -> list[int]:
    if not isinstance(value, list):
        return [1, 2, 3, 4, 5, 6]
    levels = sorted({int(level) for level in value if str(level).isdigit() and 1 <= int(level) <= 6})
    return levels or [1, 2, 3, 4, 5, 6]


async def configured_models() -> list[dict[str, Any]]:
    """Return configured models, reading the former single-model settings too."""
    raw = (await get_setting(MODEL_CATALOG_SETTING, "")).strip()
    try:
        saved = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        saved = []
    rows: list[dict[str, Any]] = []
    if isinstance(saved, list):
        for item in saved:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            model_id = str(item.get("id") or "").strip()
            if name and model_id:
                rows.append({
                    "id": model_id,
                    "name": name,
                    "provider": str(item.get("provider") or inferred_provider(name)).strip(),
                    "thinking_levels": _levels(item.get("thinking_levels")),
                    "thinking_level": str(item.get("thinking_level") or "medium").strip().lower(),
                    "enabled": bool(item.get("enabled")),
                })
    if rows:
        return rows

    # Backward compatibility for the initial single-model implementation.
    legacy_name = (await get_setting("agent_9router_model_name", "")).strip()
    if not legacy_name:
        return []
    legacy_levels = (await get_setting("agent_9router_thinking_levels", "1,2,3,4,5")).split(",")
    return [{
        "id": "legacy",
        "name": legacy_name,
        "provider": inferred_provider(legacy_name),
        "thinking_levels": _levels(legacy_levels),
        "thinking_level": "medium",
        "enabled": (await get_setting("agent_9router_enabled", "0")).strip() == "1",
    }]


async def resolve_model_id(model_id: str | None) -> str | None:
    """Return the 9Router profile for a configured model id, or raise ValueError.

    When ``model_id`` is provided but does not match any configured model,
    raises ``ValueError`` with a clear message so callers can surface
    ``malformed_request`` errors to the API consumer.
    """
    if not model_id:
        return None
    for row in await configured_models():
        if row.get("id") == model_id:
            return model_profile(row["id"])
    raise ValueError(f"Invalid model id: {model_id}")


def _router_row(model_id: str) -> dict[str, Any] | None:
    """Turn one ``/v1/models`` entry into a catalog-shaped record."""
    name = str(model_id or "").strip()
    if not name:
        return None
    provider = inferred_provider(name)
    return {
        "id": new_model_id(name, provider),
        "name": name,
        "provider": provider,
        "thinking_levels": [1, 2, 3, 4, 5, 6],
        "thinking_level": "medium",
        "enabled": True,
        "source": "router",
    }


def _parse_router_models(payload: Any) -> list[dict[str, Any]]:
    """Read an OpenAI-compatible ``{"data": [{"id": ...}]}`` model list."""
    if isinstance(payload, dict):
        entries = payload.get("data")
        if not isinstance(entries, list):
            entries = payload.get("models") if isinstance(payload.get("models"), list) else []
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            raw_id = entry.get("id") or entry.get("name") or entry.get("model")
        else:
            raw_id = entry
        row = _router_row(str(raw_id or ""))
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    rows.sort(key=lambda item: (item["provider"].casefold(), item["name"].casefold()))
    return rows


def router_models_cached() -> list[dict[str, Any]]:
    """Last successfully fetched router models. Never performs I/O."""
    return [dict(row) for row in _router_cache["models"]]


def resolve_router_model_name(model_name: str, provider: str = "") -> str:
    """Return the 9Router execution route for a saved model when known.

    Earlier curated rows could save a bare model name such as
    ``gemini-3-flash`` while 9Router exposes the executable route as
    ``ag/gemini-3-flash``. Sending the bare name lets a gateway select an
    unrelated upstream, so resolve to a provider-qualified router name only
    when the cache gives an exact or unambiguous match.
    """
    requested = (model_name or "").strip()
    if not requested:
        return requested
    rows = router_models_cached()
    exact = next((str(row.get("name") or "").strip() for row in rows
                  if str(row.get("name") or "").strip().casefold() == requested.casefold()), "")
    if exact:
        return exact

    suffix = requested.rsplit("/", 1)[-1].casefold()
    candidates = [str(row.get("name") or "").strip() for row in rows
                  if str(row.get("name") or "").strip().rsplit("/", 1)[-1].casefold() == suffix]
    normalized = normalize_provider(provider)
    if normalized:
        provider_matches = [name for name in candidates
                            if normalize_provider(name.split("/", 1)[0] if "/" in name else "") == normalized]
        if len(provider_matches) == 1:
            return provider_matches[0]
    if len(candidates) == 1:
        return candidates[0]
    return requested


def router_catalog_state() -> dict[str, Any]:
    """Cache metadata for API responses (freshness / last error)."""
    fetched_at = float(_router_cache["fetched_at"] or 0.0)
    return {
        "ok": bool(_router_cache["ok"]),
        "error": str(_router_cache["error"] or ""),
        # Seconds since the last attempt. The stored value is a monotonic clock
        # reading, which is meaningless to a client on its own.
        "age_seconds": round(time.monotonic() - fetched_at, 3) if fetched_at else None,
        "count": len(_router_cache["models"]),
    }


def reset_router_models_cache() -> None:
    """Drop the cached router catalog (settings changed / tests)."""
    _router_cache.update({"fetched_at": 0.0, "models": [], "ok": False, "error": ""})


async def fetch_router_models(*, force: bool = False) -> bool:
    """Fetch the live model list from the 9Router ``/v1/models`` endpoint.

    Returns True when the cache holds a usable list. Failures are recorded and
    swallowed: the model picker falls back to the manually curated catalog so a
    router outage can never break model selection.
    """
    import httpx

    from syte.ai_providers import resolved_nine_router_api_base

    def _cached_answer() -> bool | None:
        """Return a cached verdict, or None when a fetch is warranted.

        Failures are cached too (for a shorter window). Without this, an
        unreachable router turned every model-picker read into a request that
        blocked for the full HTTP timeout.
        """
        age = time.monotonic() - float(_router_cache["fetched_at"] or 0.0)
        ttl = ROUTER_MODELS_TTL_SECONDS if _router_cache["ok"] else ROUTER_MODELS_FAILURE_TTL_SECONDS
        if age < ttl:
            return bool(_router_cache["models"])
        return None

    if force:
        # Even a forced refresh is rate limited so this cannot be used to drive
        # unbounded outbound requests.
        age = time.monotonic() - float(_router_cache["fetched_at"] or 0.0)
        if age < ROUTER_MODELS_MIN_REFRESH_SECONDS:
            return bool(_router_cache["models"])
    else:
        cached = _cached_answer()
        if cached is not None:
            return cached

    async with _router_fetch_lock():
        # Another caller may have refreshed while we waited on the lock.
        if not force:
            cached = _cached_answer()
            if cached is not None:
                return cached

        api_key = (await get_setting("agent_9router_api_key", "")).strip()
        if not api_key:
            # Without a key the router rejects the request anyway. Skip the call
            # so a fresh install (and the test suite) never blocks on network.
            _router_cache.update({
                "fetched_at": time.monotonic(),
                "ok": False,
                "error": "Save the 9Router API key to load its model list.",
            })
            return bool(_router_cache["models"])

        api_base = await resolved_nine_router_api_base()
        url = f"{api_base.rstrip('/')}/models"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=ROUTER_MODELS_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers=headers)
            response.raise_for_status()
            rows = _parse_router_models(response.json())
        except Exception as exc:  # network, auth, malformed payload
            # Keep the client-visible reason generic: these responses are served
            # on same-origin GUI routes and the raw exception text echoes the
            # router URL and whether its key was accepted.
            _router_cache.update({
                "fetched_at": time.monotonic(),
                "ok": False,
                "error": "Could not load the model list from the router.",
            })
            _log.warning("Could not fetch 9Router models from %s: %s", url, exc)
            return bool(_router_cache["models"])

        _router_cache.update({
            "fetched_at": time.monotonic(),
            "models": rows,
            "ok": True,
            "error": "" if rows else "The router returned an empty model list.",
        })
        return bool(rows)


def merge_router_models(
    catalog: list[dict[str, Any]],
    router: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine curated catalog rows with live router rows.

    Curated entries win so an explicitly disabled model stays disabled and a
    custom thinking level is preserved.
    """
    merged = [dict(row) for row in catalog]
    known = {str(row.get("id") or "") for row in merged}
    known_names = {
        (normalize_provider(str(row.get("provider") or "")),
         str(row.get("name") or "").strip().casefold())
        for row in merged
    }
    for row in router:
        key = (normalize_provider(str(row.get("provider") or "")),
               str(row.get("name") or "").strip().casefold())
        if row["id"] in known or key in known_names:
            continue
        known.add(row["id"])
        known_names.add(key)
        merged.append(dict(row))
    return merged


def enabled_model_options(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform configured records into safe, enabled-only agent options."""
    return [{
        "id": row["id"],
        "profile": model_profile(row["id"]),
        "name": row["name"],
        "provider": row.get("provider") or inferred_provider(row["name"]),
        "thinking_levels": list(row["thinking_levels"]),
        "thinking_level": row.get("thinking_level") or "medium",
    } for row in models if row.get("enabled")]
