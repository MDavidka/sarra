"""Preview URL and domain resolution (HTTPS via wildcard on GUI domain)."""

import random
import string

from syte.config import settings
from syte.database import get_setting
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.workspace import slugify


def _preview_base_domain(gui_domain: str) -> str:
    """Use root zone for previews (sycord.site) so *.sycord.site wildcard SSL works."""
    parts = gui_domain.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return gui_domain


def _normalize_preview_zone(zone: str) -> str:
    """sycord.com apex lives on Vercel — Syte preview TLS uses sycord.site wildcard."""
    zone = normalize_domain(zone)
    if zone in ("sycord.com", "www.sycord.com"):
        return "sycord.site"
    return zone


def is_preview_hostname(domain: str) -> bool:
    domain = normalize_domain(domain)
    return bool(domain and domain.startswith("preview") and "." in domain)


async def resolve_preview_zone() -> str:
    """
    Wildcard DNS zone for preview hostnames.
    Uses preview_base_domain when set, else derives from GUI domain.
    When GUI is on sycord.com, defaults to sycord.site (Cloudflare-friendly wildcard zone).
    """
    custom = normalize_domain(await get_setting("preview_base_domain", ""))
    if custom:
        return _normalize_preview_zone(custom)
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    if not gui_domain:
        return ""
    if gui_domain == "sycord.com" or gui_domain.endswith(".sycord.com"):
        return "sycord.site"
    return _preview_base_domain(gui_domain)


async def resolve_preview_domain(project: dict) -> str:
    """
    Stable preview hostname — assigned once per project, never rotated on restart.

    preview{random_letter}-{appname}.{preview_zone}
    Example: previewk-mysite.sycord.site
    """
    existing = normalize_domain(project.get("preview_domain") or "")
    if is_preview_hostname(existing):
        return existing

    base_zone = await resolve_preview_zone()
    if not base_zone:
        return ""

    app_slug = slugify(project.get("name") or project.get("id", "app"))[:32]
    letter = random.choice(string.ascii_lowercase)
    return f"preview{letter}-{app_slug}.{base_zone}"


async def resolve_production_domain(project: dict) -> str:
    """
    Build production hostname on the preview zone when none is set:
    {app-slug}.{zone}

    Example: mysite.sycord.site
    """
    base_zone = await resolve_preview_zone()
    if not base_zone:
        return ""

    existing = normalize_domain(project.get("domain") or "")
    if existing:
        return existing

    app_slug = slugify(project.get("name") or project.get("id", "app"))[:32]
    return f"{app_slug}.{base_zone}"


def preview_zone_for_domain(domain: str) -> str:
    domain = normalize_domain(domain)
    if not domain or "." not in domain:
        return ""
    return domain.split(".", 1)[-1]


def build_preview_urls(project: dict) -> dict:
    """Primary preview_url uses HTTPS domain when configured and TLS is up."""
    from syte.preview_iframe import probe_https_available

    preview_port = project.get("preview_port")
    domain = normalize_domain(project.get("preview_domain") or "")
    ip = settings.resolved_public_ip

    direct = build_direct_url(ip, int(preview_port)) if preview_port else ""
    domain_url = build_https_url(domain) if domain else ""
    tls_ok = probe_https_available(domain_url) if domain_url else False

    primary = domain_url or direct
    tls_hint = ""
    if domain_url and not tls_ok:
        zone = preview_zone_for_domain(domain)
        tls_hint = (
            f"HTTPS failed for {domain} — Caddy needs wildcard *.{zone} TLS. "
            "Set Cloudflare API token in Syte Settings and ensure *.{zone} DNS → server IP. "
            "Preview domain is kept stable — fix TLS without changing the hostname."
        )

    return {
        "preview_url": primary,
        "preview_domain": domain,
        "preview_domain_url": domain_url,
        "preview_direct_url": direct,
        "preview_tls_ok": tls_ok if domain_url else False,
        "preview_tls_hint": tls_hint,
        "preview_fetch_url": domain_url if tls_ok else direct,
        "preview_embed_url": domain_url if tls_ok else "",
    }


def preview_dns_hint(preview_domain: str, base_zone: str = "") -> str:
    if not preview_domain:
        return (
            "Set preview base domain in Settings (or GUI domain) for HTTPS preview URLs. "
            "Requires wildcard DNS *.{zone} → server IP."
        )
    zone = base_zone or preview_domain.split(".", 1)[-1] if "." in preview_domain else preview_domain
    return (
        f"Automatic SSL via wildcard *.{zone} — no per-preview DNS record needed. "
        f"Ensure *.{zone} (or {zone}) points to this server."
    )


def preview_frame_ancestors_csp(gui_domain: str = "", *, allow_any: bool = False) -> str:
    """CSP so *.sycord.com previews embed in sycord.com (and any parent when allow_any).

    Default is restricted (no ``*``). Set ``allow_any=True`` / ``preview_embed_mode=any``
    only when public arbitrary-origin embedding is intentionally required.
    """
    ancestors = [
        "'self'",
        "https://sycord.com",
        "https://www.sycord.com",
        "https://*.sycord.com",
    ]
    if allow_any:
        ancestors.append("*")
    gui_domain = normalize_domain(gui_domain)
    if gui_domain:
        ancestors.append(f"https://{gui_domain}")
        ancestors.append(f"https://*.{gui_domain}")
        base = _preview_base_domain(gui_domain)
        if base != gui_domain:
            ancestors.append(f"https://{base}")
            ancestors.append(f"https://*.{base}")
    ancestors.extend(["http://localhost:*", "https://localhost:*"])
    return "frame-ancestors " + " ".join(ancestors)
