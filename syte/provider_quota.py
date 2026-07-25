"""Client-side quota pacing, cooldowns, and model rotation for LLM providers.

Vertex AI Express Mode enforces small shared per-project quotas and answers
HTTP 429 ``RESOURCE_EXHAUSTED`` as soon as a burst exceeds them. Retrying
immediately (or in a tight loop) makes the situation worse, so Syte:

1. paces outbound requests per model (requests/minute + max concurrency),
2. parks a model for a cooldown window after a 429 — honoring ``Retry-After``
   or ``google.rpc.RetryInfo.retryDelay`` when Google sends one,
3. rotates to a same-provider fallback model while the primary is parked.

Cooldowns are process-local and best-effort: they reduce wasted round-trips,
they are not a hard guarantee.

Reference: https://cloud.google.com/vertex-ai/generative-ai/docs/error-code-429
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

# Cooldown applied when the provider does not tell us how long to wait.
DEFAULT_QUOTA_COOLDOWN_S = 20.0
# Hard ceiling for a single cooldown window (escalates on repeated 429s).
MAX_QUOTA_COOLDOWN_S = 300.0
# A single acquire() never blocks a turn longer than this waiting on cooldown —
# the caller's retry loop prefers rotating to a fallback model instead.
MAX_COOLDOWN_WAIT_S = 15.0
# Upper bound for a single retry sleep in the caller's backoff loop.
MAX_RETRY_SLEEP_S = 45.0

_DEFAULT_MAX_CONCURRENCY = 8


class _ModelState:
    """Pacing + quota bookkeeping for one model id."""

    __slots__ = (
        "semaphore",
        "lock",
        "next_earliest_start",
        "cooldown_until",
        "quota_hits",
        "total_quota_hits",
        "last_retry_after",
        "max_concurrency",
    )

    def __init__(self, max_concurrency: int) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.lock = asyncio.Lock()
        self.next_earliest_start = 0.0
        self.cooldown_until = 0.0
        self.quota_hits = 0
        self.total_quota_hits = 0
        self.last_retry_after: float | None = None
        self.max_concurrency = max_concurrency


_states: dict[str, _ModelState] = {}


def _model_key(model: str) -> str:
    return (model or "").strip() or "unknown-model"


def _env_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        return None
    return value if value >= 0 else None


def limits_for(model: str) -> dict[str, int]:
    """Resolve requests-per-minute + max concurrency for a model.

    ``SYRA_QUOTA_RPM`` / ``SYRA_QUOTA_CONCURRENCY`` override every model, which
    is the quickest lever when a Google project has unusually tight quota.
    """
    from syte.ai_providers import model_rate_limit

    configured = model_rate_limit(model)
    rpm = _env_int("SYRA_QUOTA_RPM")
    concurrency = _env_int("SYRA_QUOTA_CONCURRENCY")
    return {
        "requests_per_minute": int(
            rpm if rpm is not None else configured.get("requests_per_minute", 0)
        ),
        "max_concurrency": max(
            1,
            int(
                concurrency
                if concurrency
                else configured.get("max_concurrency", _DEFAULT_MAX_CONCURRENCY)
            ),
        ),
    }


def _state(model: str) -> _ModelState:
    key = _model_key(model)
    state = _states.get(key)
    limits = limits_for(key)
    if state is None:
        state = _ModelState(limits["max_concurrency"])
        _states[key] = state
    return state


def rotation_enabled() -> bool:
    """Model rotation is on by default; ``SYRA_QUOTA_MODEL_ROTATION=0`` disables it.

    Rotation trades cost for availability (the fallback model may be pricier),
    so operators who care more about spend than uptime can turn it off.
    """
    raw = (os.environ.get("SYRA_QUOTA_MODEL_ROTATION") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def cooldown_remaining(model: str) -> float:
    """Seconds left before ``model`` should be called again (0 when ready)."""
    state = _states.get(_model_key(model))
    if state is None:
        return 0.0
    return max(0.0, state.cooldown_until - time.monotonic())


def is_available(model: str) -> bool:
    return cooldown_remaining(model) <= 0.0


def record_success(model: str) -> None:
    """Clear quota penalties after a successful call."""
    state = _states.get(_model_key(model))
    if state is None:
        return
    state.quota_hits = 0
    state.cooldown_until = 0.0
    state.last_retry_after = None


def record_quota_exhausted(model: str, *, retry_after: float | None = None) -> float:
    """Park ``model`` after a 429 and return the cooldown applied, in seconds.

    Consecutive hits escalate exponentially so a hard-capped project stops
    being hammered, while a one-off burst recovers quickly.
    """
    state = _state(model)
    state.quota_hits += 1
    state.total_quota_hits += 1
    if retry_after is not None and retry_after > 0:
        cooldown = float(retry_after)
    else:
        cooldown = DEFAULT_QUOTA_COOLDOWN_S * (2 ** (state.quota_hits - 1))
    cooldown = max(1.0, min(float(cooldown), MAX_QUOTA_COOLDOWN_S))
    state.cooldown_until = max(state.cooldown_until, time.monotonic() + cooldown)
    state.last_retry_after = retry_after
    return cooldown


def next_available_model(model: str) -> str | None:
    """Return a same-provider fallback model that is not cooling down."""
    if not rotation_enabled():
        return None
    from syte.ai_providers import model_fallback_chain

    for candidate in model_fallback_chain(model):
        if candidate and candidate != _model_key(model) and is_available(candidate):
            return candidate
    return None


def retry_delay(attempt: int, *, retry_after: float | None = None) -> float:
    """Backoff for retry ``attempt`` (0-based), preferring the server's hint."""
    if retry_after is not None and retry_after > 0:
        return min(float(retry_after), MAX_RETRY_SLEEP_S)
    # Deterministic exponential backoff; jitter is added by the caller.
    return min(2.0 * (2**max(0, attempt)), MAX_RETRY_SLEEP_S)


async def acquire(model: str) -> None:
    """Wait for a pacing slot for ``model``, then take a concurrency permit."""
    state = _state(model)
    await state.semaphore.acquire()
    try:
        async with state.lock:
            now = time.monotonic()
            wait = 0.0
            cooldown = state.cooldown_until - now
            if cooldown > 0:
                wait = min(cooldown, MAX_COOLDOWN_WAIT_S)
            spacing = state.next_earliest_start - now
            if spacing > 0:
                wait = max(wait, spacing)
            if wait > 0:
                await asyncio.sleep(wait)
            rpm = limits_for(model)["requests_per_minute"]
            interval = 60.0 / rpm if rpm > 0 else 0.0
            state.next_earliest_start = time.monotonic() + interval
    except BaseException:
        state.semaphore.release()
        raise


def release(model: str) -> None:
    state = _states.get(_model_key(model))
    if state is None:
        return
    try:
        state.semaphore.release()
    except ValueError:
        # Never let bookkeeping raise into a provider call path.
        pass


@asynccontextmanager
async def slot(model: str) -> AsyncIterator[None]:
    await acquire(model)
    try:
        yield
    finally:
        release(model)


_RETRY_DELAY_PATTERN = re.compile(r'"retryDelay"\s*:\s*"?(\d+(?:\.\d+)?)s"?')
_RETRY_AFTER_SECONDS_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")


def parse_retry_after_seconds(
    headers: Mapping[str, str] | None = None,
    detail: str | None = None,
) -> float | None:
    """Extract a retry hint from a ``Retry-After`` header or a Google error body.

    Handles the numeric header form and ``google.rpc.RetryInfo`` bodies
    (``"retryDelay": "37s"``). HTTP-date headers are ignored — Google's
    generative endpoints send seconds.
    """
    if headers:
        raw = ""
        for name in ("retry-after", "Retry-After", "x-ratelimit-reset-after"):
            try:
                value = headers.get(name)
            except AttributeError:
                value = None
            if value:
                raw = str(value).strip()
                break
        if raw and _RETRY_AFTER_SECONDS_PATTERN.match(raw):
            try:
                seconds = float(raw)
            except ValueError:
                seconds = 0.0
            if seconds > 0:
                return min(seconds, MAX_QUOTA_COOLDOWN_S)
    if detail:
        match = _RETRY_DELAY_PATTERN.search(detail)
        if match:
            try:
                seconds = float(match.group(1))
            except ValueError:
                return None
            if seconds > 0:
                return min(seconds, MAX_QUOTA_COOLDOWN_S)
    return None


def is_quota_detail(status_code: int | None, detail: str | None = None) -> bool:
    """True when a provider response means "quota/rate limit", not a bad request."""
    if status_code == 429:
        return True
    text = (detail or "").lower()
    return any(
        marker in text
        for marker in (
            "resource_exhausted",
            "resource has been exhausted",
            "too many requests",
            "rate limit",
            "quota exceeded",
        )
    )


def quota_snapshot() -> list[dict[str, Any]]:
    """Diagnostic view of per-model pacing state (for debug endpoints)."""
    rows: list[dict[str, Any]] = []
    for key, state in sorted(_states.items()):
        limits = limits_for(key)
        rows.append({
            "model": key,
            "requests_per_minute": limits["requests_per_minute"],
            "max_concurrency": state.max_concurrency,
            "cooldown_remaining_s": round(cooldown_remaining(key), 2),
            "consecutive_quota_hits": state.quota_hits,
            "total_quota_hits": state.total_quota_hits,
            "last_retry_after_s": state.last_retry_after,
            "available": is_available(key),
        })
    return rows


def reset_state(model: str | None = None) -> None:
    """Reset one model or all pacing state (tests / manual recovery)."""
    if model is None:
        _states.clear()
        return
    _states.pop(_model_key(model), None)
