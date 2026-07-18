"""Tests for agent service control."""

from pathlib import Path

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_list_service_capabilities(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import syte.preview_manager as pm

    monkeypatch.setattr(pm, "PID_DIR", tmp_data_dir / "pids")
    from syte.agent_service import list_service_capabilities
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "svc-proj",
        "name": "Svc",
        "port": 3015,
        "start_command": "npm start",
    })

    caps = await list_service_capabilities("svc-proj")
    assert caps["ok"] is True
    assert caps["project_id"] == "svc-proj"
    assert any(a["action"] == "deploy" for a in caps["actions"])


@pytest.mark.asyncio
async def test_run_service_status(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import syte.preview_manager as pm

    monkeypatch.setattr(pm, "PID_DIR", tmp_data_dir / "pids")
    from syte.agent_service import run_service_action
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "svc-proj-2",
        "name": "Svc2",
        "port": 3016,
        "start_command": "",
    })

    result = await run_service_action("svc-proj-2", "status")
    assert result["ok"] is True
    assert result["action"] == "status"


@pytest.mark.asyncio
async def test_agent_cannot_control_production_service(tmp_data_dir: Path) -> None:
    from syte.agent_service import run_service_action
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "safe-preview", "name": "Safe", "port": 3017, "start_command": ""})

    result = await run_service_action("safe-preview", "deploy", source="agent")

    assert result["ok"] is False
    assert result["error"] == "production_action_blocked"
    assert "preview_start" in result["message"]


@pytest.mark.asyncio
async def test_agent_cannot_run_production_build(tmp_data_dir: Path) -> None:
    from syte.agent_service import run_service_action
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "safe-build", "name": "Safe", "port": 3018, "start_command": ""})

    result = await run_service_action(
        "safe-build", "run", command="npm run build", source="agent"
    )

    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert "Build commands are not allowed" in result["output"]
