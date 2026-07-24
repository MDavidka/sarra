"""Tests for provider API key resolution (settings + env) and lineup migration."""

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
async def test_profile_api_key_falls_back_to_env(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.cloud_agent import profile_api_key, resolve_profile_api_key
    from syte.database import init_db

    await init_db()
    monkeypatch.setenv("SYRA_BASE_API_KEY", "sk-env-deepseek-123456")
    resolved = await resolve_profile_api_key("syra-base")
    assert resolved["source"] == "env"
    assert resolved["api_key"] == "sk-env-deepseek-123456"
    assert resolved["env_set"]
    assert not resolved["settings_set"]
    assert await profile_api_key("syra-base") == "sk-env-deepseek-123456"


@pytest.mark.asyncio
async def test_settings_key_wins_over_env(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.cloud_agent import resolve_profile_api_key
    from syte.database import init_db, set_setting

    await init_db()
    await set_setting("agent_syra_nano_api_key", "settings-nano-key-aaaa")
    monkeypatch.setenv("SYRA_NANO_API_KEY", "env-nano-key-bbbb")
    resolved = await resolve_profile_api_key("syra-nano")
    assert resolved["source"] == "settings"
    assert resolved["api_key"] == "settings-nano-key-aaaa"
    assert resolved["settings_set"]
    assert resolved["env_set"]


@pytest.mark.asyncio
async def test_migrate_moves_aliyun_base_key_to_ultra(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import migrate_provider_lineup_keys, resolve_profile_api_key
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_base_api_key", "aliyun-old-builder-key")
    await set_setting("agent_syra_ultra_api_key", "")

    result = await migrate_provider_lineup_keys()
    assert result["migrated"] is True
    assert result["moved_base_to_ultra"] is True
    assert await get_setting("agent_syra_base_api_key") == ""
    assert await get_setting("agent_syra_ultra_api_key") == "aliyun-old-builder-key"
    assert await get_setting("agent_provider_lineup_v4_migrated") == "1"

    ultra = await resolve_profile_api_key("syra-ultra")
    base = await resolve_profile_api_key("syra-base")
    assert ultra["api_key"] == "aliyun-old-builder-key"
    assert base["api_key"] == ""

    # Idempotent.
    again = await migrate_provider_lineup_keys()
    assert again["migrated"] is False


@pytest.mark.asyncio
async def test_migrate_moves_token_plan_sk_sp_off_base(tmp_data_dir: Path) -> None:
    """v3 left sk-sp- on base because it starts with sk-; v4 must move it."""
    from syte.cloud_agent import migrate_provider_lineup_keys, resolve_profile_api_key
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    # Simulate a host that already ran v3 migration.
    await set_setting("agent_provider_lineup_v3_migrated", "1")
    await set_setting("agent_syra_base_api_key", "sk-sp-tokenplan-abcwkCg")
    await set_setting("agent_syra_ultra_api_key", "")

    result = await migrate_provider_lineup_keys()
    assert result["migrated"] is True
    assert result["moved_base_to_ultra"] is True
    assert result["cleared_base"] is True
    assert await get_setting("agent_syra_base_api_key") == ""
    assert await get_setting("agent_syra_ultra_api_key") == "sk-sp-tokenplan-abcwkCg"
    ultra = await resolve_profile_api_key("syra-ultra")
    assert ultra["api_key"].startswith("sk-sp-")


@pytest.mark.asyncio
async def test_migrate_keeps_deepseek_looking_base_key(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import migrate_provider_lineup_keys
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_base_api_key", "sk-deepseek-already-set")
    await set_setting("agent_syra_ultra_api_key", "")
    result = await migrate_provider_lineup_keys()
    assert result["migrated"] is True
    assert result["moved_base_to_ultra"] is False
    assert await get_setting("agent_syra_base_api_key") == "sk-deepseek-already-set"
    assert await get_setting("agent_syra_ultra_api_key") == ""


@pytest.mark.asyncio
async def test_bridge_resolves_aliyun_base_from_key(tmp_data_dir: Path) -> None:
    from syte.ai_providers import ALIYUN_DASHSCOPE_API_BASE, ALIYUN_TOKEN_PLAN_BEIJING
    from syte.cloud_agent import bridge_settings
    from syte.database import init_db, set_setting

    await init_db()
    await set_setting("agent_provider_lineup_v4_migrated", "1")
    await set_setting("agent_syra_ultra_api_key", "sk-sp-beijing-key")
    bridge = await bridge_settings()
    assert bridge["profiles"]["syra-ultra"]["api_base"] == ALIYUN_TOKEN_PLAN_BEIJING

    await set_setting("agent_syra_ultra_api_key", "sk-dashscope-payg-key")
    bridge = await bridge_settings()
    assert bridge["profiles"]["syra-ultra"]["api_base"] == ALIYUN_DASHSCOPE_API_BASE


@pytest.mark.asyncio
async def test_provider_key_status_and_settings_payload(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.cloud_agent import provider_key_status
    from syte.database import init_db, set_setting
    from syte.main import get_settings

    await init_db()
    await set_setting("agent_syra_havy_api_key", "vertex-pro-key-zzzz")
    monkeypatch.setenv("SYRA_ULTRA_API_KEY", "aliyun-ultra-from-env")

    rows = await provider_key_status()
    by_profile = {row["profile"]: row for row in rows}
    assert by_profile["syra-havy"]["settings_set"] is True
    assert by_profile["syra-havy"]["source"] == "settings"
    assert by_profile["syra-ultra"]["env_set"] is True
    assert by_profile["syra-ultra"]["source"] == "env"
    assert by_profile["syra-ultra"]["env_hint"]

    payload = await get_settings()
    assert "provider_keys" in payload
    assert "provider_envs" in payload
    assert any(row["name"] == "SYRA_ULTRA_API_KEY" and row["set"] for row in payload["provider_envs"])
