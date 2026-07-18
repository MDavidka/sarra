"""Tests for agent skills and preview access."""

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
async def test_write_agent_config_includes_rules_and_skills(tmp_data_dir: Path) -> None:
    from syte.agent_skills import read_access_config
    from syte.cloud_agent import (
        agent_config_path,
        agent_instruction_path,
        agent_root,
        write_agent_config,
    )
    from syte.database import create_project, get_project, init_db, set_setting, update_project

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "skills-proj",
        "name": "Skills",
        "port": 3010,
        "start_command": "",
    })
    await update_project("skills-proj", {"agent_model_profile": "syra-base"})

    project = await get_project("skills-proj")
    path = await write_agent_config(project or {})
    instruction = agent_instruction_path("skills-proj").read_text()
    root = agent_root("skills-proj")

    assert path == agent_config_path("skills-proj")
    assert "Syte website agent" in instruction
    assert "CLI tools (required)" in instruction
    assert (root / "skills" / "cli-tools.md").exists()
    assert (root / "bin" / "syte-service").exists()

    config = await read_access_config("skills-proj", root)
    assert config["custom_urls"] == []


@pytest.mark.asyncio
async def test_write_and_read_access_config(tmp_data_dir: Path) -> None:
    from syte.agent_skills import read_access_config, write_access_config
    from syte.cloud_agent import agent_root
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "access-proj",
        "name": "Access",
        "port": 3011,
        "start_command": "",
    })
    root = agent_root("access-proj")
    await write_access_config("access-proj", {"custom_urls": ["https://example.com/a"]}, root)
    config = await read_access_config("access-proj", root)
    assert config["custom_urls"] == ["https://example.com/a"]


@pytest.mark.asyncio
async def test_preview_access_status_and_logs(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import syte.preview_manager as pm

    monkeypatch.setattr(pm, "PID_DIR", tmp_data_dir / "pids")
    from syte.preview_access import run_access_action
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "preview-proj",
        "name": "Preview",
        "port": 3012,
        "start_command": "",
    })

    status = await run_access_action("preview-proj", "status")
    assert status["ok"] is True
    assert status["action"] == "status"

    logs = await run_access_action("preview-proj", "logs", lines=50)
    assert logs["ok"] is True
    assert "logs" in logs
