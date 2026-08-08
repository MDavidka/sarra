def test_managed_9router_public_host_is_dedicated() -> None:
    from syte.caddy_routes import NINE_ROUTER_PUBLIC_HOST
    assert NINE_ROUTER_PUBLIC_HOST == "9router.sycord.site"
