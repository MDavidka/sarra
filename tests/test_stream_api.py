"""Tests for the optimized SSE streaming stack.

Covers ``syte/sse_core.py`` (frame encoding, hot-delta batching,
backpressure), the rewritten ``session_manager`` fan-out, the dedicated
``/api/stream`` window, and the httpx-based provider streams.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from syte import stream_api
from syte.ai import providers
from syte.ai.session_manager import ProjectAISession
from syte.auth import verify_operator_session_or_token
from syte.sse_core import RETRY_FRAME, encode_sse_frame, stream_gap_frame


# ---------------------------------------------------------------------------
# sse_core
# ---------------------------------------------------------------------------


def test_encode_sse_frame_shape() -> None:
    frame = encode_sse_frame({"event": "token_delta", "delta": "hi"}, event_id=7)
    text = frame.decode("utf-8")
    assert text.startswith("id: 7\nevent: token_delta\ndata: ")
    assert text.endswith("\n\n")
    payload = json.loads(text.split("data: ", 1)[1])
    assert payload["event"] == "token_delta" and payload["delta"] == "hi"


def test_stream_gap_frame_carries_backfill_hint() -> None:
    frame = stream_gap_frame(dropped=3, last_id=42, reason="backpressure")
    payload = json.loads(frame.decode().split("data: ", 1)[1])
    assert payload == {
        "event": "stream_gap",
        "dropped": 3,
        "last_id": 42,
        "reason": "backpressure",
    }


# ---------------------------------------------------------------------------
# session manager fan-out
# ---------------------------------------------------------------------------


def _collect(frames: List[bytes]) -> str:
    return b"".join(frames).decode("utf-8")


async def _drain(session: ProjectAISession, until: bytes, timeout: float = 2.0) -> List[bytes]:
    frames: List[bytes] = []

    async def consume() -> None:
        async for frame in session.subscribe(since_id=0):
            frames.append(frame)
            if until in frame:
                return

    await asyncio.wait_for(consume(), timeout=timeout)
    return frames


def test_hot_deltas_coalesce_and_cold_events_keep_order() -> None:
    async def scenario() -> None:
        session = ProjectAISession("p")
        task = asyncio.create_task(_drain(session, b'"done"'))
        await asyncio.sleep(0.02)

        session.add_event({"event": "token_delta", "delta": "a"})
        session.add_event({"event": "token_delta", "delta": "b"})
        await asyncio.sleep(0.03)  # idle window elapses -> merged flush
        session.add_event({"event": "token_delta", "delta": "c"})
        session.add_event({"event": "done", "reply": "abc"})

        text = _collect(await task)
        assert text.startswith(RETRY_FRAME.decode())
        assert text.count("event: token_delta") == 2, "expected 2 merged delta frames"
        assert '"delta":"ab"' in text and '"batch_count":2' in text
        assert '"delta":"c"' in text
        # Ordering: deltas arrive before the cold 'done' that followed them.
        assert text.index('"delta":"c"') < text.index('"done"')
        # Monotonic ids on every frame.
        ids = [int(line.split(": ")[1]) for line in text.splitlines() if line.startswith("id: ")]
        assert ids == sorted(ids) and len(ids) == 3

    asyncio.run(scenario())


def test_since_id_replay_returns_cold_events_only() -> None:
    session = ProjectAISession("p")
    session.add_event({"event": "token_delta", "delta": "hot"})
    session.add_event({"event": "status", "message": "transient"})  # excluded from window
    session.add_event({"event": "tool_call_start", "tool_name": "syte_read_file"})
    session.add_event({"event": "done", "reply": "x"})

    events = session.get_events_since(0)
    types = [e["event"] for e in events]
    assert types == ["tool_call_start", "done"], "hot deltas + status must not enter the replay window"
    assert [e["id"] for e in events] == [3, 4]
    assert session.get_events_since(3) == [e for e in events if e["id"] == 4]


def test_backpressure_drops_oldest_and_emits_stream_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    import syte.ai.session_manager as sm

    monkeypatch.setattr(sm, "SUBSCRIBER_QUEUE_SIZE", 2)

    async def scenario() -> None:
        session = sm.ProjectAISession("p")
        frames: List[bytes] = []
        got_gap = asyncio.Event()

        async def consume() -> None:
            async for frame in session.subscribe(since_id=0):
                frames.append(frame)
                if b"stream_gap" in frame:
                    got_gap.set()
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.02)
        for i in range(40):
            session.add_event({"event": "status", "message": f"s{i}"})
        await asyncio.wait_for(got_gap.wait(), timeout=2)
        task.cancel()

        text = _collect(frames)
        assert "stream_gap" in text
        assert '"reason":"backpressure"' in text

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# /api/stream window
# ---------------------------------------------------------------------------


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(stream_api.router)
    app.dependency_overrides[verify_operator_session_or_token] = lambda: {"id": "tester"}
    return TestClient(app)


def test_stream_catalog_and_health() -> None:
    client = _build_client()
    catalog = client.get("/api/stream").json()
    assert catalog["ok"] is True
    assert "chat" in catalog["endpoints"] and "events" in catalog["endpoints"]
    assert catalog["hot_path"]["batched_events"] == ["token_delta", "thought_delta"]
    assert client.get("/api/stream/health").json() == {"ok": True, "stream": "alive"}


def test_events_endpoint_replays_cold_window() -> None:
    client = _build_client()
    session = stream_api.session_manager.get_or_create_session("global")
    session.add_event({"event": "tool_call_start", "tool_name": "syte_read_file"})
    session.add_event({"event": "done", "reply": "hi"})

    with client.stream("GET", "/api/stream/projects/global/events?since_id=0") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        collected: List[bytes] = []
        for chunk in response.iter_bytes():
            collected.append(chunk)
            joined = _collect(collected)
            if '"done"' in joined:
                break
    joined = _collect(collected)
    assert joined.startswith("retry: ")
    assert "event: tool_call_start" in joined and "event: done" in joined


def test_activity_poll_mirror() -> None:
    client = _build_client()
    session = stream_api.session_manager.get_or_create_session("global")
    before = session.last_event_id
    session.add_event({"event": "tool_call_result", "tool_name": "syte_read_file", "result": {"ok": True}})

    body = client.get("/api/stream/projects/global/activity?since_id=0").json()
    assert body["ok"] is True and body["last_id"] >= before + 1
    assert body["events"][-1]["event"] == "tool_call_result"

    incremental = client.get(f"/api/stream/projects/global/activity?since_id={before}").json()
    assert [e["event"] for e in incremental["events"]] == ["tool_call_result"]


def test_stream_paths_avoid_retired_surface_terms() -> None:
    paths = {route.path for route in stream_api.router.routes}
    forbidden = ("agent", "model", "provider", "router", "syra", "litellm", "ai.json")
    assert not [p for p in paths if any(term in p.lower() for term in forbidden)]


# ---------------------------------------------------------------------------
# httpx provider streams
# ---------------------------------------------------------------------------


def _patch_provider_client(handler: Any) -> None:
    providers._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    providers._http_client_loop_id = id(asyncio.get_running_loop())


def test_openai_compatible_streaming_tokens_and_tool_calls() -> None:
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"syte_read_file","arguments":"{\\"pa"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\":\\"a.ts\\"}"}}]}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        assert payload["stream"] is True and payload["tools"]
        return httpx.Response(200, content=sse_body)

    async def scenario() -> None:
        _patch_provider_client(handler)
        client = providers.UnifiedAIClient(provider="openai", model="gpt-4o", api_key="sk-test")
        chunks = [c async for c in client.stream_chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])]
        kinds = [c["type"] for c in chunks]
        assert kinds == ["token", "token", "thought", "tool_call"]
        assert chunks[-1]["name"] == "syte_read_file"
        assert json.loads(chunks[-1]["arguments"]) == {"path": "a.ts"}

    asyncio.run(scenario())


def test_provider_http_error_maps_to_error_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=json.dumps({"error": {"message": "bad key"}}).encode())

    async def scenario() -> None:
        _patch_provider_client(handler)
        client = providers.UnifiedAIClient(provider="openai", model="gpt-4o", api_key="sk-test")
        chunks = [c async for c in client.stream_chat([{"role": "user", "content": "hi"}])]
        assert chunks[0]["type"] == "error"
        assert "bad key" in chunks[0]["content"] and "401" in chunks[0]["content"]

    asyncio.run(scenario())


def test_anthropic_streaming_text_and_thinking() -> None:
    body = (
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi "}}\n\n'
        b'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"hm"}}\n\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"there"}}\n\n'
        b'data: {"type":"message_stop"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "sk-ant-test"
        return httpx.Response(200, content=body)

    async def scenario() -> None:
        _patch_provider_client(handler)
        client = providers.UnifiedAIClient(provider="anthropic", model="claude-x", api_key="sk-ant-test")
        chunks = [c async for c in client.stream_chat([{"role": "user", "content": "hi"}])]
        assert [(c["type"], c.get("content")) for c in chunks] == [
            ("token", "Hi "),
            ("thought", "hm"),
            ("token", "there"),
        ]

    asyncio.run(scenario())
