from syte.caddy_routes import render_all_service_routes


def test_render_all_service_routes_isolates_previews_with_tls():
    projects = [{
        "name": "Shop",
        "domain": "shop.example.test",
        "port": 3001,
        "preview_domain": "preview-shop.example.test",
        "preview_port": 4101,
    }]
    text = "\n".join(render_all_service_routes(
        projects,
        frame_csp="frame-ancestors 'self'",
        use_wildcard_tls=True,
    ))
    assert "preview-shop.example.test {" in text
    assert "reverse_proxy 127.0.0.1:4101" in text
    assert "*.example.test {" in text
    assert "reverse_proxy 127.0.0.1:3001" in text


def test_render_all_service_routes_rejects_invalid_hosts():
    text = "\n".join(render_all_service_routes(
        [{"name": "Unsafe", "domain": "not a hostname", "port": 3001}],
        frame_csp="frame-ancestors 'self'",
        use_wildcard_tls=False,
    ))
    assert text == ""
