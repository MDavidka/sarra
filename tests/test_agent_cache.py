import pytest

from syte.agent_cache import cache_key, cached_agent_call, clear
from syte.config import settings


@pytest.mark.asyncio
async def test_cached_agent_call_hashes_messages_and_reuses_result(monkeypatch):
    clear()
    monkeypatch.setattr(settings, "agent_cache_ttl_s", 300.0)
    calls = 0

    async def call_llm():
        nonlocal calls
        calls += 1
        return "answer"

    messages = [{"role": "user", "content": "hello"}]
    assert cache_key(messages) == cache_key([{"content": "hello", "role": "user"}])
    assert await cached_agent_call(messages, call_llm) == "answer"
    assert await cached_agent_call(messages, call_llm) == "answer"
    assert calls == 1
    clear()
