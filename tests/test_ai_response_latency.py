"""Tests for AI response latency improvements (streaming hot path)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


@pytest.mark.asyncio
async def test_token_delta_skips_turso_and_notifies_sse(tmp_data_dir: Path) -> None:
    from syte.agent_activity import (
        HOT_STREAM_EVENT_TYPES,
        record_agent_event,
        subscribe_agent_activity,
        unsubscribe_agent_activity,
    )
    from syte.database import init_db

    await init_db()
    assert "token_delta" in HOT_STREAM_EVENT_TYPES

    queue = subscribe_agent_activity("proj-hot")
    turso = AsyncMock()
    try:
        with patch("syte.turso_store.record_event", new=turso):
            event = await record_agent_event(
                "proj-hot",
                "token_delta",
                role="assistant",
                title="Stream",
                detail="Hello",
                payload={"delta": "Hello", "session": 1},
                turso_session_id="turso-sess-1",
            )
        assert event["event_type"] == "token_delta"
        # SSE fan-out happens even though Turso is skipped.
        live = queue.get_nowait()
        assert live["id"] == event["id"]
        assert live["detail"] == "Hello"
        turso.assert_not_awaited()
    finally:
        unsubscribe_agent_activity("proj-hot", queue)


@pytest.mark.asyncio
async def test_non_hot_event_still_mirrors_turso(tmp_data_dir: Path) -> None:
    from syte.agent_activity import record_agent_event
    from syte.database import init_db

    await init_db()
    turso = AsyncMock()
    with patch("syte.turso_store.record_event", new=turso):
        await record_agent_event(
            "proj-cold",
            "processing",
            title="Processing",
            detail="accepted",
            payload={"session": 1},
            turso_session_id="turso-sess-2",
        )
    turso.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_complex_site_times_out_to_fallback() -> None:
    from syte.site_planner import fallback_site_plan, plan_complex_site

    async def slow_provider(*_args, **_kwargs):
        await asyncio.sleep(1.0)
        return {"content": "[]"}

    result = await plan_complex_site(
        "proj",
        "Build a full website with landing page with about and contact pages and blog",
        provider_completion=slow_provider,
        model={"provider": "test", "model": "test", "api_key": "x", "api_base": "http://x"},
        timeout_s=0.05,
    )
    assert result["ok"] is True
    assert result["planner"] == "fallback_timeout"
    assert result["subtasks"] == fallback_site_plan(
        "Build a full website with landing page with about and contact pages and blog"
    )


@pytest.mark.asyncio
async def test_persist_message_mirrors_turso_in_background(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _drain_turso_mirrors, _persist_message, _turso_mirror_tasks
    from syte.cloud_agent_store import ensure_session
    from syte.database import init_db

    await init_db()
    await ensure_session("proj-mirror", "syra-base")

    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_mirror(**_kwargs):
        started.set()
        await asyncio.sleep(0.05)
        finished.set()
        return True

    with patch("syte.cloud_agent._mirror_message_to_turso", new=slow_mirror):
        local_id = await _persist_message(
            "proj-mirror",
            "req-1",
            "user",
            "hello",
            session_number=1,
            turso_session_id="sess-bg",
        )
        # Returns before the mirror finishes — TTFT is not blocked.
        assert local_id > 0
        assert any(not t.done() for t in list(_turso_mirror_tasks))
        await asyncio.sleep(0)  # let the scheduled mirror start
        assert started.is_set()
        assert not finished.is_set()
        await _drain_turso_mirrors(timeout_s=2.0)
        assert finished.is_set()
