"""Tests for the 9router.sycord.site Caddy SSL route and isolated previews."""

from pathlib import Path

import pytest

from syte.caddy_routes import (
    NINE_ROUTER_UPSTREAM_DEFAULT,
    render_9router_route,
    render_all_service_routes,
)


def test_render_9router_route_emits_host_block_with_auto_ssl() -> None:
    lines = render_9router_route(
        "9router.sycord.site", NINE_ROUTER_UPSTREAM_DEFAULT, use_wildcard_tls=False
    )
    text = "\n".join(lines)
    assert "9router.sycord.site {" in text
    assert "reverse_proxy 65.75.203.134:20128" in text
    # Auto-HTTPS with no manual tls block → Caddy issues a LE cert itself.
    assert "tls {" not in text


def test_render_9router_route_uses_dns_tls_when_wildcard_enabled() -> None:
    lines = render_9router_route(
        "9router.sycord.site", NINE_ROUTER_UPSTREAM_DEFAULT, use_wildcard_tls=True
    )
    text = "\n".join(lines)
    assert "tls {" in text
    assert "dns cloudflare {env.CLOUDFLARE_API_TOKEN}" in text
    assert "reverse_proxy 65.75.203.134:20128" in text


def test_render_9router_route_skips_unsafe_hostname() -> None:
    assert render_9router_route("", "65.75.203.134:20128", use_wildcard_tls=True) == []
    assert (
        render_9router_route("not a host", "65.75.203.134:20128", use_wildcard_tls=True) == []
    )


def test_render_9router_route_accepts_custom_upstream() -> None:
    lines = render_9router_route("9router.sycord.site", "1.1.1.1:5050", use_wildcard_tls=True)
    text = "\n".join(lines)
    assert "reverse_proxy 1.1.1.1:5050" in text


def test_normalize_nine_router_upstream_rejects_local_destinations() -> None:
    from syte.certificates import normalize_remote_nine_router_upstream

    assert normalize_remote_nine_router_upstream("localhost:5050") == ""
    assert normalize_remote_nine_router_upstream("127.0.0.1:5050") == ""
    assert normalize_remote_nine_router_upstream("10.0.0.9:5050") == ""
    assert normalize_remote_nine_router_upstream("1.1.1.1:5050") == "1.1.1.1:5050"


def test_render_all_service_routes_isolates_previews_with_own_tls() -> None:
    projects = [
        {
            "name": "Shop",
            "domain": "shop.sycord.site",
            "port": 3001,
            "preview_domain": "previewk-shop.sycord.site",
            "preview_port": 4101,
        }
    ]
    lines = render_all_service_routes(
        projects,
        frame_csp="frame-ancestors 'self'",
        use_wildcard_tls=True,
    )
    text = "\n".join(lines)
    # Preview gets its own host block with a dedicated DNS-01 TLS block.
    assert "previewk-shop.sycord.site {" in text
    assert "dns cloudflare {env.CLOUDFLARE_API_TOKEN}" in text
    assert "reverse_proxy 127.0.0.1:4101" in text
    # Production stays on the shared wildcard zone block.
    assert "*.sycord.site {" in text
    assert "reverse_proxy 127.0.0.1:3001" in text


def test_render_all_service_routes_keeps_previews_on_wildcard_when_disabled() -> None:
    projects = [
        {
            "name": "Shop",
            "domain": "shop.sycord.site",
            "port": 3001,
            "preview_domain": "previewk-shop.sycord.site",
            "preview_port": 4101,
        }
    ]
    lines = render_all_service_routes(
        projects,
        frame_csp="frame-ancestors 'self'",
        use_wildcard_tls=True,
        isolate_previews=False,
    )
    text = "\n".join(lines)
    assert "previewk-shop.sycord.site {" not in text
    assert "*.sycord.site {" in text


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


@pytest.mark.asyncio
async def test_nine_router_upstream_defaults_to_gateway_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.certificates import nine_router_upstream
    from syte.config import settings
    from syte.database import init_db, set_setting

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "db_path", tmp_path / "syte.db")
    await init_db()

    # Default: the dedicated gateway upstream, not a local port.
    assert await nine_router_upstream() == "65.75.203.134:20128"

    await set_setting("nine_router_upstream", "1.1.1.1:5050")
    assert await nine_router_upstream() == "1.1.1.1:5050"

    # Private/local settings fall back to the real remote gateway.
    await set_setting("nine_router_upstream", "10.0.0.9:5050")
    assert await nine_router_upstream() == "65.75.203.134:20128"
    await set_setting("nine_router_upstream", "localhost:5050")
    assert await nine_router_upstream() == "65.75.203.134:20128"

    # The legacy local port must never redirect public 9Router traffic.
    await set_setting("nine_router_upstream", "")
    await set_setting("nine_router_backend_port", "5300")
    assert await nine_router_upstream() == "65.75.203.134:20128"

    # Invalid upstreams fall back to the default instead of breaking Caddy.
    await set_setting("nine_router_upstream", "not a host")
    assert await nine_router_upstream() == "65.75.203.134:20128"
    await set_setting("nine_router_upstream", "1.1.1.1:99999")
    assert await nine_router_upstream() == "65.75.203.134:20128"
