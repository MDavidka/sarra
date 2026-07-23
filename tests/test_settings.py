"""Tests for settings save — Cloudflare token + preview zone."""

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
async def test_save_preview_zone_and_cloudflare_token_together(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: token must not be dropped when preview_base_domain is in the same request."""
    from syte.database import get_setting, init_db

    await init_db()
    applied = {"count": 0}

    async def fake_apply() -> tuple[bool, str]:
        applied["count"] += 1
        return True, "proxy applied"

    monkeypatch.setattr("syte.main.apply_proxy_config", fake_apply)
    monkeypatch.setattr(
        "syte.certificates.apply_cloudflare_integration",
        lambda: [],
    )
    monkeypatch.setattr(
        "syte.certificates.caddy_has_cloudflare_plugin",
        lambda: False,
    )

    from syte.main import SettingsRequest, save_settings

    res = await save_settings(SettingsRequest(
        preview_base_domain="sycord.site",
        cloudflare_api_token="cf-test-token-abc",
    ))

    assert res["ok"] is True
    assert applied["count"] == 1
    assert await get_setting("preview_base_domain") == "sycord.site"
    assert await get_setting("cloudflare_api_token") == "cf-test-token-abc"
    assert res["cloudflare_tls"]["token_configured"] is True


@pytest.mark.asyncio
async def test_save_cloudflare_token_only(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    await set_setting("preview_base_domain", "sycord.site")

    async def fake_apply() -> tuple[bool, str]:
        return True, "proxy applied"

    monkeypatch.setattr("syte.main.apply_proxy_config", fake_apply)
    monkeypatch.setattr(
        "syte.certificates.apply_cloudflare_integration",
        lambda: [],
    )
    monkeypatch.setattr(
        "syte.certificates.caddy_has_cloudflare_plugin",
        lambda: True,
    )

    from syte.main import SettingsRequest, save_settings

    res = await save_settings(SettingsRequest(cloudflare_api_token="only-token"))

    assert res["ok"] is True
    assert await get_setting("cloudflare_api_token") == "only-token"
    assert await get_setting("preview_base_domain") == "sycord.site"


@pytest.mark.asyncio
async def test_agent_settings_use_cloud_namespace(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import get_setting, init_db

    await init_db()

    async def fake_apply() -> tuple[bool, str]:
        return True, "proxy applied"

    monkeypatch.setattr("syte.main.apply_proxy_config", fake_apply)
    from syte.main import SettingsRequest, save_settings

    res = await save_settings(SettingsRequest(
        agent_default_model_profile="syra-ultra",
        agent_syra_nano_api_key="nano-key",
        agent_syra_base_api_key="base-key",
        agent_syra_havy_api_key="havy-key",
        agent_syra_ultra_api_key="fg-ultra-key",
    ))

    assert res["ok"] is True
    assert await get_setting("agent_default_model_profile") == "syra-ultra"
    assert await get_setting("agent_syra_base_api_key") == "base-key"
    assert await get_setting("agent_syra_ultra_api_key") == "fg-ultra-key"
    assert "Syte cloud" in " ".join(res["messages"])
    assert any("syra-ultra" in m for m in res["messages"])

@pytest.mark.asyncio
async def test_legacy_provider_settings_are_migrated_once(tmp_data_dir: Path) -> None:
    from syte.database import get_setting, init_db, set_setting

    await init_db()
    await set_setting("continue_syra_base_api_key", "saved-key")
    await init_db()

    assert await get_setting("agent_syra_base_api_key") == "saved-key"


@pytest.mark.asyncio
async def test_save_turso_settings_and_reset_client_cache(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import get_setting, init_db
    from syte import turso_store

    await init_db()

    async def fake_apply() -> tuple[bool, str]:
        return True, "proxy applied"

    monkeypatch.setattr("syte.main.apply_proxy_config", fake_apply)
    reset_calls = {"count": 0}
    original_reset = turso_store.reset_client_cache

    def fake_reset():
        reset_calls["count"] += 1
        original_reset()

    monkeypatch.setattr("syte.turso_store.reset_client_cache", fake_reset)

    from syte.main import SettingsRequest, save_settings

    res = await save_settings(SettingsRequest(
        turso_database_url="libsql://example.turso.io",
        turso_auth_token="secret-token",
    ))

    assert res["ok"] is True
    assert await get_setting("turso_database_url") == "libsql://example.turso.io"
    assert await get_setting("turso_auth_token") == "secret-token"
    assert reset_calls["count"] == 1


@pytest.mark.asyncio
async def test_get_settings_reports_turso_configuration(
    tmp_data_dir: Path,
) -> None:
    from syte.database import init_db, set_setting

    await init_db()
    await set_setting("turso_database_url", "libsql://example.turso.io")
    await set_setting("turso_auth_token", "secret-token")

    from syte.main import get_settings

    res = await get_settings()
    assert res["turso_database_url"] == "libsql://example.turso.io"
    assert res["turso_auth_token_set"] is True
    assert res["turso_configured"] is True
