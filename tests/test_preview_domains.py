"""Tests for production/preview domain resolution."""

import pytest

from syte.preview_domains import resolve_production_domain, resolve_preview_zone


@pytest.mark.asyncio
async def test_resolve_production_domain_auto_assign(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_setting(key: str, default: str = "") -> str:
        if key == "preview_base_domain":
            return "sycord.site"
        return default

    monkeypatch.setattr("syte.preview_domains.get_setting", fake_get_setting)

    domain = await resolve_production_domain({"name": "My Site", "id": "abc", "domain": None})
    assert domain == "my-site.sycord.site"


@pytest.mark.asyncio
async def test_resolve_production_domain_keeps_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_setting(key: str, default: str = "") -> str:
        if key == "preview_base_domain":
            return "sycord.site"
        return default

    monkeypatch.setattr("syte.preview_domains.get_setting", fake_get_setting)

    domain = await resolve_production_domain({
        "name": "My Site",
        "id": "abc",
        "domain": "custom.example.com",
    })
    assert domain == "custom.example.com"


@pytest.mark.asyncio
async def test_resolve_preview_zone_sycord_com_defaults_to_site(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_setting(key: str, default: str = "") -> str:
        if key == "preview_base_domain":
            return ""
        if key == "gui_domain":
            return "sycord.com"
        return default

    monkeypatch.setattr("syte.preview_domains.get_setting", fake_get_setting)

    zone = await resolve_preview_zone()
    assert zone == "sycord.site"
