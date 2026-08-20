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


@pytest.mark.asyncio
async def test_bulk_catalog_only_exposes_enabled_models(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import bridge_settings, model_metadata_for_profile
    from syte.database import init_db
    from syte.main import (
        BulkModelConfigurationRequest,
        ModelConfigurationRequest,
        ModelProviderSetupRequest,
        add_models_bulk,
        get_available_models,
        save_model_provider,
    )

    await init_db()
    await save_model_provider(ModelProviderSetupRequest(api_key="9router-test-key"))
    saved = await add_models_bulk(BulkModelConfigurationRequest(models=[
        ModelConfigurationRequest(model_name="deepseek/deepseek-r1", thinking_levels=[3], enabled=True),
        ModelConfigurationRequest(model_name="qwen/qwen3", thinking_levels=[1, 2], enabled=False),
    ]))

    assert [row["name"] for row in saved["models"]] == ["deepseek/deepseek-r1", "qwen/qwen3"]
    available = await get_available_models()
    assert available["models"] == [{
        "id": saved["models"][0]["id"],
        "profile": saved["models"][0]["profile"],
        "name": "deepseek/deepseek-r1",
        "provider": "9Router",
        "thinking_levels": [3],
        "thinking_level": "medium",
    }]
    bridge = await bridge_settings()
    assert available["models"][0]["profile"] in bridge["profiles"]
    assert saved["models"][1]["profile"] not in bridge["profiles"]
    selected = await model_metadata_for_profile(available["models"][0]["profile"])
    assert selected["model"] == "deepseek/deepseek-r1"
    assert selected["api_key"] == "9router-test-key"


@pytest.mark.asyncio
async def test_same_model_can_be_added_once_per_provider(tmp_data_dir: Path) -> None:
    from fastapi import HTTPException
    from syte.database import init_db
    from syte.main import ModelConfigurationRequest, add_model, save_model_provider, ModelProviderSetupRequest

    await init_db()
    await save_model_provider(ModelProviderSetupRequest(api_key="9router-test-key"))
    first = await add_model(ModelConfigurationRequest(
        provider="OpenAI", model_name="gpt-4.1", thinking_level="xhigh",
    ))
    assert first["models"][0]["provider"] == "OpenAI"
    assert first["models"][0]["thinking_level"] == "xhigh"

    with pytest.raises(HTTPException, match="already has this model"):
        await add_model(ModelConfigurationRequest(
            provider="openai", model_name="GPT-4.1",
        ))

    second = await add_model(ModelConfigurationRequest(
        provider="Gateway B", model_name="gpt-4.1",
    ))
    assert [row["provider"] for row in second["models"]] == ["OpenAI", "Gateway B"]


def test_9router_bare_model_name_resolves_to_provider_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import model_catalog

    monkeypatch.setattr(model_catalog, "_router_cache", {
        "fetched_at": 1.0,
        "models": [
            {"id": "route-1", "name": "ag/gemini-3-flash", "provider": "Ag"},
            {"id": "route-2", "name": "ag/claude-sonnet-4-6", "provider": "Ag"},
        ],
        "ok": True,
        "error": "",
    })

    assert model_catalog.resolve_router_model_name(
        "gemini-3-flash", "ag",
    ) == "ag/gemini-3-flash"
    assert model_catalog.resolve_router_model_name(
        "ag/gemini-3-flash", "ag",
    ) == "ag/gemini-3-flash"
    assert model_catalog.resolve_router_model_name(
        "unknown-model", "ag",
    ) == "unknown-model"


@pytest.mark.asyncio
async def test_bridge_uses_provider_qualified_9router_route_for_curated_model(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import model_catalog
    from syte.cloud_agent import bridge_settings
    from syte.database import init_db
    from syte.main import ModelConfigurationRequest, ModelProviderSetupRequest, save_default_model, save_model_provider

    await init_db()
    await save_model_provider(ModelProviderSetupRequest(api_key="9router-test-key"))
    saved = await save_default_model(ModelConfigurationRequest(
        provider="ag",
        model_name="gemini-3-flash",
        thinking_levels=[3],
        enabled=True,
    ))

    async def fake_fetch_router_models(*, force: bool = False) -> bool:
        model_catalog._router_cache.update({
            "fetched_at": 1.0,
            "models": [{"id": "route-1", "name": "ag/gemini-3-flash", "provider": "Ag"}],
            "ok": True,
            "error": "",
        })
        return True

    monkeypatch.setattr(model_catalog, "fetch_router_models", fake_fetch_router_models)
    bridge = await bridge_settings()

    assert bridge["profiles"]["9router"]["model"] == "ag/gemini-3-flash"
    assert bridge["profiles"][saved["model"]["profile"]]["model"] == "ag/gemini-3-flash"
