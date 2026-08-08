from syte.caddy_routes import NINE_ROUTER_DASHBOARD_PATH, render_managed_9router_route


def test_managed_9router_root_redirects_to_official_dashboard() -> None:
    text = "\n".join(
        render_managed_9router_route(
            "api.sycord.site",
            20129,
            use_wildcard_tls=False,
        )
    )

    assert "@nine_router_root path /" in text
    assert f"redir @nine_router_root {NINE_ROUTER_DASHBOARD_PATH} 302" in text
    assert "reverse_proxy 127.0.0.1:20129" in text
