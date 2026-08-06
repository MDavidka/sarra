"""SSL / HTTPS status helpers for the SSL dashboard."""

from __future__ import annotations

import asyncio
import subprocess
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


def _caddy_has_cert(hostname: str) -> bool:
    """Best-effort: check if Caddy stored a cert for this hostname."""
    cert_root = _cert_dir()
    if not cert_root:
        return False
    host = normalize_domain(hostname)
    if not host:
        return False
    for path in cert_root.rglob("*.crt"):
        if host in path.parent.name or host in path.name:
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



def litellm_api_ssl_status() -> dict:
    """Return certificate status for the public Syra LiteLLM API endpoint."""
    from syte.litellm_config import LITELLM_PUBLIC_API_URL, LITELLM_PUBLIC_HOST

    active = _caddy_has_cert(LITELLM_PUBLIC_HOST)
    return {
        "configured": True,
        "active": active,
        "domain": LITELLM_PUBLIC_HOST,
        "url": LITELLM_PUBLIC_API_URL,
        "label": "HTTPS" if active else "SSL pending",
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


@dataclass
class SslOverview:
    """Aggregated SSL configuration + per-project certificate status."""

    caddy: dict = field(default_factory=dict)
    cloudflare: dict = field(default_factory=dict)
    gui: dict = field(default_factory=dict)
    litellm: dict = field(default_factory=dict)
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
    from syte.litellm_config import LITELLM_PUBLIC_HOST
    from syte.preview_domains import resolve_preview_zone
    from syte.ssl_debug import debug_endpoint

    overview = SslOverview(
        caddy={
            "installed": caddy_installed(),
            "active": await _caddy_active(),
        },
        cloudflare=await cloudflare_tls_status(),
        gui=production_ssl_status(
            {"domain": await get_setting("gui_domain", "") or LITELLM_PUBLIC_HOST}
        ),
        projects=[],
    )

    overview.litellm = litellm_api_ssl_status()

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

    gui_domain = await get_setting("gui_domain", "") or LITELLM_PUBLIC_HOST
    overview_debug = []
    overview_debug.append(await debug_endpoint(
        name="GUI",
        domain=gui_domain,
        configured=True,
        cert_active=bool(overview.gui.get("active")),
    ))
    overview_debug.append(await debug_endpoint(
        name="LiteLLM API",
        domain=LITELLM_PUBLIC_HOST,
        configured=True,
        cert_active=bool(overview.litellm.get("active")),
    ))
    # Known external AI-router base — referenced by Syte but not proxied by this
    # Caddy instance unless explicitly configured. Surfaces as a diagnostic.
    try:
        from syte.ai_providers import NINE_ROUTER_API_BASE
        _nine_host = normalize_domain(NINE_ROUTER_API_BASE)
    except Exception:  # noqa: BLE001
        _nine_host = "9router.sycord.site"
    overview_debug.append(await debug_endpoint(
        name="9Router (external)",
        domain=_nine_host,
        configured=bool(_nine_host and is_safe_caddy_hostname(_nine_host)),
        cert_active=False,
        extra="External AI-router base referenced by Syte; ensure DNS and any reverse proxy are configured if it must be reachable.",
    ))

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
        "litellm": overview.litellm,
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
