"""Tests for documented agent activity stream encodings."""

import json

import pytest

from syte.log_stream import _tagged_activity_event, stream_agent_activity_tagged


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
