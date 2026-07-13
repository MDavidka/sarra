"""Small, process-local TTL cache for safe, repeatable agent lookups."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from syte.config import settings

T = TypeVar("T")
_cache: OrderedDict[str, tuple[float, T]] = OrderedDict()


def cache_key(messages: list[Mapping[str, Any]]) -> str:
    payload = json.dumps(messages, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clear() -> None:
    _cache.clear()


def get_or_set(key: str, factory: Callable[[], T]) -> T:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < settings.agent_cache_ttl_s:
        _cache.move_to_end(key)
        return hit[1]
    if hit:
        _cache.pop(key, None)

    value = factory()
    _cache[key] = (now, value)
    _cache.move_to_end(key)
    while len(_cache) > max(1, settings.agent_cache_max_entries):
        _cache.popitem(last=False)
    return value


async def cached_agent_call(
    messages: list[Mapping[str, Any]],
    call_llm: Callable[[], Any],
) -> T:
    """Cache an async, read-only model call by its canonical message hash."""
    key = cache_key(messages)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < settings.agent_cache_ttl_s:
        _cache.move_to_end(key)
        return hit[1]
    if hit:
        _cache.pop(key, None)

    value = await call_llm()
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > max(1, settings.agent_cache_max_entries):
        _cache.popitem(last=False)
    return value
