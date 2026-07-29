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
    monkeypatch.setenv("SYRA_NANO_API_KEY", "vertex-env-key-123456")
    resolved = await resolve_profile_api_key("syra-nano")
    assert resolved["source"] == "env"
    assert resolved["api_key"] == "vertex-env-key-123456"
    assert resolved["env_set"]
    assert not resolved["settings_set"]
    assert await profile_api_key("syra-nano") == "vertex-env-key-123456"


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


@pytest.mark.asyncio
async def test_legacy_solar_delete_removes_only_its_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import solar_runtime

    commands: list[list[str]] = []

    monkeypatch.setattr(solar_runtime.shutil, "which", lambda name: "/usr/bin/ollama" if name == "ollama" else None)
    async def fake_snapshot() -> tuple[bool, bool, bool]:
        return True, True, False

    monkeypatch.setattr(solar_runtime, "_local_snapshot", fake_snapshot)
    monkeypatch.setattr(
        solar_runtime,
        "_run_logged",
        lambda argv: (commands.append(argv) or (0, "removed")),
    )

    result = await solar_runtime.delete_solar()

    assert commands == [["ollama", "rm", "qwen2.5-coder:3b"]]
    assert result["status"] == "ollama_only"
    assert "removed" in result["message"]
