"""Tests for Aliyun / OpenRouter / DeepSeek key shape helpers and lineup v4 migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from syte.ai_providers import (
    ALIYUN_DASHSCOPE_API_BASE,
    ALIYUN_MAAS_API_BASE,
    aliyun_api_base_for_key,
    key_mismatch_hint,
    looks_like_aliyun_token_plan_key,
    looks_like_openrouter_key,
)
from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


def test_openrouter_and_token_plan_key_shapes() -> None:
    assert looks_like_openrouter_key("sk-or-v1-abc817a")
    assert not looks_like_openrouter_key("sk-sp-token-plan")
    assert looks_like_aliyun_token_plan_key("sk-sp-abcdef")
    assert not looks_like_aliyun_token_plan_key("sk-or-v1-abc")


def test_aliyun_api_base_routes_by_key() -> None:
    assert aliyun_api_base_for_key("sk-sp-token") == ALIYUN_MAAS_API_BASE
    assert aliyun_api_base_for_key("sk-dashscope-payg") == ALIYUN_DASHSCOPE_API_BASE
    # OpenRouter keys are not remapped to DashScope; callers reject them first.
    assert aliyun_api_base_for_key("sk-or-v1-x") == ALIYUN_MAAS_API_BASE


def test_ultra_mismatch_hint_for_openrouter() -> None:
    hint = key_mismatch_hint("syra-ultra", "sk-or-v1-leftover817a")
    assert "OpenRouter" in hint
    assert "sk-sp-" in hint


@pytest.mark.asyncio
async def test_migrate_v4_moves_token_plan_off_base_over_openrouter(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import migrate_provider_lineup_keys
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    await set_setting("agent_provider_lineup_v3_migrated", "1")
    await set_setting("agent_syra_base_api_key", "sk-sp-real-token-plan")
    await set_setting("agent_syra_ultra_api_key", "sk-or-v1-old-openrouter817a")

    result = await migrate_provider_lineup_keys()
    assert result["migrated"] is True
    assert result["moved_base_to_ultra"] is True
    assert result["cleared_openrouter_ultra"] is True
    assert await get_setting("agent_syra_ultra_api_key") == "sk-sp-real-token-plan"
    assert await get_setting("agent_syra_base_api_key") == ""
    assert await get_setting("agent_openrouter_api_key_legacy") == "sk-or-v1-old-openrouter817a"
    assert await get_setting("agent_provider_lineup_v4_migrated") == "1"

    again = await migrate_provider_lineup_keys()
    assert again["migrated"] is False


@pytest.mark.asyncio
async def test_probe_fail_fast_on_openrouter_ultra(tmp_data_dir: Path) -> None:
    from syte.agent_debug import probe_profile_provider

    result = await probe_profile_provider("syra-ultra", "sk-or-v1-abc817a")
    assert result["ok"] is False
    assert "OpenRouter" in result["error"]
    assert result["probes"] == []
    assert any("Aliyun" in h or "sk-sp-" in h for h in result["hints"])


@pytest.mark.asyncio
async def test_migrate_notes_leftover_openrouter_on_ultra(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import migrate_provider_lineup_keys
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    await set_setting("agent_provider_lineup_v3_migrated", "1")
    await set_setting("agent_syra_ultra_api_key", "sk-or-v1-still-there817a")
    await set_setting("agent_syra_base_api_key", "sk-deepseek-ok")

    result = await migrate_provider_lineup_keys()
    assert result["migrated"] is True
    assert result["moved_base_to_ultra"] is False
    assert "OpenRouter" in result["note"]
    assert await get_setting("agent_syra_ultra_api_key") == "sk-or-v1-still-there817a"
