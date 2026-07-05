"""Caddy preview route generation — wildcard zones + iframe headers."""

import re
from collections import defaultdict

from syte.domain_utils import normalize_domain


def host_zone(hostname: str) -> str:
    hostname = normalize_domain(hostname)
    parts = hostname.split(".", 1)
    return parts[1] if len(parts) == 2 else hostname


def caddy_matcher_name(hostname: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", hostname.lower())[:56] or "preview"


def collect_preview_routes(projects: list[dict]) -> list[tuple[str, int, str]]:
    routes: list[tuple[str, int, str]] = []
    for project in projects:
        hostname = normalize_domain(project.get("preview_domain") or "")
        port = project.get("preview_port")
        if hostname and port:
            routes.append((hostname, int(port), project.get("name") or project.get("id", "preview")))
    return routes


def preview_iframe_header_lines(frame_csp: str, indent: str = "        ") -> list[str]:
    return [
        f"{indent}header {{",
        f"{indent}    -X-Frame-Options",
        f"{indent}    -Cross-Origin-Embedder-Policy",
        f"{indent}    -Cross-Origin-Opener-Policy",
        f"{indent}    -Cross-Origin-Resource-Policy",
        f'{indent}    Content-Security-Policy "{frame_csp}"',
        f"{indent}}}",
    ]


def preview_reverse_proxy_lines(port: int, indent: str = "        ") -> list[str]:
    return [
        f"{indent}reverse_proxy 127.0.0.1:{port} {{",
        f"{indent}    header_down -X-Frame-Options",
        f"{indent}    header_down Content-Security-Policy",
        f"{indent}    header_down Cross-Origin-Embedder-Policy",
        f"{indent}    header_down Cross-Origin-Opener-Policy",
        f"{indent}    header_down Cross-Origin-Resource-Policy",
        f"{indent}}}",
    ]


def render_preview_host_block(
    hostname: str,
    port: int,
    name: str,
    *,
    frame_csp: str,
    embed_mode: str,
) -> list[str]:
    return [
        f"# Preview — {name} (iframe embed: {embed_mode})",
        f"{hostname} {{",
        *preview_iframe_header_lines(frame_csp, "    "),
        *preview_reverse_proxy_lines(port, "    "),
        "}",
        "",
    ]


def render_preview_wildcard_zone(
    zone: str,
    routes: list[tuple[str, int, str]],
    *,
    frame_csp: str,
    embed_mode: str,
    dns_tls: bool,
) -> list[str]:
    lines = [
        f"# Preview wildcard zone — *.{zone}",
        f"*.{zone} {{",
    ]
    if dns_tls:
        lines.extend([
            "    tls {",
            "        dns cloudflare {env.CLOUDFLARE_API_TOKEN}",
            "    }",
        ])
    for hostname, port, name in routes:
        matcher = caddy_matcher_name(hostname)
        lines.extend([
            f"    @{matcher} host {hostname}",
            f"    handle @{matcher} {{",
            f"        # {name}",
            *preview_iframe_header_lines(frame_csp, "        "),
            *preview_reverse_proxy_lines(port, "        "),
            "    }",
        ])
    lines.extend(["}", ""])
    return lines


def render_preview_sections(
    projects: list[dict],
    *,
    frame_csp: str,
    embed_mode: str,
    use_wildcard_tls: bool,
) -> list[str]:
    """Emit Caddy blocks for all project previews."""
    routes = collect_preview_routes(projects)
    if not routes:
        return []

    if not use_wildcard_tls:
        lines: list[str] = []
        for hostname, port, name in routes:
            lines.extend(
                render_preview_host_block(
                    hostname, port, name, frame_csp=frame_csp, embed_mode=embed_mode
                )
            )
        return lines

    by_zone: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for hostname, port, name in routes:
        by_zone[host_zone(hostname)].append((hostname, port, name))

    lines: list[str] = []
    for zone in sorted(by_zone):
        lines.extend(
            render_preview_wildcard_zone(
                zone,
                by_zone[zone],
                frame_csp=frame_csp,
                embed_mode=embed_mode,
                dns_tls=True,
            )
        )
    return lines
