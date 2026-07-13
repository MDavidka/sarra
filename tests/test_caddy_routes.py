import pytest

from syte.caddy_routes import (
    CaddyRoute,
    caddy_matcher_name,
    collect_project_routes,
    host_zone,
    preview_iframe_header_lines,
    render_all_service_routes,
    render_apex_hosts,
    render_host_block,
    render_route_handle,
    render_wildcard_zone,
    reverse_proxy_lines,
    routes_by_zone,
)


def test_host_zone() -> None:
    assert host_zone("example.com") == "example.com"
    assert host_zone("sub.example.com") == "example.com"
    assert host_zone("a.b.c.example.com") == "example.com"
    assert host_zone("localhost") == "localhost"
    assert host_zone("https://example.com/path") == "example.com"
    assert host_zone("http://sub.example.com:8080") == "example.com"
    assert host_zone("") == ""
    assert host_zone("com") == "com"


def test_caddy_matcher_name() -> None:
    assert caddy_matcher_name("example.com") == "example_com"
    assert caddy_matcher_name("sub.example.com") == "sub_example_com"
    assert caddy_matcher_name("a-b.com") == "a_b_com"
    assert caddy_matcher_name("A_B.com") == "a_b_com"
    # test truncation
    long_name = "a" * 60 + ".com"
    assert len(caddy_matcher_name(long_name)) == 56
    assert caddy_matcher_name("!@#$%^") == "______"
    assert caddy_matcher_name("") == "host"


def test_collect_project_routes() -> None:
    projects = [
        {
            "id": "proj1",
            "name": "Project 1",
            "domain": "example.com",
            "port": 8000,
        },
        {
            "id": "proj2",
            "name": "Project 2",
            "preview_domain": "preview.example.com",
            "preview_port": 8001,
        },
        {
            "id": "proj3",
            "name": "Project 3",
            "domain": "app.example.com",
            "port": "8002",
            "preview_domain": "preview-app.example.com",
            "preview_port": "8003",
        },
        {
            "id": "proj4",
            "name": "Project 4",
            "domain": "missing-port.com",
            # missing port
        },
        {
            "id": "proj5",
            # missing domain
            "port": 8004,
        },
    ]

    prod_routes, prev_routes = collect_project_routes(projects)

    assert len(prod_routes) == 2
    assert prod_routes[0] == CaddyRoute("example.com", 8000, "Project 1", "production")
    assert prod_routes[1] == CaddyRoute("app.example.com", 8002, "Project 3", "production")

    assert len(prev_routes) == 2
    assert prev_routes[0] == CaddyRoute("preview.example.com", 8001, "Project 2", "preview")
    assert prev_routes[1] == CaddyRoute("preview-app.example.com", 8003, "Project 3", "preview")


def test_routes_by_zone() -> None:
    routes = [
        CaddyRoute("example.com", 8000, "App", "production"),
        CaddyRoute("api.example.com", 8001, "API", "production"),
        CaddyRoute("test.com", 8002, "Test", "production"),
        CaddyRoute("sub.test.com", 8003, "Sub Test", "production"),
    ]

    grouped = routes_by_zone(routes)
    assert len(grouped) == 2
    assert len(grouped["example.com"]) == 2
    assert len(grouped["test.com"]) == 2


def test_preview_iframe_header_lines() -> None:
    csp = "frame-ancestors 'self'"
    lines = preview_iframe_header_lines(csp, indent="  ")

    assert lines[0] == "  header {"
    assert "      -X-Frame-Options" in lines
    assert "      Cross-Origin-Resource-Policy cross-origin" in lines
    assert "      Access-Control-Allow-Origin *" in lines
    assert f'      Content-Security-Policy "{csp}"' in lines
    assert lines[-1] == "  }"


def test_reverse_proxy_lines() -> None:
    # No stripping
    lines1 = reverse_proxy_lines(8000, strip_frame_headers=False, indent="  ")
    assert lines1 == [
        "  reverse_proxy 127.0.0.1:8000 {",
        "  }"
    ]

    # With stripping
    lines2 = reverse_proxy_lines(8000, strip_frame_headers=True, indent="  ")
    assert "  reverse_proxy 127.0.0.1:8000 {" in lines2
    assert "      header_down -X-Frame-Options" in lines2
    assert "  }" in lines2


def test_render_route_handle() -> None:
    route = CaddyRoute("api.example.com", 8000, "API", "production")
    lines = render_route_handle(route, frame_csp="csp", indent="  ")

    assert "  @api_example_com host api.example.com" in lines
    assert "  handle @api_example_com {" in lines
    assert "      # API (production)" in lines
    assert "      reverse_proxy 127.0.0.1:8000 {" in lines
    assert "  }" in lines

    route_prev = CaddyRoute("prev.example.com", 8001, "Prev", "preview")
    lines_prev = render_route_handle(route_prev, frame_csp="csp", indent="  ")

    assert "      header {" in lines_prev
    assert '          Content-Security-Policy "csp"' in lines_prev
    assert "          header_down -X-Frame-Options" in lines_prev


def test_render_host_block() -> None:
    route = CaddyRoute("example.com", 8000, "App", "production")
    lines = render_host_block(route, frame_csp="csp")

    assert "# App — production" in lines
    assert "example.com {" in lines
    assert "    reverse_proxy 127.0.0.1:8000 {" in lines
    assert "}" in lines
    assert lines[-1] == ""

    route_prev = CaddyRoute("prev.com", 8001, "Prev", "preview")
    lines_prev = render_host_block(route_prev, frame_csp="csp")

    assert "    header {" in lines_prev
    assert '        Content-Security-Policy "csp"' in lines_prev
    assert "        header_down -X-Frame-Options" in lines_prev


def test_render_wildcard_zone() -> None:
    routes = [
        CaddyRoute("a.example.com", 8000, "A", "production"),
        CaddyRoute("b.example.com", 8001, "B", "production")
    ]

    lines = render_wildcard_zone("example.com", routes, frame_csp="csp", dns_tls=True)

    assert "# Wildcard zone *.example.com — auto SSL" in lines
    assert "*.example.com {" in lines
    assert "    tls {" in lines
    assert "        dns cloudflare {env.CLOUDFLARE_API_TOKEN}" in lines
    assert "    }" in lines
    assert "    @a_example_com host a.example.com" in lines
    assert "    @b_example_com host b.example.com" in lines
    assert "}" in lines

    lines_no_tls = render_wildcard_zone("example.com", routes, frame_csp="csp", dns_tls=False)
    assert "    tls {" not in lines_no_tls


def test_render_apex_hosts() -> None:
    hosts = [
        ("example.com", 8000, "App"),
        ("test.com", 8001, "Test")
    ]

    lines = render_apex_hosts(hosts)

    assert "# App — apex" in lines
    assert "example.com {" in lines
    assert "    reverse_proxy 127.0.0.1:8000" in lines
    assert "}" in lines
    assert "# Test — apex" in lines
    assert "test.com {" in lines
    assert "    reverse_proxy 127.0.0.1:8001" in lines
    assert "}" in lines


def test_render_all_service_routes() -> None:
    projects = [
        {
            "id": "proj1",
            "name": "App",
            "domain": "example.com",
            "port": 8000,
        },
        {
            "id": "proj2",
            "name": "Sub",
            "domain": "sub.example.com",
            "port": 8001,
        }
    ]

    # No projects
    assert render_all_service_routes([], frame_csp="csp", use_wildcard_tls=True) == []

    # No wildcard TLS
    lines_no_wildcard = render_all_service_routes(projects, frame_csp="csp", use_wildcard_tls=False)
    assert "# App — production" in lines_no_wildcard
    assert "example.com {" in lines_no_wildcard
    assert "# Sub — production" in lines_no_wildcard
    assert "sub.example.com {" in lines_no_wildcard

    # Wildcard TLS
    lines_wildcard = render_all_service_routes(projects, frame_csp="csp", use_wildcard_tls=True)
    assert "# App — apex" in lines_wildcard
    assert "example.com {" in lines_wildcard
    assert "    reverse_proxy 127.0.0.1:8000" in lines_wildcard

    assert "# Wildcard zone *.example.com — auto SSL" in lines_wildcard
    assert "*.example.com {" in lines_wildcard
    assert "    @sub_example_com host sub.example.com" in lines_wildcard
    assert "        reverse_proxy 127.0.0.1:8001 {" in lines_wildcard
