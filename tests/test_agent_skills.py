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
    await set_setting("agent_provider_lineup_v3_migrated", "1")
    await set_setting("agent_syra_base_api_key", "sk-test-base-key")
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
    assert "Syte website agent" in instruction or "Active Skills" in instruction or "CLI tools" in instruction
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
async def test_project_skills_can_be_enabled_and_disabled(tmp_data_dir: Path) -> None:
    from syte.agent_skills import disable_skill, enable_skill, get_project_skills
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "skill-state-proj",
        "name": "Skill state",
        "port": 3013,
        "start_command": "",
    })

    skills = await get_project_skills("skill-state-proj")
    assert len(skills) >= 3
    assert not next(skill for skill in skills if skill["id"] == "website-editing")["active"]

    enabled = await enable_skill("skill-state-proj", "website-editing", {"tone": "bold"})
    assert enabled["ok"] is True
    assert enabled["skill"]["active"] is True
    assert enabled["skill"]["parameters"] == {"tone": "bold"}

    from syte.cloud_agent import _build_syte_instruction

    instruction = await _build_syte_instruction("skill-state-proj", force_refresh=True)
    assert "## Active Skills" in instruction
    assert "Website editing" in instruction
    assert "# Website editing" in instruction
    assert "HeroUI" in instruction or "heroui" in instruction.lower()
    assert "shadcn" in instruction.lower()

    disabled = await disable_skill("skill-state-proj", "website-editing")
    assert disabled == {"ok": True, "skill_id": "website-editing", "active": False}


@pytest.mark.asyncio
async def test_custom_skills_can_be_added_edited_and_deleted(tmp_data_dir: Path) -> None:
    from syte.agent_skills import (
        add_custom_skill,
        delete_custom_skill,
        disable_skill,
        get_project_skills,
        update_custom_skill,
    )
    from syte.cloud_agent import _build_syte_instruction
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "custom-skill-proj",
        "name": "Custom skills",
        "port": 3015,
        "start_command": "",
    })

    added = await add_custom_skill(
        "custom-skill-proj",
        name="Brand voice",
        description="Product copy rules",
        content="# Brand voice\n\nPrefer short sentences. Never invent feature claims.",
        enable=True,
    )
    assert added["ok"] is True
    skill = added["skill"]
    assert skill["id"] == "brand-voice"
    assert skill["custom"] is True
    assert skill["active"] is True

    instruction = await _build_syte_instruction("custom-skill-proj", force_refresh=True)
    assert "Prefer short sentences" in instruction

    updated = await update_custom_skill(
        "custom-skill-proj",
        "brand-voice",
        content="# Brand voice\n\nUse warm, confident product language.",
    )
    assert updated["ok"] is True
    assert updated["skill"]["content"] == "# Brand voice\n\nUse warm, confident product language."

    instruction = await _build_syte_instruction("custom-skill-proj", force_refresh=True)
    assert "warm, confident" in instruction

    disabled = await disable_skill("custom-skill-proj", "brand-voice")
    assert disabled["active"] is False
    listed = await get_project_skills("custom-skill-proj")
    custom = next(s for s in listed if s["id"] == "brand-voice")
    assert custom["active"] is False
    assert custom["custom"] is True

    deleted = await delete_custom_skill("custom-skill-proj", "brand-voice")
    assert deleted == {"ok": True, "skill_id": "brand-voice", "deleted": True}
    assert not any(s["id"] == "brand-voice" for s in await get_project_skills("custom-skill-proj"))


@pytest.mark.asyncio
async def test_api_router_skills_enable_and_disable(tmp_data_dir: Path) -> None:
    from syte import api_router
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "skill-api-proj",
        "name": "Skill API",
        "port": 3014,
        "start_command": "",
    })

    listed = await api_router.api_agent_skills_list(uuid="skill-api-proj", _token={})
    assert listed["ok"] is True
    assert any(skill["id"] == "cli-tools" for skill in listed["skills"])

    enabled = await api_router.api_agent_skills_enable(
        api_router.AgentSkillEnableBody(
            uuid="skill-api-proj",
            skill_id="cli-tools",
            parameters={"mode": "strict"},
        ),
        _token={},
    )
    assert enabled["ok"] is True
    assert enabled["skill"]["active"] is True
    assert enabled["skill"]["parameters"] == {"mode": "strict"}

    disabled = await api_router.api_agent_skills_disable(
        api_router.AgentSkillDisableBody(uuid="skill-api-proj", skill_id="cli-tools"),
        _token={},
    )
    assert disabled == {"ok": True, "skill_id": "cli-tools", "active": False}


@pytest.mark.asyncio
async def test_api_router_skills_add_and_delete(tmp_data_dir: Path) -> None:
    from syte import api_router
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "skill-add-api",
        "name": "Skill add API",
        "port": 3016,
        "start_command": "",
    })

    added = await api_router.api_agent_skills_add(
        api_router.AgentSkillAddBody(
            uuid="skill-add-api",
            name="QA checklist",
            content="# QA checklist\n\nAlways verify preview on phone and desktop.",
            description="Verification habit",
            enable=True,
        ),
        _token={},
    )
    assert added["ok"] is True
    assert added["skill"]["id"] == "qa-checklist"
    assert added["skill"]["active"] is True

    deleted = await api_router.api_agent_skills_delete(
        api_router.AgentSkillDeleteBody(uuid="skill-add-api", skill_id="qa-checklist"),
        _token={},
    )
    assert deleted == {"ok": True, "skill_id": "qa-checklist", "deleted": True}


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
