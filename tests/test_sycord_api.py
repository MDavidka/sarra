"""Tests for Sycord API spec and agent integration."""

import pytest
from pathlib import Path

from syte.config import settings
from syte.sycord.integration_guide import build_backend_integration
from syte.sycord.spec import build_sycord_spec


def test_sycord_spec_includes_agent_endpoints() -> None:
    spec = build_sycord_spec("https://sycord.site")
    paths = {ep["path"] for ep in spec["endpoints"]}
    assert "https://sycord.site/sycord/api/agent_status" in paths
    assert "https://sycord.site/sycord/api/agent_change" in paths
    assert "https://sycord.site/sycord/api/agent_activity" in paths
    assert "agent_session" in spec
    assert any("agent_change" in step for step in spec["workflow"])


def test_sycord_integration_guide_includes_agent_steps() -> None:
    guide = build_backend_integration("https://sycord.site")
    names = [step["name"] for step in guide["steps"]]
    assert "Request AI code change (async)" in names
    assert "Stream agent activity (SSE)" in names
    whens = [row["when"] for row in guide["quick_reference"]]
    assert any("AI to edit code" in w for w in whens)


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_sycord_agent_change_async(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.database import create_project, init_db
    from syte.sycord import service

    await init_db()
    await create_project({
        "id": "sycord-proj",
        "name": "Sycord",
        "port": 3030,
        "start_command": "",
    })

    async def fake_communicate(project_id, message, **kwargs):
        assert project_id == "sycord-proj"
        assert message == "Add footer"
        assert kwargs["source"] == "sycord"
        assert kwargs["background"] is True
        return {
            "ok": True,
            "request_id": "req_test123",
            "status": "accepted",
            "stream_url": "/api/projects/sycord-proj/agent/activity/stream?live=1",
        }

    monkeypatch.setattr("syte.cloud_agent.communicate_with_agent", fake_communicate)

    result = await service.agent_change("sycord-proj", "Add footer", model_profile="syra-base")
    assert result["ok"] is True
    assert result["request_id"] == "req_test123"
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_sycord_agent_status_includes_stream_urls(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.database import create_project, init_db
    from syte.sycord import service

    await init_db()
    await create_project({
        "id": "sycord-status",
        "name": "Status",
        "port": 3031,
        "start_command": "",
    })

    async def fake_status(project_id, *, request_base=""):
        return {
            "agent_status": "running",
            "agent_running": True,
            "agent_port": 5204,
        }

    monkeypatch.setattr("syte.cloud_agent.get_agent_status", fake_status)

    payload = await service.agent_status("sycord-status", request_base="https://sycord.site")
    assert payload is not None
    assert payload["uuid"] == "sycord-status"
    assert "activity_stream_url" in payload
    assert "activity_text_stream_url" in payload
    assert "format=text" in payload["activity_text_stream_url"]
