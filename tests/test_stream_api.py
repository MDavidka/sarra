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
    assert payload["event"] == "stream_gap"
    assert payload["dropped"] == 3
    assert payload["last_id"] == 42
    assert payload["reason"] == "backpressure"


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
    from syte import api_router
    app.include_router(stream_api.router)
    app.include_router(api_router.router, prefix="/api")
    app.include_router(api_router.router, prefix="/sycord/api")
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
    forbidden = ("agent", "router", "syra", "litellm", "ai.json")
    assert not [p for p in paths if any(term in p.lower() for term in forbidden)]


# ---------------------------------------------------------------------------
# Models Catalog & Command Stream Tests
# ---------------------------------------------------------------------------


def test_models_endpoint_json_and_stream() -> None:
    client = _build_client()

    # 1. JSON format on /api/models (Sycord-pages contract)
    res = client.get("/api/models")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert isinstance(data["models"], list)
    assert len(data["models"]) > 0
    assert "available_models" in data
    assert "saved_providers" in data

    # 2. SSE streaming format on /api/models?stream=true
    with client.stream("GET", "/api/models?stream=true") as stream_res:
        assert stream_res.status_code == 200
        assert stream_res.headers["content-type"].startswith("text/event-stream")
        chunks = [chunk.decode("utf-8") for chunk in stream_res.iter_bytes()]
        joined = "".join(chunks)
        assert "retry: 2000" in joined
        assert "data: " in joined
        assert "data: [DONE]" in joined

    # 3. /api/models?active_only=true returns single model from AI tab
    res_active = client.get("/api/models?active_only=true")
    assert res_active.status_code == 200
    data_active = res_active.json()
    assert len(data_active["models"]) == 1
    assert data_active["models"][0]["active"] is True
    assert data_active["models"][0]["is_active_in_ai_tab"] is True

    # 4. /sycord/api/models works without 404
    res_sycord = client.get("/sycord/api/models")
    assert res_sycord.status_code == 200
    assert res_sycord.json()["ok"] is True

    # 5. /api/stream/models JSON and SSE
    res_stream_models = client.get("/api/stream/models")
    assert res_stream_models.status_code == 200
    assert res_stream_models.json()["ok"] is True



def test_stream_project_command() -> None:
    client = _build_client()
    with client.stream("POST", "/api/stream/projects/global/command", json={"command": "echo 'hello better sse'"}) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        chunks = [chunk.decode("utf-8") for chunk in res.iter_bytes()]
        joined = "".join(chunks)
        assert "event: command_start" in joined
        assert "event: command_output" in joined
        assert "hello better sse" in joined
        assert "event: command_end" in joined
        assert "event: done" in joined


def test_new_stream_event_types_validation() -> None:
    from syte.ai.events import (
        WorkingStatusEvent,
        CommandStartEvent,
        CommandOutputEvent,
        CommandEndEvent,
        ErrorLogEvent,
        AskQuestionEvent,
        parse_ai_event,
    )

    # is_working
    w_evt = parse_ai_event({"event": "is_working", "is_working": True, "activity": "Compiling tests"})
    assert isinstance(w_evt, WorkingStatusEvent)
    assert w_evt.is_working is True

    # command_start
    cs_evt = parse_ai_event({"event": "command_start", "command": "npm run build", "cwd": "app"})
    assert isinstance(cs_evt, CommandStartEvent)
    assert cs_evt.command == "npm run build"

    # command_output
    co_evt = parse_ai_event({"event": "command_output", "text": "Build succeeded\n", "stream": "stdout"})
    assert isinstance(co_evt, CommandOutputEvent)
    assert co_evt.stream == "stdout"

    # command_end
    ce_evt = parse_ai_event({"event": "command_end", "command": "npm run build", "exit_code": 0, "duration_ms": 120.5})
    assert isinstance(ce_evt, CommandEndEvent)
    assert ce_evt.exit_code == 0

    # error_log
    el_evt = parse_ai_event({"event": "error_log", "level": "error", "message": "Syntax error in file", "source": "linter"})
    assert isinstance(el_evt, ErrorLogEvent)
    assert el_evt.level == "error"

    # ask_question
    aq_evt = parse_ai_event({"event": "ask_question", "question": "Would you like to install tailwind?", "options": ["yes", "no"]})
    assert isinstance(aq_evt, AskQuestionEvent)
    assert len(aq_evt.options) == 2


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


# ---------------------------------------------------------------------------
# Better-SSE Session, Channel, and Subtab Streaming Tests
# ---------------------------------------------------------------------------


def test_better_sse_session_and_channel_pub_sub() -> None:
    from syte.sse_core import Channel, Session

    channel = Channel("subtab:test_subtab")
    session1 = Session()
    session2 = Session()

    channel.register(session1)
    channel.register(session2)

    assert channel.session_count == 2
    assert session1 in channel.sessions and session2 in channel.sessions

    # Broadcast event
    channel.broadcast({"message": "deployment updated"}, event="deploy_status")

    assert not session1.queue.empty()
    assert not session2.queue.empty()

    frame1, evt1 = session1.queue.get_nowait()
    frame2, evt2 = session2.queue.get_nowait()

    assert evt1["event"] == "deploy_status"
    assert evt1["message"] == "deployment updated"
    assert frame1 == frame2

    # Deregister
    session1.close()
    assert channel.session_count == 1
    assert session1 not in channel.sessions


def test_better_sse_subtab_endpoint_stream_and_broadcast() -> None:
    async def scenario() -> None:
        from syte.sse_core import channel_hub

        # Broadcast event to subtab 'build'
        channel_hub.broadcast_to_subtab(
            "global",
            "build",
            {"event": "build_step", "step": "compiling assets", "progress": 50},
        )

        channel = channel_hub.get_subtab_channel("global", "build")
        session = channel_hub.create_session()
        channel.register(session, since_id=0, replay=True)

        frames: List[bytes] = []
        async def consume():
            async for frame in session.iterate(since_id=0, replay=True):
                frames.append(frame)
                if b"compiling assets" in frame:
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        joined = _collect(frames)
        assert "event: build_step" in joined
        assert "compiling assets" in joined
        assert "retry: 2000" in joined

    asyncio.run(scenario())


def test_better_sse_channels_list_and_catalog() -> None:
    client = _build_client()

    # Channels list
    channels_res = client.get("/api/stream/channels")
    assert channels_res.status_code == 200
    data = channels_res.json()
    assert data["ok"] is True
    assert isinstance(data["channels"], list)

    # Stream catalog includes better_sse metadata
    catalog = client.get("/api/stream").json()
    assert catalog["ok"] is True
    assert catalog["library"] == "better-sse"
    assert "subtab_stream" in catalog["endpoints"]
    assert "subtab_broadcast" in catalog["endpoints"]
    assert "general" in catalog["better_sse"]["supported_subtabs"]
    assert "ai" in catalog["better_sse"]["supported_subtabs"]


def test_tools_aliases_and_execution(tmp_path: Any) -> None:
    from syte.ai.tools import execute_syte_tool
    import asyncio

    project = {"id": "test_proj", "name": "Test Project"}
    
    async def run_scenario():
        # 1. create_folder
        res_folder = await execute_syte_tool(project, "create_folder", {"path": "src/components"})
        assert res_folder["ok"] is True

        # 2. write_file
        res_write = await execute_syte_tool(project, "write_file", {
            "path": "src/components/Button.tsx",
            "content": "export function Button() { return <button>Click</button>; }"
        })
        assert res_write["ok"] is True

        # 3. read_file
        res_read = await execute_syte_tool(project, "read_file", {"path": "src/components/Button.tsx"})
        assert res_read["ok"] is True
        assert "export function Button" in res_read["content"]

        # 4. edit_file
        res_edit = await execute_syte_tool(project, "edit_file", {
            "path": "src/components/Button.tsx",
            "old_text": "<button>Click</button>",
            "new_text": "<button>Click Me</button>",
        })
        assert res_edit["ok"] is True

        # 5. write_files (bulk)
        res_bulk_write = await execute_syte_tool(project, "write_files", {
            "files": [
                {"path": "src/a.ts", "content": "export const a = 1;"},
                {"path": "src/b.ts", "content": "export const b = 2;"},
            ]
        })
        assert res_bulk_write["ok"] is True
        assert res_bulk_write["files_written"] == 2

        # 6. read_files (bulk)
        res_bulk_read = await execute_syte_tool(project, "read_files", {
            "paths": ["src/a.ts", "src/b.ts"]
        })
        assert res_bulk_read["ok"] is True
        assert "src/a.ts" in res_bulk_read["files"]

        # 7. detect_framework
        res_detect = await execute_syte_tool(project, "detect_framework", {})
        assert res_detect["ok"] is True

    asyncio.run(run_scenario())


