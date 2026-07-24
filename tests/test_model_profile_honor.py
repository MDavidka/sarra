"""Regression: selected model profile must not be auto-swapped for short prompts."""

from __future__ import annotations

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
async def test_short_message_keeps_selected_ultra_profile(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.cloud_agent import communicate_with_agent
    from syte.database import create_project, init_db, set_setting, update_project

    await init_db()
    await set_setting("agent_provider_lineup_v3_migrated", "1")
    await set_setting("agent_provider_lineup_v4_migrated", "1")
    await set_setting("agent_syra_ultra_api_key", "aliyun-ultra-test-key")
    project = await create_project({"id": "keep-ultra", "name": "keep-ultra", "port": 3101, "start_command": ""})
    await update_project(project["id"], {"agent_model_profile": "syra-ultra"})

    seen = {}

    async def fake_impl(project_id, message, **kwargs):
        seen["model_profile"] = kwargs.get("model_profile")
        return {"ok": True, "reply": "hi", "request_id": "req-1"}

    monkeypatch.setattr("syte.cloud_agent._communicate_with_agent_impl", fake_impl)

    # No explicit profile on the request — must keep project's syra-ultra,
    # not auto-route "hey" to syra-nano.
    result = await communicate_with_agent(project["id"], "hey", background=False)
    assert result["ok"] is True
    assert seen["model_profile"] == "syra-ultra"
    assert result["model_routing"]["auto_applied"] is False
    assert result["model_routing"]["suggested_profile"] == "syra-nano"
    assert result["model_routing"]["effective_profile"] == "syra-ultra"


@pytest.mark.asyncio
async def test_explicit_profile_beats_suggestion(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.cloud_agent import communicate_with_agent
    from syte.database import create_project, init_db, set_setting

    await init_db()
    await set_setting("agent_provider_lineup_v3_migrated", "1")
    await set_setting("agent_provider_lineup_v4_migrated", "1")
    await set_setting("agent_syra_base_api_key", "sk-deepseek-test")
    project = await create_project({"id": "explicit-base", "name": "explicit-base", "port": 3102, "start_command": ""})

    seen = {}

    async def fake_impl(project_id, message, **kwargs):
        seen["model_profile"] = kwargs.get("model_profile")
        return {"ok": True, "reply": "ok", "request_id": "req-2"}

    monkeypatch.setattr("syte.cloud_agent._communicate_with_agent_impl", fake_impl)

    result = await communicate_with_agent(
        project["id"], "hey", model_profile="syra-base", background=False,
    )
    assert seen["model_profile"] == "syra-base"
    assert result["model_routing"]["auto_applied"] is False


def test_sanitize_api_key_strips_quotes_and_bearer() -> None:
    from syte.cloud_agent import sanitize_api_key

    assert sanitize_api_key('  "sk-abc"  ') == "sk-abc"
    assert sanitize_api_key("Bearer sk-abc") == "sk-abc"
    assert sanitize_api_key("`sk-abc`") == "sk-abc"
