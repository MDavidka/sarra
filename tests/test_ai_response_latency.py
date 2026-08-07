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
    from syte.agent_activity import drain_turso_event_mirrors, record_agent_event
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
        await drain_turso_event_mirrors(timeout_s=2.0)
    turso.assert_awaited_once()


@pytest.mark.asyncio
async def test_hot_delta_strips_verbose_metadata(tmp_data_dir: Path) -> None:
    from syte.agent_activity import list_agent_events, record_agent_event, subscribe_agent_activity, unsubscribe_agent_activity
    from syte.database import init_db

    await init_db()
    queue = subscribe_agent_activity("proj-slim")
    try:
        await record_agent_event(
            "proj-slim",
            "token_delta",
            role="assistant",
            title="Stream",
            detail="Hello world",
            payload={
                "delta": "Hello world",
                "request_id": "req-1",
                "session": 3,
                "mark_kind": "stream",
                "extra_noise": "drop-me",
            },
            turso_session_id="should-skip",
        )
        live = queue.get_nowait()
        assert live["event_type"] == "token_delta"
        assert live["detail"] == "Hello world"
        assert live["payload"]["delta"] == "Hello world"
        assert live["payload"]["request_id"] == "req-1"
        assert "mark_kind" not in live["payload"]
        assert "extra_noise" not in live["payload"]
        assert "project_id" not in live
        assert "created_at" not in live
        assert "source" not in live
    finally:
        unsubscribe_agent_activity("proj-slim", queue)

    rows = await list_agent_events("proj-slim")
    assert len(rows) == 1
    assert set(rows[0]["payload"].keys()) <= {
        "delta", "request_id", "session", "agent", "subagent_task_id",
    }


@pytest.mark.asyncio
async def test_delta_batcher_flushes_on_char_threshold(tmp_data_dir: Path) -> None:
    from syte.agent_activity import (
        StreamDeltaBatcher,
        list_agent_events,
        subscribe_agent_activity,
        unsubscribe_agent_activity,
    )
    from syte.database import init_db

    await init_db()
    queue = subscribe_agent_activity("proj-batch")
    batcher = StreamDeltaBatcher(
        "proj-batch",
        "token_delta",
        request_id="req-b",
        session=1,
        min_chars=20,
        max_chars=40,
        min_tokens=2,
        max_tokens=8,
        flush_ms=5_000,
    )
    try:
        assert await batcher.push("hello ") is None
        event = await batcher.push("world and more text here!!")
        assert event is not None
        assert event["event_type"] == "token_delta"
        assert "hello " in event["detail"]
        live = queue.get_nowait()
        assert live["payload"]["delta"]
        assert "mark_kind" not in live.get("payload", {})
    finally:
        unsubscribe_agent_activity("proj-batch", queue)
        await batcher.flush()

    events = await list_agent_events("proj-batch")
    assert len(events) >= 1


@pytest.mark.asyncio
async def test_hot_delta_batching_keeps_stream_real_time(tmp_data_dir: Path) -> None:
    """The hot path must not buffer deltas for long: idle flush <25ms, low min batch."""
    from syte.agent_activity import (
        HOT_DELTA_BATCH_FLUSH_MS,
        HOT_DELTA_BATCH_MIN_CHARS,
        HOT_DELTA_BATCH_MIN_TOKENS,
    )

    assert HOT_DELTA_BATCH_FLUSH_MS <= 25, "idle flush delay must stay sub-25ms for live streaming"
    assert HOT_DELTA_BATCH_MIN_CHARS <= 60, "min char threshold must stay small for live streaming"
    assert HOT_DELTA_BATCH_MIN_TOKENS <= 4, "min token threshold must stay small for live streaming"


@pytest.mark.asyncio
async def test_small_delta_flushes_via_short_idle_timer(tmp_data_dir: Path) -> None:
    """A small trickle of tokens must reach SSE within ~1 idle flush window, not be held."""
    from syte.agent_activity import (
        StreamDeltaBatcher,
        subscribe_agent_activity,
        unsubscribe_agent_activity,
    )
    from syte.database import init_db

    await init_db()
    queue = subscribe_agent_activity("proj-trickle")
    batcher = StreamDeltaBatcher(
        "proj-trickle",
        "token_delta",
        request_id="req-t",
        session=1,
        flush_ms=20,
    )
    try:
        # Well below any min threshold — must still flush via the idle timer.
        assert await batcher.push("hi") is None
        try:
            await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            pytest.fail("trickle delta never reached SSE subscribers")
    finally:
        unsubscribe_agent_activity("proj-trickle", queue)
        await batcher.flush()


@pytest.mark.asyncio
async def test_sse_gzip_compression_negotiated() -> None:
    from syte.agent_activity import compress_sse_frames, negotiate_sse_encoding

    assert negotiate_sse_encoding("gzip, deflate") == "gzip"
    assert negotiate_sse_encoding("br, gzip") in {"br", "gzip"}

    async def frames():
        yield "event: token_delta\ndata: {\"delta\":\"hi\"}\n\n"

    chunks: list[bytes] = []
    async for chunk in compress_sse_frames(frames(), encoding="gzip"):
        chunks.append(chunk)
    blob = b"".join(chunks)
    import gzip

    assert b"token_delta" in gzip.decompress(blob)


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
