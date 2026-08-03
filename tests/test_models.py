"""Tests for the first-run 9Router model setup."""

from pathlib import Path

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


@pytest.mark.asyncio
async def test_9router_model_is_onboarded_then_enabled(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import bridge_settings
    from syte.database import init_db
    from syte.main import (
        ModelConfigurationRequest,
        ModelProviderSetupRequest,
        get_models,
        save_default_model,
        save_model_provider,
    )

    await init_db()
    initial = await get_models()
    assert initial["provider"]["api_base"] == "https://9router.sycord.site/v1"
    assert initial["provider"]["api_key_set"] is False
    assert initial["model"] is None

    await save_model_provider(ModelProviderSetupRequest(api_key="9router-test-key"))
    saved = await save_default_model(ModelConfigurationRequest(
        model_name="deepseek/deepseek-r1",
        thinking_levels=[2, 3, 5],
        enabled=True,
    ))

    assert saved["model"] == {
        "profile": "9router",
        "name": "deepseek/deepseek-r1",
        "thinking_levels": [2, 3, 5],
        "enabled": True,
    }
    bridge = await bridge_settings()
    assert bridge["profiles"]["9router"]["api_base"] == "https://9router.sycord.site/v1"
    assert bridge["profiles"]["9router"]["model"] == "deepseek/deepseek-r1"
    assert bridge["profiles"]["9router"]["api_key"] == "9router-test-key"


@pytest.mark.asyncio
async def test_disabled_9router_model_is_not_available_to_runtime(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import bridge_settings
    from syte.database import init_db
    from syte.main import ModelConfigurationRequest, ModelProviderSetupRequest, save_default_model, save_model_provider

    await init_db()
    await save_model_provider(ModelProviderSetupRequest(api_key="9router-test-key"))
    await save_default_model(ModelConfigurationRequest(
        model_name="qwen/qwen3",
        thinking_levels=[1],
        enabled=False,
    ))

    bridge = await bridge_settings()
    assert bridge["profiles"]["9router"]["enabled"] is False
    assert bridge["profiles"]["9router"]["api_key"] == ""
