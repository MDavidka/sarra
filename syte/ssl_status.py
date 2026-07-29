"""SSL / HTTPS status helpers for project dashboard."""

from __future__ import annotations

from pathlib import Path

from syte.caddy_routes import host_zone
from syte.domain_utils import build_https_url, normalize_domain


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
