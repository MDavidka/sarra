"""Tests for async agent job queue."""

import pytest
from pathlib import Path

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_submit_agent_request_returns_immediately(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.agent_jobs import submit_agent_request
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "job-proj",
        "name": "Jobs",
        "port": 3020,
        "start_command": "",
    })

    async def fake_run(*_args, **_kwargs):
        return {"ok": True, "reply": "done"}

    monkeypatch.setattr("syte.agent_jobs._run_job", fake_run)

    result = await submit_agent_request("job-proj", "hello", source="test")
    assert result["ok"] is True
    assert result["status"] == "accepted"
    assert result["request_id"].startswith("req_")
    assert "stream_url" in result
