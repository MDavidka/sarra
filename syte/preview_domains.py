"""Preview URL and domain resolution (HTTPS via connected domain)."""

from syte.config import settings
from syte.database import get_setting
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain


async def resolve_preview_domain(project: dict) -> str:
    """
    Build preview hostname from connected domain:
    - Project domain app.example.com → preview.app.example.com
    - GUI domain sycord.site only → preview-{uuid}.sycord.site
    """
    project_domain = normalize_domain(project.get("domain") or "")
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))

    if project_domain:
        if project_domain.startswith("preview."):
            return project_domain
        return f"preview.{project_domain}"

    if gui_domain:
        slug = project["id"].lower().replace("_", "-")[:40]
        return f"preview-{slug}.{gui_domain}"

    return ""


def build_preview_urls(project: dict) -> dict:
    """Primary preview_url uses HTTPS domain when configured."""
    preview_port = project.get("preview_port")
    domain = normalize_domain(project.get("preview_domain") or "")
    ip = settings.resolved_public_ip

    direct = build_direct_url(ip, int(preview_port)) if preview_port else ""
    domain_url = build_https_url(domain) if domain else ""
    primary = domain_url or direct

    return {
        "preview_url": primary,
        "preview_domain": domain,
        "preview_domain_url": domain_url,
        "preview_direct_url": direct,
    }


def preview_dns_hint(preview_domain: str, connected_domain: str) -> str:
    if not preview_domain:
        return (
            "No domain configured. Set a project domain (POST /api/set_domain) "
            "or GUI domain in Settings for HTTPS preview URLs."
        )
    return (
        f"Point DNS A record {preview_domain} → your server IP "
        f"(same as {connected_domain}). Caddy issues TLS automatically."
    )
