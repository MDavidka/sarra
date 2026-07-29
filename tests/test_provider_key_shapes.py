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
async def test_removed_provider_migration_is_noop(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import migrate_provider_lineup_keys
    from syte.database import init_db

    await init_db()

    result = await migrate_provider_lineup_keys()
    assert result == {"migrated": False, "reason": "three_model_lineup"}

    again = await migrate_provider_lineup_keys()
    assert again == result


@pytest.mark.asyncio
async def test_probe_fail_fast_on_openrouter_ultra(tmp_data_dir: Path) -> None:
    from syte.agent_debug import probe_profile_provider

    result = await probe_profile_provider("syra-ultra", "sk-or-v1-abc817a")
    assert result["ok"] is False
    assert "OpenRouter" in result["error"]
    assert result["probes"] == []
    assert any("Aliyun" in h or "sk-sp-" in h for h in result["hints"])


@pytest.mark.asyncio
async def test_removed_provider_migration_does_not_touch_ultra(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import migrate_provider_lineup_keys
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_ultra_api_key", "sk-or-v1-still-there817a")

    result = await migrate_provider_lineup_keys()
    assert result["migrated"] is False
    assert await get_setting("agent_syra_ultra_api_key") == "sk-or-v1-still-there817a"
