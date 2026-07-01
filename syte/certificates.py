import subprocess
from pathlib import Path

from syte.config import settings
from syte.database import list_projects


async def async_generate_caddyfile() -> str:
    lines = [
        "# Syte-managed Caddy configuration",
        "# Auto-generated — do not edit manually",
        "",
        f":{settings.port} {{",
        "    reverse_proxy 127.0.0.1:8787",
        "}",
        "",
    ]

    public_ip = settings.resolved_public_ip
    projects = await list_projects()

    for project in projects:
        port = project["port"]
        domain = project.get("domain")
        name = project["name"]

        if domain:
            lines.extend([
                f"{domain} {{",
                f"    reverse_proxy 127.0.0.1:{port}",
                f"    tls {settings.admin_email}",
                "}",
                "",
            ])
        else:
            lines.extend([
                f"# {name} — http://{public_ip}:{port}",
                f":{port} {{",
                f"    reverse_proxy 127.0.0.1:{port}",
                "}",
                "",
            ])

    lines.append(f"# Public IP: {public_ip}")
    return "\n".join(lines)


async def apply_proxy_config() -> tuple[bool, str]:
    config = await async_generate_caddyfile()
    config_path = settings.caddy_config_path

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config)

        result = subprocess.run(
            ["caddy", "reload", "--config", str(config_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, "Proxy configuration applied."

        result = subprocess.run(
            ["caddy", "validate", "--config", str(config_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, "Caddy config written. Run: sudo systemctl reload caddy"

        return True, f"Config saved to {config_path}. Install Caddy to enable HTTPS."
    except PermissionError:
        fallback = settings.data_dir / "Caddyfile"
        fallback.write_text(config)
        return True, f"Saved to {fallback} (no write access to {config_path})."


async def issue_certificate(domain: str, email: str) -> tuple[bool, str]:
    """Request TLS certificate for a custom domain via Caddy or certbot."""
    settings.admin_email = email

    result = subprocess.run(
        [
            "certbot", "certonly", "--standalone",
            "-d", domain,
            "--email", email,
            "--agree-tos",
            "--non-interactive",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, f"Certificate issued for {domain}."

    ok, msg = await apply_proxy_config()
    if ok:
        return True, (
            f"Caddy will auto-issue certificate for {domain} on next reload. "
            "Ensure DNS points to this server."
        )
    return False, result.stderr or "Certificate issuance failed. Install certbot or caddy."
