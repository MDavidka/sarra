"""Unified Caddy route generation — production + preview with wildcard TLS."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from syte.domain_utils import (
    is_safe_caddy_hostname,
    normalize_domain,
    sanitize_caddy_label,
)

# 9Router AI gateway upstream: TLS terminates on this Caddy instance
# (https://9router.sycord.site) and traffic is proxied to the dedicated
# gateway host/port. A separate loopback-only listener below verifies the same
# certificate/SNI path for local API clients without changing the remote upstream.
NINE_ROUTER_UPSTREAM_DEFAULT = "65.75.203.134:20128"
# Caddy also exposes a loopback-only TLS listener so the Settings tab and
# local API clients can verify the certificate/SNI path without leaving this VM.
NINE_ROUTER_LOCAL_TLS_PORT = 20128
# Host used when the managed Router tab publishes the local 9Router container.
NINE_ROUTER_PUBLIC_HOST = "api.sycord.site"
# The managed 9Router web UI should live on a separate GUI host so the router
# can own api.sycord.site without colliding with Syte's main console.
NINE_ROUTER_GUI_HOST = "9router.sycord.site"
# The official 9Router web UI is mounted at /dashboard. Keep this in the route
# layer so opening the public host lands on the real dashboard instead of the
# API root, which intentionally returns 404.
NINE_ROUTER_DASHBOARD_PATH = "/dashboard"

# Public recursive resolvers used for DNS-01 propagation checks. Without these
# Caddy asks the system resolver, which on this host points at a local/split
# view that does not yet see the freshly written _acme-challenge TXT record —
# the DNS-01 order then times out and Caddy falls back to its self-signed
# internal issuer.
ACME_DNS_RESOLVERS = "1.1.1.1 8.8.8.8"

# How long Caddy waits for the challenge TXT record to become visible on the
# authoritative nameservers before giving up on the order.
ACME_PROPAGATION_TIMEOUT = "5m"


def dedicated_dns_tls_lines(indent: str = "    ") -> list[str]:
    """TLS block that forces a *dedicated* DNS-01 certificate for one hostname.

    Naming the hostname in its own site block already makes Caddy manage a
    certificate whose only subject is that hostname — separate from the shared
    ``*.{zone}`` wildcard. Pinning explicit resolvers and a generous
    propagation timeout is what makes that dedicated order actually succeed, so
    the host stops falling back to the wildcard (or to Caddy's untrusted
    internal issuer) when the wildcard is broken or self-signed.
    """
    return [
        f"{indent}tls {{",
        f"{indent}    dns cloudflare {{env.CLOUDFLARE_API_TOKEN}}",
        f"{indent}    resolvers {ACME_DNS_RESOLVERS}",
        f"{indent}    propagation_timeout {ACME_PROPAGATION_TIMEOUT}",
        f"{indent}}}",
    ]


def host_zone(hostname: str) -> str:
    hostname = normalize_domain(hostname)
    parts = hostname.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname


def caddy_matcher_name(hostname: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", hostname.lower())[:56] or "host"


@dataclass(frozen=True)
class CaddyRoute:
    hostname: str
    port: int
    label: str
    kind: str  # production | preview


def collect_project_routes(
    projects: list[dict],
) -> tuple[list[CaddyRoute], list[CaddyRoute]]:
    """Split production and preview routes from project records."""
    production: list[CaddyRoute] = []
    preview: list[CaddyRoute] = []
    for project in projects:
        name = sanitize_caddy_label(project.get("name") or project.get("id", "project"))
        domain = normalize_domain(project.get("domain") or "")
        port = project.get("port")
        if domain and port and is_safe_caddy_hostname(domain):
            production.append(CaddyRoute(domain, int(port), name, "production"))

        preview_domain = normalize_domain(project.get("preview_domain") or "")
        preview_port = project.get("preview_port")
        if preview_domain and preview_port and is_safe_caddy_hostname(preview_domain):
            preview.append(CaddyRoute(preview_domain, int(preview_port), name, "preview"))
    return production, preview


def collect_custom_tls_routes(projects: list[dict]) -> list[CaddyRoute]:
    """Per-app dedicated TLS domains (own Let's Encrypt cert, not wildcard).

    These render as standalone host blocks so an app can pin its own custom
    domain + certificate independently of the shared wildcard zone.
    """
    custom: list[CaddyRoute] = []
    for project in projects:
        domain = normalize_domain(project.get("custom_tls_domain") or "")
        port = project.get("port")
        enabled = project.get("custom_tls_enabled")
        if domain and port and is_safe_caddy_hostname(domain) and enabled:
            name = sanitize_caddy_label(project.get("name") or project.get("id", "project"))
            custom.append(CaddyRoute(domain, int(port), name, "custom"))
    return custom


def render_custom_tls_block(route: CaddyRoute) -> list[str]:
    """Emit a standalone host block for a project's dedicated custom TLS domain."""
    label = sanitize_caddy_label(route.label)
    return [
        f"# {label} — custom TLS",
        f"{route.hostname} {{",
        f"    reverse_proxy 127.0.0.1:{route.port}",
        "}",
        "",
    ]


def routes_by_zone(routes: list[CaddyRoute]) -> dict[str, list[CaddyRoute]]:
    grouped: dict[str, list[CaddyRoute]] = defaultdict(list)
    for route in routes:
        grouped[host_zone(route.hostname)].append(route)
    return grouped


from syte.preview_iframe import PREVIEW_STRIP_HEADERS


def preview_cors_origin(gui_domain: str = "") -> str:
    """Single allowed CORS origin for preview fetches (never '*')."""
    gui = normalize_domain(gui_domain or "")
    if gui:
        return f"https://{gui}"
    return "https://sycord.com"


def preview_iframe_header_lines(
    frame_csp: str,
    indent: str = "        ",
    *,
    cors_origin: str | None = None,
) -> list[str]:
    origin = cors_origin or preview_cors_origin()
    # Caddyfile strings cannot embed raw quotes/newlines.
    origin = origin.replace('"', "").replace("\n", "").replace("\r", "") or "https://sycord.com"
    lines = [f"{indent}header {{"]
    for name in PREVIEW_STRIP_HEADERS:
        if name == "Content-Security-Policy":
            continue
        lines.append(f"{indent}    -{name}")
    lines.extend([
        f"{indent}    Cross-Origin-Resource-Policy cross-origin",
        f"{indent}    Access-Control-Allow-Origin {origin}",
        f'{indent}    Content-Security-Policy "{frame_csp}"',
        f"{indent}}}",
    ])
    return lines


def reverse_proxy_lines(
    port: int,
    *,
    strip_frame_headers: bool,
    indent: str = "        ",
) -> list[str]:
    lines = [f"{indent}reverse_proxy 127.0.0.1:{port} {{"]
    if strip_frame_headers:
        for name in PREVIEW_STRIP_HEADERS:
            lines.append(f"{indent}    header_down -{name}")
    lines.append(f"{indent}}}")
    return lines


def render_route_handle(
    route: CaddyRoute,
    *,
    frame_csp: str,
    indent: str = "    ",
    cors_origin: str | None = None,
) -> list[str]:
    matcher = caddy_matcher_name(route.hostname)
    is_preview = route.kind == "preview"
    label = sanitize_caddy_label(route.label)
    lines = [
        f"{indent}@{matcher} host {route.hostname}",
        f"{indent}handle @{matcher} {{",
        f"{indent}    # {label} ({route.kind})",
    ]
    if is_preview:
        lines.extend(
            preview_iframe_header_lines(
                frame_csp, f"{indent}    ", cors_origin=cors_origin
            )
        )
    lines.extend(
        reverse_proxy_lines(
            route.port,
            strip_frame_headers=is_preview,
            indent=f"{indent}    ",
        )
    )
    lines.append(f"{indent}}}")
    return lines


def render_host_block(
    route: CaddyRoute,
    *,
    frame_csp: str,
    cors_origin: str | None = None,
) -> list[str]:
    is_preview = route.kind == "preview"
    label = sanitize_caddy_label(route.label)
    lines = [
        f"# {label} — {route.kind}",
        f"{route.hostname} {{",
    ]
    if is_preview:
        lines.extend(
            preview_iframe_header_lines(frame_csp, "    ", cors_origin=cors_origin)
        )
    lines.extend(
        reverse_proxy_lines(route.port, strip_frame_headers=is_preview, indent="    ")
    )
    lines.append("}")
    lines.append("")
    return lines


def render_litellm_api_route(
    hostname: str,
    port: int,
    *,
    use_wildcard_tls: bool,
    gui_port: int | None = None,
    backend_name: str = "LiteLLM",
) -> list[str]:
    """Render the combined Syte GUI and public OpenAI-compatible API host.

    The selected gateway stays on loopback. Only ``/v1/*`` paths are forwarded
    to it; all other paths go to the Syte GUI when ``gui_port`` is provided,
    keeping gateway administration private. ``backend_name`` is used only in
    the generated comment so this route can also publish local 9Router.
    """

    hostname = normalize_domain(hostname)
    if not hostname or not is_safe_caddy_hostname(hostname):
        return []

    lines = [
        f"# {backend_name} public API — /v1 routes stay behind the Syte GUI host",
        f"{hostname} {{",
    ]
    if use_wildcard_tls:
        lines.extend([
            "    tls {",
            "        dns cloudflare {env.CLOUDFLARE_API_TOKEN}",
            "    }",
        ])
    lines.extend([
        "    @v1 path /v1 /v1/*",
        "    handle @v1 {",
        f"        reverse_proxy 127.0.0.1:{port}",
        "    }",
        "    handle {",
    ])
    if gui_port is not None:
        lines.append(f"        reverse_proxy 127.0.0.1:{gui_port}")
    else:
        lines.append('        respond "Not found" 404')
    lines.extend([
        "    }",
        "}",
        "",
    ])
    return lines


def render_managed_9router_route(
    hostname: str,
    port: int,
    *,
    use_wildcard_tls: bool,
) -> list[str]:
    """Render the full public host for the managed local 9Router container.

    The managed container owns ``api.sycord.site`` while enabled. This is
    intentionally a full host block rather than a path-only handler: the
    9Router dashboard and its API assets need the same origin and base URL.
    The public root redirects to the official dashboard because 9Router's API
    root is not a web page and intentionally returns 404.
    """
    hostname = normalize_domain(hostname)
    if not hostname or not is_safe_caddy_hostname(hostname):
        return []
    lines = [
        "# Managed 9Router — public host (the Router tab owns this hostname)",
        f"{hostname} {{",
    ]
    if use_wildcard_tls:
        lines.extend(dedicated_dns_tls_lines("    "))
    lines.extend([
        f"    @nine_router_root path /",
        f"    redir @nine_router_root {NINE_ROUTER_DASHBOARD_PATH} 302",
        f"    reverse_proxy 127.0.0.1:{port} {{",
        "        header_up X-Forwarded-Host {host}",
        "        header_up X-Forwarded-Proto https",
        "    }",
        "}",
        "",
    ])
    return lines


def render_9router_route(
    hostname: str,
    upstream: str = NINE_ROUTER_UPSTREAM_DEFAULT,
    *,
    use_wildcard_tls: bool,
) -> list[str]:
    """Render the public 9Router AI-gateway host with its own dedicated TLS cert.

    ``9router.sycord.site`` is the legacy public host used while the managed
    container is disabled. Managed mode instead publishes the container at
    ``api.sycord.site``. This standalone host block makes Caddy terminate TLS
    for the legacy route and forward every path to the gateway upstream
    (``65.75.203.134:20128`` by default — the dedicated gateway host; override
    via the ``nine_router_upstream`` setting).

    The certificate is deliberately **not** the shared ``*.{zone}`` wildcard.
    9Router is the only externally-proxied host here, so a wildcard re-issue or
    a self-signed wildcard placeholder must not be able to break the AI gateway
    that every agent request depends on. Giving it its own single-subject order
    isolates it exactly like previews (see ``render_preview_host_block``).
    """
    hostname = normalize_domain(hostname)
    if not hostname or not is_safe_caddy_hostname(hostname):
        return []

    lines = [
        "# 9Router public AI gateway — dedicated SSL (never the zone wildcard)",
        f"{hostname} {{",
    ]
    if use_wildcard_tls:
        # DNS-01 via Cloudflare: issues a real Let's Encrypt certificate whose
        # only subject is this hostname, even before port 80 is reachable.
        lines.extend(dedicated_dns_tls_lines("    "))
    lines.extend([
        f"    reverse_proxy {upstream} {{",
        # Preserve the client-facing host/scheme so the gateway can build
        # correct absolute URLs behind this TLS terminator.
        "        header_up Host {upstream_hostport}",
        "        header_up X-Forwarded-Host {host}",
        "        header_up X-Forwarded-Proto https",
        "    }",
        "}",
        "",
        "# 9Router loopback TLS probe — certificate/SNI check for the local API path",
        "# No tls directive here on purpose: a second automation policy for the same",
        "# hostname would make certificate management ambiguous. Caddy matches",
        "# automation policies by SNI, so this listener reuses the dedicated cert",
        "# issued by the public block above.",
        f"{hostname}:{NINE_ROUTER_LOCAL_TLS_PORT} {{",
        "    bind 127.0.0.1",
    ])
    lines.extend([
        f"    reverse_proxy {upstream} {{",
        "        header_up Host {upstream_hostport}",
        "        header_up X-Forwarded-Host {host}",
        "        header_up X-Forwarded-Proto https",
        "    }",
        "}",
        "",
    ])
    return lines


def render_preview_host_block(
    route: CaddyRoute,
    *,
    frame_csp: str,
    cors_origin: str | None = None,
) -> list[str]:
    """Emit a standalone host block for one preview route with its own TLS.

    Previews get their own Let's Encrypt DNS-01 certificate instead of sharing
    the zone wildcard cert, so a wildcard re-issue (e.g. after a fresh server
    install) never cuts active previews.
    """
    label = sanitize_caddy_label(route.label)
    lines = [
        f"# {label} — preview (isolated SSL)",
        f"{route.hostname} {{",
    ]
    lines.extend(dedicated_dns_tls_lines("    "))
    lines.extend(preview_iframe_header_lines(frame_csp, "    ", cors_origin=cors_origin))
    lines.extend(
        reverse_proxy_lines(route.port, strip_frame_headers=True, indent="    ")
    )
    lines.append("}")
    lines.append("")
    return lines


def render_isolated_previews(
    preview_routes: list[CaddyRoute],
    *,
    frame_csp: str,
    cors_origin: str | None = None,
) -> list[str]:
    lines: list[str] = []
    for route in preview_routes:
        lines.extend(
            render_preview_host_block(route, frame_csp=frame_csp, cors_origin=cors_origin)
        )
    return lines


def render_wildcard_zone(
    zone: str,
    routes: list[CaddyRoute],
    *,
    frame_csp: str,
    dns_tls: bool = True,
    cors_origin: str | None = None,
) -> list[str]:
    label = sanitize_caddy_label(zone)
    lines = [f"# {label} — wildcard zone", f"*.{zone} {{"]
    if dns_tls:
        lines.extend(dedicated_dns_tls_lines("    "))
    for route in routes:
        lines.extend(
            render_route_handle(route, frame_csp=frame_csp, indent="    ", cors_origin=cors_origin)
        )
    lines.append("}")
    lines.append("")
    return lines


def render_apex_hosts(hosts: list[tuple[str, int, str]]) -> list[str]:
    lines: list[str] = []
    for hostname, port, label in hosts:
        name = sanitize_caddy_label(label)
        lines.extend([
            f"# {name} — apex",
            f"{hostname} {{",
            f"    reverse_proxy 127.0.0.1:{port}",
            "}",
            "",
        ])
    return lines


def render_all_service_routes(
    projects: list[dict],
    *,
    frame_csp: str,
    use_wildcard_tls: bool,
    cors_origin: str | None = None,
    isolate_previews: bool = True,
) -> list[str]:
    """Emit Caddy blocks for production + preview."""
    custom_routes = collect_custom_tls_routes(projects)
    production, preview = collect_project_routes(projects)
    all_routes = production + preview
    custom_lines = [line for route in custom_routes for line in render_custom_tls_block(route)]
    if not use_wildcard_tls:
        lines: list[str] = list(custom_lines)
        for route in all_routes:
            lines.extend(render_host_block(route, frame_csp=frame_csp, cors_origin=cors_origin))
        return lines

    lines: list[str] = list(custom_lines)
    if isolate_previews and preview:
        lines.extend(render_isolated_previews(preview, frame_csp=frame_csp, cors_origin=cors_origin))
    by_zone = routes_by_zone(production)
    for zone in sorted(by_zone):
        zone_routes = by_zone[zone]
        apex_routes = [r for r in zone_routes if r.hostname == zone]
        sub_routes = [r for r in zone_routes if r.hostname != zone]
        if apex_routes:
            lines.extend(render_apex_hosts([(r.hostname, r.port, r.label) for r in apex_routes]))
        if sub_routes:
            lines.extend(render_wildcard_zone(zone, sub_routes, frame_csp=frame_csp, dns_tls=True, cors_origin=cors_origin))
    if not isolate_previews:
        preview_zone_routes = routes_by_zone(preview)
        for zone in sorted(preview_zone_routes):
            lines.extend(render_wildcard_zone(zone, preview_zone_routes[zone], frame_csp=frame_csp, dns_tls=True, cors_origin=cors_origin))
    return lines
