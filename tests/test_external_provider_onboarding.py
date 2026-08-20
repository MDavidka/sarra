from __future__ import annotations

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
async def test_openai_provider_onboarding_creates_selectable_server_side_profile(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import bridge_settings, is_available_model_profile
    from syte.database import init_db
    from syte.main import ModelProviderSetupRequest, get_available_models, get_models, save_model_provider

    await init_db()
    result = await save_model_provider(ModelProviderSetupRequest(
        provider_type="openai",
        api_key="sk-test-openai-key",
        name="",
        api_base="",
        default_model="",
    ))

    assert result["ok"] is True
    assert result["saved_provider"] == {
        "id": "openai",
        "name": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
    }
    assert "sk-test-openai-key" not in repr(result)

    available = await get_available_models()
    assert available["models"][-1]["profile"] == "provider:openai"
    assert available["models"][-1]["name"] == "gpt-4.1-mini"

    bridge = await bridge_settings()
    assert bridge["profiles"]["provider:openai"]["api_base"] == "https://api.openai.com/v1"
    assert bridge["profiles"]["provider:openai"]["api_key"] == "sk-test-openai-key"
    assert await is_available_model_profile("provider:openai") is True

    public_catalog = await get_models()
    assert public_catalog["external_providers"] == [{
        "id": "openai",
        "name": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
        "profile": "provider:openai",
        "api_key_set": True,
    }]
    assert "sk-test-openai-key" not in repr(public_catalog)


@pytest.mark.asyncio
async def test_custom_provider_requires_https_base_and_model(tmp_data_dir: Path) -> None:
    from fastapi import HTTPException
    from syte.database import init_db
    from syte.main import ModelProviderSetupRequest, save_model_provider

    await init_db()
    with pytest.raises(HTTPException, match="HTTPS-compatible API base URL"):
        await save_model_provider(ModelProviderSetupRequest(
            provider_type="custom",
            api_key="test-key",
            name="Local endpoint",
            api_base="http://127.0.0.1:4000/v1",
            default_model="model-a",
        ))
