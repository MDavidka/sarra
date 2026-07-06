"""Tests for production/preview domain resolution."""

import pytest

from syte.preview_domains import (
    _preview_domain_valid,
    build_preview_urls,
    resolve_preview_domain,
    resolve_production_domain,
    resolve_preview_zone,
)


@pytest.mark.asyncio
async def test_resolve_preview_domain_rejects_wrong_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_setting(key: str, default: str = "") -> str:
        if key == "preview_base_domain":
            return "sycord.site"
        return default

    monkeypatch.setattr("syte.preview_domains.get_setting", fake_get_setting)

    domain = await resolve_preview_domain(
        {
            "name": "tsst76",
            "id": "abc",
            "preview_domain": "previewa-tsst76.sycord.com",
        },
        new_session=False,
    )
    assert domain.endswith(".sycord.site")
    assert domain != "previewa-tsst76.sycord.com"


def test_preview_domain_valid() -> None:
    assert _preview_domain_valid("previewa-app.sycord.site", "sycord.site")
    assert not _preview_domain_valid("previewa-app.sycord.com", "sycord.site")


def test_build_preview_urls_tls_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("syte.preview_iframe.probe_https_available", lambda _url: False)
    monkeypatch.setattr("syte.config.settings.public_ip", "152.89.245.113")
    urls = build_preview_urls({
        "preview_domain": "previewa-tsst76.sycord.com",
        "preview_port": 4000,
    })
    assert urls["preview_tls_ok"] is False
    assert urls["preview_fetch_url"] == "http://152.89.245.113:4000"
    assert urls["preview_tls_hint"]


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
