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
