"""Tests for persisted cloud-agent activity."""

from pathlib import Path

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


@pytest.mark.asyncio
async def test_record_and_replay_cloud_activity(tmp_data_dir: Path) -> None:
    from syte.agent_activity import list_agent_events, record_agent_event
    from syte.database import init_db

    await init_db()
    await record_agent_event(
        "proj-1", "processing", title="Processing", detail="accepted",
        payload={"request_id": "req-1"}, source="kilo-cloud",
    )
    events = await list_agent_events("proj-1")
    assert len(events) == 1
    assert events[0]["event_type"] == "processing"
    assert events[0]["payload"]["request_id"] == "req-1"
    assert events[0]["source"] == "kilo-cloud"


@pytest.mark.asyncio
async def test_workspace_activity_maps_file_change(tmp_data_dir: Path) -> None:
    from syte.agent_activity import list_agent_events, record_workspace_activity
    from syte.database import init_db

    await init_db()
    await record_workspace_activity("proj-2", "write_file", path="app/main.py", source="agent")
    event = (await list_agent_events("proj-2"))[0]
    assert event["event_type"] == "file_modified"
    assert event["payload"]["path"] == "app/main.py"


@pytest.mark.asyncio
async def test_list_agent_events_ignores_non_int_session(tmp_data_dir: Path) -> None:
    """Chat open uses session=last; non-int payload.session must not 500."""
    from syte.agent_activity import list_agent_events, record_agent_event
    from syte.database import init_db

    await init_db()
    await record_agent_event(
        "proj-session", "user_message", role="user", title="You", detail="hi",
        payload={"session": 1, "request_id": "r1"},
    )
    await record_agent_event(
        "proj-session", "status", title="legacy", detail="bad session mark",
        payload={"session": "uuid-not-int", "request_id": "r2"},
    )
    await record_agent_event(
        "proj-session", "assistant_message", role="assistant", title="Agent",
        detail="ok", payload={"session": 2, "request_id": "r3"},
    )

    last = await list_agent_events("proj-session", session="last")
    assert len(last) == 1
    assert last[0]["payload"]["session"] == 2
    assert last[0]["detail"] == "ok"

    only_first = await list_agent_events("proj-session", session=1)
    assert len(only_first) == 1
    assert only_first[0]["detail"] == "hi"


@pytest.mark.asyncio
async def test_screenshot_base64_stripped_from_activity(tmp_data_dir: Path) -> None:
    """Inline chat_image_base64 must not ship to clients (chat-open freeze risk)."""
    from syte.agent_activity import list_agent_events, record_agent_event
    from syte.database import init_db

    await init_db()
    await record_agent_event(
        "proj-shot",
        "screenshot",
        role="assistant",
        title="Screenshot /",
        detail="captured",
        payload={
            "session": 1,
            "screenshots": [
                {
                    "id": "shot-1",
                    "viewport": "desktop",
                    "image_url": "/api/projects/proj-shot/agent/screenshots/shot-1",
                    "thumb_url": "/api/projects/proj-shot/agent/screenshots/shot-1?variant=thumb",
                    "chat_image_base64": "a" * 50_000,
                    "ok": True,
                }
            ],
        },
    )
    events = await list_agent_events("proj-shot", session="last")
    assert len(events) == 1
    shot = events[0]["payload"]["screenshots"][0]
    assert "chat_image_base64" not in shot
    assert shot["thumb_url"].endswith("variant=thumb")


@pytest.mark.asyncio
async def test_activity_sse_emits_named_event_frames(tmp_data_dir: Path) -> None:
    """SSE frames use `event: {event_type}` so clients must not rely on onmessage alone."""
    from syte.agent_activity import activity_sse_generator, record_agent_event
    from syte.database import init_db

    await init_db()
    await record_agent_event(
        "proj-sse",
        "token_delta",
        role="assistant",
        title="Stream",
        detail="Hello",
        payload={"session": 1, "request_id": "req-1", "delta": "Hello"},
    )

    frames: list[str] = []
    agen = activity_sse_generator("proj-sse", since_id=0, session="last", heartbeat_seconds=0.01)
    try:
        async for frame in agen:
            frames.append(frame)
            if "event: token_delta" in frame:
                break
    finally:
        await agen.aclose()

    assert frames, "expected at least one SSE frame"
    frame = frames[0]
    assert "event: token_delta\n" in frame
    assert "data: " in frame
    assert '"event_type": "token_delta"' in frame or '"event_type":"token_delta"' in frame
