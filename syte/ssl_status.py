"""SSL / HTTPS status helpers for the SSL dashboard."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from syte.caddy_routes import host_zone
from syte.domain_utils import build_https_url, is_safe_caddy_hostname, normalize_domain


def _cert_dir() -> Path | None:
    candidates = [
        Path("/var/lib/caddy/.local/share/caddy/certificates"),
        Path("/root/.local/share/caddy/certificates"),
    ]
    for base in candidates:
        if base.is_dir():
            return base
    return None


def _has_wildcard_cert(zone: str, cert_root: Path) -> bool:
    """Caddy stores wildcard certs as wildcard_.{zone} in the cert path."""
    marker = f"wildcard_.{zone}"
    for path in cert_root.rglob("*.crt"):
        if marker in path.parent.name or marker in path.name:
            return True
    return False


def stored_wildcard_cert(zone: str) -> dict | None:
    """Inspect the stored wildcard cert for a zone.

    Uses ``openssl`` (available wherever Caddy runs) to read the issuer and
    validity so we can distinguish a real Let's Encrypt wildcard cert from a
    Caddy self-signed placeholder ("Caddy Local Authority") — the latter is
    what shows up as ``cert error`` for every subdomain in the zone.
    """
    cert_root = _cert_dir()
    if not cert_root:
        return None
    marker = f"wildcard_.{zone}"
    for path in cert_root.rglob("*.crt"):
        if marker in path.parent.name or marker in path.name:
            issuer, not_after = _read_cert_meta(path)
            return {
                "path": str(path),
                "issuer": issuer,
                "valid_until": not_after,
                "self_signed": bool(issuer and "Caddy Local Authority" in issuer),
                "exists": True,
            }
    return None


def _read_cert_meta(path: Path) -> tuple[str | None, str | None]:
    """Return (issuer, not_after) for a PEM cert using ``openssl x509``."""
    import subprocess
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(path), "-noout", "-issuer", "-enddate"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    issuer = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("issuer="):
            issuer = line.split("=", 1)[1].strip('"').strip() or None
        if line.startswith("notAfter="):
            not_after = line.split("=", 1)[1].strip()
            return issuer, not_after
    return issuer, None


def _exact_cert_path(hostname: str) -> Path | None:
    """Return the stored certificate whose subject is exactly ``hostname``.

    Deliberately does *not* fall back to the zone wildcard: this is how the
    dashboard distinguishes a host with its own dedicated certificate (for
    example, an isolated preview) from one covered by the shared wildcard.
    """
    cert_root = _cert_dir()
    if not cert_root:
        return None
    host = normalize_domain(hostname)
    if not host:
        return None
    for path in cert_root.rglob("*.crt"):
        if host in path.parent.name or host in path.name:
            return path
    return None


def caddy_has_exact_cert(hostname: str) -> bool:
    """True when Caddy holds a certificate issued for exactly this hostname."""
    return _exact_cert_path(hostname) is not None


def stored_host_cert(hostname: str) -> dict | None:
    """Inspect the dedicated (exact-subject) certificate for one hostname."""
    path = _exact_cert_path(hostname)
    if path is None:
        return None
    issuer, not_after = _read_cert_meta(path)
    return {
        "path": str(path),
        "issuer": issuer,
        "valid_until": not_after,
        "self_signed": bool(issuer and "Caddy Local Authority" in issuer),
        "exists": True,
    }


def cert_scope(hostname: str) -> str:
    """Classify how a hostname is covered by Caddy's certificate store.

    Returns ``"dedicated"`` when the host has its own single-subject cert,
    ``"wildcard"`` when it is only covered by the zone wildcard, and ``"none"``
    when no certificate covers it at all.
    """
    host = normalize_domain(hostname)
    if not host:
        return "none"
    if caddy_has_exact_cert(host):
        return "dedicated"
    cert_root = _cert_dir()
    if cert_root:
        zone = host_zone(host)
        if host != zone and host.endswith(f".{zone}") and _has_wildcard_cert(zone, cert_root):
            return "wildcard"
    return "none"


def _caddy_has_cert(hostname: str) -> bool:
    """Best-effort: check if Caddy stored a cert for this hostname.

    Accepts wildcard coverage — use :func:`caddy_has_exact_cert` when a
    dedicated certificate is what matters.
    """
    cert_root = _cert_dir()
    if not cert_root:
        return False
    host = normalize_domain(hostname)
    if not host:
        return False
    if _exact_cert_path(host) is not None:
        return True
    zone = host_zone(host)
    if host != zone and host.endswith(f".{zone}"):
        return _has_wildcard_cert(zone, cert_root)
    return False


def production_ssl_status(project: dict) -> dict:
    domain = normalize_domain(project.get("domain") or "")
    if not domain:
        return {
            "configured": False,
            "active": False,
            "domain": None,
            "url": None,
            "label": "HTTP only",
        }
    active = _caddy_has_cert(domain)
    return {
        "configured": True,
        "active": active,
        "domain": domain,
        "url": build_https_url(domain),
        "label": "HTTPS" if active else "SSL pending",
    }


def preview_ssl_status(project: dict) -> dict:
    domain = normalize_domain(project.get("preview_domain") or "")
    port = project.get("preview_port")
    if not domain:
        return {
            "configured": False,
            "active": False,
            "domain": None,
            "url": None,
            "label": "off",
        }
    active = _caddy_has_cert(domain)
    return {
        "configured": True,
        "active": active,
        "domain": domain,
        "url": build_https_url(domain) if domain else None,
        "port": port,
        "label": "Preview HTTPS" if active else "Preview SSL pending",
    }


def project_ssl_summary(project: dict) -> dict:
    production = production_ssl_status(project)
    preview = preview_ssl_status(project)
    if production["active"]:
        badge = "https"
        badge_label = "SSL"
    elif production["configured"]:
        badge = "pending"
        badge_label = "SSL pending"
    elif preview["active"]:
        badge = "preview-https"
        badge_label = "Preview SSL"
    elif preview["configured"]:
        badge = "preview-pending"
        badge_label = "Preview pending"
    else:
        badge = "http"
        badge_label = "HTTP"
    return {
        "production": production,
        "preview": preview,
        "badge": badge,
        "badge_label": badge_label,
    }



# ---------------------------------------------------------------------------
# Aggregate SSL dashboard
# ---------------------------------------------------------------------------


def caddy_installed() -> bool:
    import shutil

    return bool(shutil.which("caddy"))


async def _caddy_active() -> bool:
    code, _ = await asyncio.to_thread(
        _run_sh, ["systemctl", "is-active", "caddy"]
    )
    if code == 0:
        return True
    return bool(caddy_installed())


def _run_sh(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return 255, ""


# ---------------------------------------------------------------------------
# AlmaLinux host + endpoint status monitor
# ---------------------------------------------------------------------------

async def _resolve_host(hostname: str) -> list[str]:
    """Resolve a hostname to its IPs, off the event loop."""
    hostname = normalize_domain(hostname)
    if not hostname:
        return []
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror:
        return []
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return ips


async def monitor_endpoint(name: str, domain: str, *, expect_dedicated: bool = False) -> dict:
    """Live DNS + HTTPS status for one monitored hostname.

    ``expect_dedicated`` marks hosts that are configured with their own
    single-subject certificate (9Router, isolated previews). For those, being
    covered only by the zone wildcard is a misconfiguration worth surfacing.
    """
    from syte.ssl_debug import live_probe_https

    host = normalize_domain(domain or "")
    if not host:
        return {
            "name": name,
            "domain": None,
            "configured": False,
            "resolves": False,
            "ips": [],
            "cert_active": False,
            "cert_scope": "none",
            "expect_dedicated": expect_dedicated,
            "dedicated_cert": False,
            "state": "not-configured",
            "reachable": False,
            "latency_ms": None,
            "detail": "no hostname",
        }
    ips = await _resolve_host(host)
    live = await live_probe_https(build_https_url(host))
    scope = cert_scope(host)
    state = live.get("state")
    detail = live.get("detail")
    if expect_dedicated and scope == "wildcard":
        state = "dedicated-cert-missing"
        detail = "Endpoint is reachable, but only the zone wildcard covers it; a dedicated certificate is required."
    return {
        "name": name,
        "domain": host,
        "configured": True,
        "resolves": bool(ips),
        "ips": ips,
        "cert_active": scope != "none",
        "cert_scope": scope,
        "expect_dedicated": expect_dedicated,
        "dedicated_cert": scope == "dedicated",
        "cert": stored_host_cert(host) if scope == "dedicated" else None,
        "state": state,
        "reachable": bool(live.get("reachable")),
        "latency_ms": live.get("latency_ms"),
        "detail": detail,
    }


async def almalinux_monitor() -> dict:
    """Live status monitor for the AlmaLinux host and its public endpoints.

    Probes the three public sycord.site surfaces — apex, wildcard subdomains,
    and the 9Router gateway — plus reports host identity so the dashboard can
    show which machine is being monitored.
    """
    from syte.config import settings

    os_info: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                os_info[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    os_name = (
        os_info.get("PRETTY_NAME")
        or " ".join(part for part in (os_info.get("NAME", ""), os_info.get("VERSION_ID", "")) if part)
    )
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ""
    try:
        public_ip = settings.resolved_public_ip
    except Exception:  # noqa: BLE001 - best-effort host info
        public_ip = ""

    endpoints = [
        await monitor_endpoint("sycord.site", "sycord.site"),
        await monitor_endpoint("*.sycord.site (wildcard)", "probe.sycord.site"),
    ]
    return {
        "os": os_name,
        "os_id": os_info.get("ID", ""),
        "hostname": hostname,
        "public_ip": public_ip,
        "endpoints": endpoints,
    }


# ---------------------------------------------------------------------------
# Service SSL health monitor — web / api / projects
# ---------------------------------------------------------------------------

# Health verdicts, worst first, so a group can be reduced to its weakest member.
HEALTH_DOWN = "down"
HEALTH_DEGRADED = "degraded"
HEALTH_PENDING = "pending"
HEALTH_HEALTHY = "healthy"
HEALTH_UNCONFIGURED = "unconfigured"

_HEALTH_RANK = {
    HEALTH_DOWN: 0,
    HEALTH_DEGRADED: 1,
    HEALTH_PENDING: 2,
    HEALTH_UNCONFIGURED: 3,
    HEALTH_HEALTHY: 4,
}


def health_from_state(state: str | None, *, cert_active: bool, resolves: bool = True) -> str:
    """Reduce a live probe result to a single health verdict.

    ``serving`` is the only healthy outcome: a stored certificate that browsers
    reject, or a host that resolves but never answers, is not "working SSL".
    """
    if state == "not-configured":
        return HEALTH_UNCONFIGURED
    if state == "serving":
        return HEALTH_HEALTHY
    if state in ("invalid-cert", "cert-error", "malformed", "dedicated-cert-missing"):
        # TLS terminates but clients refuse the certificate — worst case, since
        # it looks configured while every request fails.
        return HEALTH_DOWN
    if not resolves:
        return HEALTH_DOWN
    if state == "down":
        # A cert exists but nothing answers → the proxy or app is down.
        return HEALTH_DOWN if cert_active else HEALTH_PENDING
    if state == "pending":
        return HEALTH_PENDING
    return HEALTH_DEGRADED


def worst_health(verdicts: list[str]) -> str:
    """The weakest verdict in a group, ignoring unconfigured members."""
    considered = [v for v in verdicts if v != HEALTH_UNCONFIGURED]
    if not considered:
        return HEALTH_UNCONFIGURED
    return min(considered, key=lambda v: _HEALTH_RANK.get(v, 1))


async def _surface_health(
    key: str,
    name: str,
    description: str,
    domain: str,
    *,
    expect_dedicated: bool = False,
) -> dict:
    """Build one health row for a single named HTTPS surface."""
    endpoint = await monitor_endpoint(name, domain, expect_dedicated=expect_dedicated)
    health = health_from_state(
        endpoint.get("state"),
        cert_active=bool(endpoint.get("cert_active")),
        resolves=bool(endpoint.get("resolves")),
    )
    issues: list[str] = []
    if endpoint.get("configured") and not endpoint.get("resolves"):
        issues.append(f"DNS does not resolve for {endpoint['domain']}.")
    if endpoint.get("state") in ("invalid-cert", "cert-error", "dedicated-cert-missing"):
        issues.append(endpoint.get("detail") or "Certificate is not suitable for this endpoint.")
    if endpoint.get("state") == "down" and endpoint.get("cert_active"):
        issues.append("Certificate is installed but the endpoint is not responding.")
    if endpoint.get("state") == "pending":
        issues.append("Certificate has not been issued yet.")
    if expect_dedicated and endpoint.get("cert_scope") == "wildcard":
        issues.append("Expected a dedicated certificate but only the zone wildcard covers this host.")
    return {
        "key": key,
        "name": name,
        "description": description,
        "kind": "endpoint",
        "health": health,
        "issues": issues,
        **endpoint,
    }


async def service_health_monitor() -> dict:
    """Health monitor for the three SSL surfaces: web, API, and project sites.

    These are the three things that can independently break:

    * **web** — the operator GUI on its custom domain,
    * **projects** — every deployed project's production and preview hostname,
      aggregated because there can be many.
    """
    from syte.database import get_setting, list_projects
    from syte.ssl_debug import live_probe_https

    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    web = await _surface_health(
        "web",
        "Web GUI",
        "Operator interface served over HTTPS",
        gui_domain,
    ) if gui_domain else {
        "key": "web", "name": "Web GUI", "configured": False,
        "health": HEALTH_UNCONFIGURED, "issues": [],
    }

    # Projects: probe every configured production/preview host, then aggregate.
    projects = await list_projects()
    hosts: list[dict] = []
    for project in projects:
        summary = project_ssl_summary(project)
        label = project.get("name") or project.get("id", "project")
        for kind in ("production", "preview"):
            host_info = summary[kind]
            if not host_info.get("configured"):
                continue
            domain = host_info["domain"]
            scope = cert_scope(domain)
            live = await live_probe_https(build_https_url(domain))
            health = health_from_state(
                live.get("state"), cert_active=scope != "none"
            )
            hosts.append({
                "project": label,
                "project_id": project.get("id"),
                "kind": kind,
                "domain": domain,
                "url": host_info.get("url"),
                "cert_scope": scope,
                "cert_active": scope != "none",
                "dedicated_cert": scope == "dedicated",
                "state": live.get("state"),
                "reachable": bool(live.get("reachable")),
                "latency_ms": live.get("latency_ms"),
                "detail": live.get("detail"),
                "health": health,
            })

    counts = {
        HEALTH_HEALTHY: 0,
        HEALTH_PENDING: 0,
        HEALTH_DEGRADED: 0,
        HEALTH_DOWN: 0,
    }
    for host in hosts:
        counts[host["health"]] = counts.get(host["health"], 0) + 1

    project_issues: list[str] = []
    broken = [h for h in hosts if h["health"] in (HEALTH_DOWN, HEALTH_DEGRADED)]
    for host in broken[:5]:
        project_issues.append(
            f"{host['project']} ({host['kind']}) — {host['domain']}: {host.get('detail') or host['health']}"
        )
    if len(broken) > 5:
        project_issues.append(f"…and {len(broken) - 5} more failing project hostnames.")

    projects_row = {
        "key": "projects",
        "name": "Project SSL",
        "description": "Production and preview hostnames for deployed projects",
        "kind": "aggregate",
        "configured": bool(hosts),
        "health": worst_health([h["health"] for h in hosts]) if hosts else HEALTH_UNCONFIGURED,
        "issues": project_issues,
        "total": len(hosts),
        "counts": counts,
        "hosts": hosts,
        "detail": (
            f"{counts[HEALTH_HEALTHY]}/{len(hosts)} hostnames serving trusted HTTPS"
            if hosts else "no project hostnames configured"
        ),
    }

    services = [web, projects_row]
    return {
        "services": services,
        "overall": worst_health([s["health"] for s in services]),
        "counts": {
            "healthy": sum(1 for s in services if s["health"] == HEALTH_HEALTHY),
            "pending": sum(1 for s in services if s["health"] == HEALTH_PENDING),
            "degraded": sum(1 for s in services if s["health"] == HEALTH_DEGRADED),
            "down": sum(1 for s in services if s["health"] == HEALTH_DOWN),
            "unconfigured": sum(1 for s in services if s["health"] == HEALTH_UNCONFIGURED),
        },
    }


# ---------------------------------------------------------------------------
# Caddy server settings monitor
# ---------------------------------------------------------------------------

async def caddy_server_monitor() -> dict:
    """Monitor Caddy's server-side settings and health.

    Delegates to :mod:`syte.caddy_monitor`, which additionally probes the admin
    API, the :80/:443 listeners, the Caddyfile on disk and the certificate
    store. The flat keys the dashboard has always consumed (``installed``,
    ``active``, ``enabled``, ``version``, ``uptime_seconds``, ``config_path``,
    ``config_exists``, ``systemd_env_configured``,
    ``cloudflare_plugin_installed``) are preserved in the payload.
    """
    from syte.caddy_monitor import caddy_monitor

    return await caddy_monitor()


@dataclass
class SslOverview:
    """Aggregated SSL configuration + per-project certificate status."""

    caddy: dict = field(default_factory=dict)
    cloudflare: dict = field(default_factory=dict)
    gui: dict = field(default_factory=dict)
    almalinux: dict = field(default_factory=dict)
    caddy_monitor: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)
    projects: list[dict] = field(default_factory=list)
    pending_count: int = 0
    active_count: int = 0
    configured_count: int = 0
    caddy_pending: bool = False
    action_hints: list[str] = field(default_factory=list)


async def project_ssl_detail(project: dict) -> dict:
    """Detailed SSL status for one project (used by the SSL dashboard)."""
    summary = project_ssl_summary(project)
    production = summary["production"]
    preview = summary["preview"]
    return {
        "id": project.get("id"),
        "name": project.get("name") or project.get("id", "project"),
        "badge": summary["badge"],
        "badge_label": summary["badge_label"],
        "production": production,
        "preview": preview,
        "custom_tls_domain": normalize_domain(project.get("custom_tls_domain") or ""),
        "custom_tls_enabled": int(project.get("custom_tls_enabled") or 0) == 1,
    }


async def build_ssl_overview() -> dict:
    """Build a single aggregate SSL status payload for the dashboard."""
    from syte.certificates import cloudflare_tls_status
    from syte.database import list_projects, get_setting
    from syte.preview_domains import resolve_preview_zone
    from syte.ssl_debug import debug_endpoint

    overview = SslOverview(
        caddy={
            "installed": caddy_installed(),
            "active": await _caddy_active(),
        },
        cloudflare=await cloudflare_tls_status(),
        gui=production_ssl_status({"domain": await get_setting("gui_domain", "")}),
        projects=[],
    )

    overview.almalinux = await almalinux_monitor()
    overview.caddy_monitor = await caddy_server_monitor()
    overview.health = await service_health_monitor()

    preview_zone = await resolve_preview_zone()
    debug: list[dict] = []
    for project in await list_projects():
        detail = await project_ssl_detail(project)
        overview.projects.append(detail)
        production_ok = bool(detail["production"]["active"])
        preview_ok = bool(detail["preview"]["active"])
        if production_ok or preview_ok:
            overview.active_count += 1
        elif detail["production"]["configured"] or detail["preview"]["configured"]:
            overview.pending_count += 1
        if detail["production"]["configured"] or detail["preview"]["configured"]:
            overview.configured_count += 1

        proj_debug = {
            "project": detail["name"],
            "id": detail["id"],
            "badge": detail["badge"],
        }
        if detail["production"]["configured"]:
            proj_debug["production"] = await debug_endpoint(
                name="production",
                domain=detail["production"]["domain"],
                configured=True,
                cert_active=production_ok,
            )
        else:
            proj_debug["production"] = {
                "name": "production", "configured": False,
                "cert": False, "state": "not-configured", "domain": None,
            }
        if detail["preview"]["configured"]:
            proj_debug["preview"] = await debug_endpoint(
                name="preview",
                domain=detail["preview"]["domain"],
                configured=True,
                cert_active=preview_ok,
            )
        else:
            proj_debug["preview"] = {
                "name": "preview", "configured": False,
                "cert": False, "state": "not-configured", "domain": None,
            }
        debug.append(proj_debug)

        # Merge live serving state into the project detail so the dashboard's
        # badges reflect whether the cert is actually accepted + reachable, not
        # just whether a cert file exists.
        for key, dbg_key in (("production", "production"), ("preview", "preview")):
            row = proj_debug.get(dbg_key) or {}
            detail[key]["live_state"] = row.get("state")
            detail[key]["live_detail"] = row.get("detail")
            detail[key]["serving"] = row.get("reachable", False) or row.get("state") == "serving"

    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    overview_debug: list[dict] = []
    if gui_domain:
        overview_debug.append(await debug_endpoint(
            name="GUI",
            domain=gui_domain,
            configured=True,
            cert_active=bool(overview.gui.get("active")),
        ))

    # Surface everything the deep Caddy monitor found wrong — stale config,
    # missing :443 listener, unreachable admin API, untrusted certs.
    for problem in overview.caddy_monitor.get("problems", []):
        if problem not in overview.action_hints:
            overview.action_hints.append(problem)

    if overview.cloudflare.get("token_configured") and not overview.caddy["installed"]:
        overview.action_hints.append(
            "Caddy is not installed — HTTPS will not work. Install Caddy (sudo ./scripts/install.sh)."
        )
    if overview.cloudflare.get("token_configured") and not overview.caddy["active"]:
        overview.action_hints.append(
            "Caddy is not running — run 'Apply & resolve SSL' to start and reload it."
        )
    if (
        overview.cloudflare.get("token_configured")
        and not overview.cloudflare.get("ready")
    ):
        overview.action_hints.extend(overview.cloudflare.get("hints", []))

    if preview_zone and not overview.cloudflare.get("token_configured"):
        overview.action_hints.append(
            f"Preview HTTPS uses wildcard *.{preview_zone} via Cloudflare DNS — "
            "add a Cloudflare API token to auto-issue certificates."
        )

    # Detect a Caddy self-signed / placeholder wildcard cert: if present, every
    # subdomain in the zone is protected by a cert browsers reject → "cert error".
    wildcard_info = None
    if preview_zone:
        wildcard_info = stored_wildcard_cert(preview_zone)
        if wildcard_info and wildcard_info.get("self_signed"):
            overview.action_hints.append(
                f"The wildcard cert *.{preview_zone} is a Caddy self-signed placeholder "
                "(issuer: {issuer}) — every {zone} subdomain fails HTTPS. Re-issue it: "
                "ensure the Cloudflare token + DNS-01 are correct, then run 'Apply & resolve SSL'."
                .format(issuer=wildcard_info.get("issuer") or "Caddy Local Authority", zone=preview_zone)
            )

    overview.caddy_pending = not overview.caddy.get("active")
    try:
        from syte.config import settings as _settings
        _gui_port = _settings.port
    except Exception:  # noqa: BLE001
        _gui_port = 8787
    return {
        "ok": True,
        "caddy": overview.caddy,
        "cloudflare": overview.cloudflare,
        "gui": overview.gui,
        "almalinux_monitor": overview.almalinux,
        "caddy_monitor": overview.caddy_monitor,
        "health": overview.health,
        "projects": overview.projects,
        "debug": overview_debug,
        "projects_debug": debug,
        "wildcard_cert": wildcard_info,
        "custom_tls_host": await get_setting("custom_tls_host", ""),
        "custom_tls_port": await get_setting("custom_tls_port", ""),
        "gui_port": _gui_port,
        "totals": {
            "configured": overview.configured_count,
            "active": overview.active_count,
            "pending": overview.pending_count,
        },
        "caddy_pending": overview.caddy_pending,
        "action_hints": overview.action_hints,
    }


async def resolve_ssl_issues() -> dict:
    """Attempt to repair SSL: ensure Caddy is up and reload the proxy config."""
    from syte.certificates import apply_proxy_config, ensure_caddy

    messages: list[str] = []
    caddy_ok, caddy_msg = await asyncio.to_thread(ensure_caddy)
    messages.append(caddy_msg)
    proxy_ok, proxy_msg = await apply_proxy_config()
    messages.append(proxy_msg)

    overview = await build_ssl_overview()
    overview["resolved"] = proxy_ok and caddy_ok
    overview["messages"] = messages
    return overview
