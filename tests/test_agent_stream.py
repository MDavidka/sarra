"""Tests for documented agent activity stream encodings."""

import json

import pytest


def _parse_tagged(record: str) -> tuple[str, dict]:
    tag, encoded = record.split("]<", 1)
    return tag.removeprefix("["), json.loads(encoded.removesuffix(">"))


@pytest.mark.parametrize(
    ("event_type", "payload", "expected_tag"),
    [
        ("request_started", {"request_id": "req-1"}, "start"),
        ("processing", {"request_id": "req-1"}, "processing"),
        ("thinking", {"request_id": "req-1"}, "think"),
        ("token_delta", {"request_id": "req-1", "delta": "Hi"}, "delta"),
        (
            "command_run",
            {"request_id": "req-1", "phase": "started", "tool": "terminal"},
            "tool:start",
        ),
        (
            "command_output",
            {"request_id": "req-1", "phase": "finished", "tool": "terminal"},
            "tool:result",
        ),
        ("request_completed", {"request_id": "req-1", "reply": "Done"}, "done"),
        (
            "request_failed",
            {"request_id": "req-1", "error": "provider_error"},
            "error",
        ),
    ],
)
def test_tagged_activity_event_vocabulary(
    event_type: str,
    payload: dict,
    expected_tag: str,
) -> None:
    from syte.log_stream import _tagged_activity_event

    record = _tagged_activity_event({
        "id": 42,
        "event_type": event_type,
        "role": "assistant",
        "title": "Example",
        "detail": "detail",
        "payload": payload,
        "created_at": "2026-07-12T17:00:00+00:00",
    })

    tag, body = _parse_tagged(record)
    assert tag == expected_tag
    assert body["id"] == 42
    assert body["request_id"] == "req-1"
    assert body["type"] == event_type
    if event_type == "token_delta":
        assert body["text"] == "Hi"
    if event_type == "request_completed":
        assert body["text"] == "Done"


@pytest.mark.asyncio
async def test_tagged_activity_stream_is_valid_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.log_stream import stream_agent_activity_tagged

    async def fake_stream(*args, **kwargs):
        yield 'data: {"type":"session","text":"Live agent activity stream"}\n\n'
        yield (
            'data: {"type":"activity","event":{"id":7,'
            '"event_type":"thinking","role":"assistant","title":"Plan",'
            '"detail":"Inspect first","payload":{"request_id":"req-7"}}}\n\n'
        )
        yield 'data: {"type":"ping","since_id":7}\n\n'

    monkeypatch.setattr("syte.log_stream.stream_agent_activity", fake_stream)
    chunks = [
        chunk
        async for chunk in stream_agent_activity_tagged(
            "proj-1",
            live_only=True,
        )
    ]

    assert chunks[0].startswith("data: [session]<")
    assert chunks[1].startswith("data: [think]<")
    assert '"request_id":"req-7"' in chunks[1]
    assert chunks[2] == 'data: [ping]<{"since_id":7}>\n\n'


@pytest.mark.asyncio
async def test_tagged_activity_stream_applies_type_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.log_stream import stream_agent_activity_tagged

    async def fake_stream(*args, **kwargs):
        yield (
            'data: {"type":"activity","event":{"id":1,'
            '"event_type":"thinking","detail":"Plan","payload":{}}}\n\n'
        )
        yield (
            'data: {"type":"activity","event":{"id":2,'
            '"event_type":"command_run","detail":"pytest","payload":{}}}\n\n'
        )

    monkeypatch.setattr("syte.log_stream.stream_agent_activity", fake_stream)
    chunks = [
        chunk
        async for chunk in stream_agent_activity_tagged(
            "proj-1",
            type_filter=["thinking"],
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].startswith("data: [think]<")


def test_format_marked_activity_event_line() -> None:
    from syte.log_stream import format_marked_activity_event

    line = format_marked_activity_event({
        "event_type": "tool_call_started",
        "detail": '{"path":"app/page.tsx"}',
        "payload": {
            "session": 1,
            "message_index": 2,
            "mark_status": "g",
            "mark_kind": "tool",
            "tool": "read_file",
        },
    })
    assert line == 'S1002(g)-<tool>read_file {"path":"app/page.tsx"}'

    plan = format_marked_activity_event({
        "event_type": "thinking",
        "title": "Plan",
        "detail": "Inspect first",
        "payload": {
            "session": 2,
            "message_index": 3,
            "mark_status": "g",
            "mark_kind": "plan",
        },
    })
    assert plan == "S2003(g)-<plan>Inspect first"


@pytest.mark.asyncio
async def test_marked_activity_stream_emits_boot_session_and_marks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.log_stream import stream_agent_activity_marked

    async def fake_stream(*args, **kwargs):
        yield (
            'data: {"type":"activity","event":{"id":1,"event_type":"request_started",'
            '"detail":"Add dark mode","payload":{"session":1,"message_index":1,'
            '"mark_status":"d","mark_kind":"user","session_started":true}}}\n\n'
        )
        yield (
            'data: {"type":"activity","event":{"id":2,"event_type":"tool_call_started",'
            '"detail":"{}","payload":{"session":1,"message_index":2,"mark_status":"g",'
            '"mark_kind":"tool","tool":"read_file"}}}\n\n'
        )
        yield (
            'data: {"type":"activity","event":{"id":3,"event_type":"request_started",'
            '"detail":"Next","payload":{"session":2,"message_index":1,"mark_status":"d",'
            '"mark_kind":"user","session_started":true}}}\n\n'
        )
        yield (
            'data: {"type":"activity","event":{"id":4,"event_type":"thinking",'
            '"detail":"Updating header","payload":{"session":2,"message_index":3,'
            '"mark_status":"g","mark_kind":"plan"}}}\n\n'
        )
        yield 'data: {"type":"ping","since_id":4}\n\n'

    monkeypatch.setattr("syte.log_stream.stream_agent_activity", fake_stream)
    chunks = [
        chunk.removeprefix("data: ").rstrip("\n")
        async for chunk in stream_agent_activity_marked("proj-1", live_only=True)
    ]

    assert chunks[0] == "[boot]"
    assert chunks[1] == "[session1]"
    assert chunks[2] == "S1001(d)-<user>Add dark mode"
    assert chunks[3] == "S1002(g)-<tool>read_file {}"
    assert chunks[4] == "[session2]"
    assert chunks[5] == "S2001(d)-<user>Next"
    assert chunks[6] == "S2003(g)-<plan>Updating header"
    assert chunks[7] == '[ping]<{"since_id":4}>'
