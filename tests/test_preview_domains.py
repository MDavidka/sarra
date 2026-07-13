"""Tests for production/preview domain resolution."""

import pytest

from syte.preview_domains import (
    build_preview_urls,
    is_preview_hostname,
    resolve_preview_domain,
    resolve_production_domain,
    resolve_preview_zone,
    preview_frame_ancestors_csp,
)


@pytest.mark.asyncio
async def test_resolve_preview_domain_reuses_existing_any_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_setting(key: str, default: str = "") -> str:
        if key == "preview_base_domain":
            return "sycord.site"
        return default

    monkeypatch.setattr("syte.preview_domains.get_setting", fake_get_setting)

    domain = await resolve_preview_domain({
        "name": "tsst76",
        "id": "abc",
        "preview_domain": "previewa-tsst76.sycord.com",
    })
    assert domain == "previewa-tsst76.sycord.com"


def test_is_preview_hostname() -> None:
    assert is_preview_hostname("previewa-app.sycord.site")
    assert is_preview_hostname("previewa-app.sycord.com")
    assert not is_preview_hostname("app.sycord.site")
    assert not is_preview_hostname("")


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
async def test_resolve_preview_domain_allocates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_setting(key: str, default: str = "") -> str:
        if key == "preview_base_domain":
            return "sycord.site"
        return default

    monkeypatch.setattr("syte.preview_domains.get_setting", fake_get_setting)

    domain = await resolve_preview_domain({"name": "My Site", "id": "abc"})
    assert domain.endswith(".sycord.site")
    assert domain.startswith("preview")


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


def test_preview_frame_ancestors_csp_default() -> None:
    csp = preview_frame_ancestors_csp()
    assert csp.startswith("frame-ancestors ")
    parts = csp.split(" ")
    assert "'self'" in parts
    assert "https://sycord.com" in parts
    assert "https://www.sycord.com" in parts
    assert "https://*.sycord.com" in parts
    assert "*" in parts
    assert "http://localhost:*" in parts
    assert "https://localhost:*" in parts


def test_preview_frame_ancestors_csp_allow_any_false() -> None:
    csp = preview_frame_ancestors_csp(allow_any=False)
    parts = csp.split(" ")
    assert "*" not in parts
    assert "'self'" in parts


def test_preview_frame_ancestors_csp_with_gui_domain() -> None:
    csp = preview_frame_ancestors_csp(gui_domain="custom.com")
    parts = csp.split(" ")
    assert "https://custom.com" in parts
    assert "https://*.custom.com" in parts

    csp_sub = preview_frame_ancestors_csp(gui_domain="app.custom.com")
    parts_sub = csp_sub.split(" ")
    assert "https://app.custom.com" in parts_sub
    assert "https://*.app.custom.com" in parts_sub
    assert "https://custom.com" in parts_sub
    assert "https://*.custom.com" in parts_sub
