"""Unified Caddy route generation — production + preview with wildcard TLS."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from syte.domain_utils import normalize_domain


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
        name = project.get("name") or project.get("id", "project")
        domain = normalize_domain(project.get("domain") or "")
        port = project.get("port")
        if domain and port:
            production.append(CaddyRoute(domain, int(port), name, "production"))

        preview_domain = normalize_domain(project.get("preview_domain") or "")
        preview_port = project.get("preview_port")
        if preview_domain and preview_port:
            preview.append(CaddyRoute(preview_domain, int(preview_port), name, "preview"))
    return production, preview


def routes_by_zone(routes: list[CaddyRoute]) -> dict[str, list[CaddyRoute]]:
    grouped: dict[str, list[CaddyRoute]] = defaultdict(list)
    for route in routes:
        grouped[host_zone(route.hostname)].append(route)
    return grouped


from syte.preview_iframe import PREVIEW_STRIP_HEADERS


def preview_iframe_header_lines(frame_csp: str, indent: str = "        ") -> list[str]:
    lines = [f"{indent}header {{"]
    for name in PREVIEW_STRIP_HEADERS:
        if name == "Content-Security-Policy":
            continue
        lines.append(f"{indent}    -{name}")
    lines.extend([
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


def render_route_handle(route: CaddyRoute, *, frame_csp: str, indent: str = "    ") -> list[str]:
    matcher = caddy_matcher_name(route.hostname)
    is_preview = route.kind == "preview"
    lines = [
        f"{indent}@{matcher} host {route.hostname}",
        f"{indent}handle @{matcher} {{",
        f"{indent}    # {route.label} ({route.kind})",
    ]
    if is_preview:
        lines.extend(preview_iframe_header_lines(frame_csp, f"{indent}    "))
    lines.extend(
        reverse_proxy_lines(
            route.port,
            strip_frame_headers=is_preview,
            indent=f"{indent}    ",
        )
    )
    lines.append(f"{indent}}}")
    return lines


def render_host_block(route: CaddyRoute, *, frame_csp: str) -> list[str]:
    is_preview = route.kind == "preview"
    lines = [
        f"# {route.label} — {route.kind}",
        f"{route.hostname} {{",
    ]
    if is_preview:
        lines.extend(preview_iframe_header_lines(frame_csp, "    "))
    lines.extend(
        reverse_proxy_lines(route.port, strip_frame_headers=is_preview, indent="    ")
    )
    lines.append("}")
    lines.append("")
    return lines


def render_wildcard_zone(
    zone: str,
    routes: list[CaddyRoute],
    *,
    frame_csp: str,
    dns_tls: bool,
) -> list[str]:
    lines = [f"# Wildcard zone *.{zone} — auto SSL", f"*.{zone} {{"]
    if dns_tls:
        lines.extend([
            "    tls {",
            "        dns cloudflare {env.CLOUDFLARE_API_TOKEN}",
            "    }",
        ])
    for route in routes:
        lines.extend(render_route_handle(route, frame_csp=frame_csp, indent="    "))
    lines.extend(["}", ""])
    return lines


def render_apex_hosts(
    hostnames: list[tuple[str, int, str]],
) -> list[str]:
    """Exact apex hosts (e.g. sycord.com) — not covered by *.sycord.com cert."""
    lines: list[str] = []
    for hostname, port, label in hostnames:
        lines.extend([
            f"# {label} — apex",
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
) -> list[str]:
    """Emit Caddy blocks for production + preview (grouped wildcard TLS when enabled)."""
    production, preview = collect_project_routes(projects)
    all_routes = production + preview
    if not all_routes:
        return []

    if not use_wildcard_tls:
        lines: list[str] = []
        for route in all_routes:
            lines.extend(render_host_block(route, frame_csp=frame_csp))
        return lines

    lines: list[str] = []
    by_zone = routes_by_zone(all_routes)
    for zone in sorted(by_zone):
        zone_routes = by_zone[zone]
        apex_routes = [r for r in zone_routes if r.hostname == zone]
        sub_routes = [r for r in zone_routes if r.hostname != zone]

        if apex_routes:
            lines.extend(
                render_apex_hosts([(r.hostname, r.port, r.label) for r in apex_routes])
            )
        if sub_routes:
            lines.extend(
                render_wildcard_zone(
                    zone,
                    sub_routes,
                    frame_csp=frame_csp,
                    dns_tls=True,
                )
            )
    return lines
