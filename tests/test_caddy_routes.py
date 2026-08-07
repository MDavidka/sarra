"""Tests for the 9router.sycord.site Caddy SSL route."""

from pathlib import Path

import pytest

from syte.caddy_routes import render_9router_route


def test_render_9router_route_emits_host_block_with_auto_ssl() -> None:
    lines = render_9router_route("9router.sycord.site", 4000, use_wildcard_tls=False)
    text = "\n".join(lines)
    assert "9router.sycord.site {" in text
    assert "reverse_proxy 127.0.0.1:4000" in text
    # Auto-HTTPS with no manual tls block → Caddy issues a LE cert itself.
    assert "tls {" not in text


def test_render_9router_route_uses_dns_tls_when_wildcard_enabled() -> None:
    lines = render_9router_route("9router.sycord.site", 4000, use_wildcard_tls=True)
    text = "\n".join(lines)
    assert "tls {" in text
    assert "dns cloudflare {env.CLOUDFLARE_API_TOKEN}" in text
    assert "reverse_proxy 127.0.0.1:4000" in text


def test_render_9router_route_skips_unsafe_hostname() -> None:
    assert render_9router_route("", 4000, use_wildcard_tls=True) == []
    assert render_9router_route("not a host", 4000, use_wildcard_tls=True) == []


@pytest.mark.asyncio
async def test_nine_router_backend_port_defaults_and_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.certificates import nine_router_backend_port
    from syte.config import settings
    from syte.database import init_db, set_setting

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "syte.db")
    await init_db()

    assert await nine_router_backend_port() == 4000

    await set_setting("nine_router_backend_port", "5300")
    assert await nine_router_backend_port() == 5300

    # Invalid values fall back to the gateway default instead of breaking Caddy.
    await set_setting("nine_router_backend_port", "not-a-port")
    assert await nine_router_backend_port() == 4000
    await set_setting("nine_router_backend_port", "99999")
    assert await nine_router_backend_port() == 4000
