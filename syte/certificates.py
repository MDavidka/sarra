import shutil
import subprocess

from syte.caddy_preview import render_preview_sections
from syte.config import settings
from syte.database import get_setting, list_projects
from syte.domain_utils import normalize_domain
from syte.preview_domains import preview_frame_ancestors_csp


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output.strip()
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}"


def ensure_caddy() -> tuple[bool, str]:
    """Ensure Caddy reverse proxy is enabled and running (24/7 GUI + domains)."""
    if not shutil.which("caddy"):
        return False, "Caddy not installed — install for HTTPS domains."

    messages = []
    for cmd in (
        ["systemctl", "enable", "caddy"],
        ["systemctl", "start", "caddy"],
    ):
        code, out = _run(cmd)
        if code != 0 and "not found" not in out.lower():
            messages.append(out)

    code, out = _run(["systemctl", "is-active", "caddy"])
    if code == 0:
        return True, "Caddy is running."

    config = settings.caddy_config_path
    fallback = settings.data_dir / "Caddyfile"
    cfg = config if config.exists() else fallback
    if cfg.exists():
        code, out = _run(["caddy", "run", "--config", str(cfg), "--adapter", "caddyfile"])
        if code == 0:
            return True, "Caddy started."

    return False, "; ".join(messages) or "Could not start Caddy."


async def _write_caddy_env() -> str | None:
    """Write Cloudflare token for Caddy DNS TLS (preview wildcards)."""
    token = (await get_setting("cloudflare_api_token", "")).strip()
    if not token:
        return None
    env_path = settings.data_dir / "caddy.env"
    env_path.write_text(f"CLOUDFLARE_API_TOKEN={token}\n")
    env_path.chmod(0o600)
    return str(env_path)


async def async_generate_caddyfile() -> str:
    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    public_ip = settings.resolved_public_ip
    email = settings.admin_email
    embed_mode = (await get_setting("preview_embed_mode", "any")).strip().lower()
    frame_csp = preview_frame_ancestors_csp(gui_domain, allow_any=embed_mode != "restricted")
    cf_token = (await get_setting("cloudflare_api_token", "")).strip()
    wildcard_tls = (await get_setting("preview_wildcard_tls", "auto")).strip().lower()
    use_wildcard_tls = wildcard_tls in ("1", "true", "yes", "on", "auto") and bool(cf_token)

    lines = [
        "# Syte-managed Caddy configuration",
        "# Auto-generated — do not edit manually",
        "# Direct IP access (http://IP:port) is served by Syte/apps — not Caddy.",
        "# Caddy only terminates TLS for named domains on :443.",
        "",
    ]

    if use_wildcard_tls:
        lines.extend([
            "# Preview wildcard TLS: caddy add-package github.com/caddy-dns/cloudflare",
            "# Caddy service needs: EnvironmentFile=/var/lib/syte/caddy.env",
            "",
        ])

    if email and "@" in email and not email.endswith("@localhost"):
        lines.extend([
            "{",
            f"    email {email}",
            "}",
            "",
        ])

    if gui_domain:
        lines.extend([
            f"{gui_domain} {{",
            f"    reverse_proxy 127.0.0.1:{settings.port}",
            "}",
            "",
        ])
    else:
        lines.extend([
            f"# GUI direct access: http://{public_ip}:{settings.port}",
            "",
        ])

    projects = await list_projects()
    for project in projects:
        port = project["port"]
        domain = normalize_domain(project.get("domain") or "")
        name = project["name"]

        if domain:
            lines.extend([
                f"{domain} {{",
                f"    reverse_proxy 127.0.0.1:{port}",
                "}",
                "",
            ])
        else:
            lines.extend([
                f"# {name} — direct: http://{public_ip}:{port}",
                "",
            ])

    lines.extend(
        render_preview_sections(
            projects,
            frame_csp=frame_csp,
            embed_mode=embed_mode,
            use_wildcard_tls=use_wildcard_tls,
        )
    )

    lines.append(f"# Public IP: {public_ip}")
    return "\n".join(lines)


async def apply_proxy_config() -> tuple[bool, str]:
    config = await async_generate_caddyfile()
    config_path = settings.caddy_config_path
    fallback = settings.data_dir / "Caddyfile"
    env_path = await _write_caddy_env()

    for target in (config_path, fallback):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(config)
            written = target
            break
        except PermissionError:
            continue
    else:
        return False, "Could not write Caddy configuration (permission denied)."

    extra = ""
    if env_path:
        extra = (
            f" Cloudflare DNS TLS env: {env_path} — "
            "ensure Caddy systemd unit has EnvironmentFile= that path."
        )

    if not shutil.which("caddy"):
        return True, (
            f"Caddy config saved to {written}. "
            "Install Caddy and run: sudo caddy reload --config " + str(written) + extra
        )

    code, out = _run(["caddy", "validate", "--config", str(written)])
    if code != 0:
        return False, f"Invalid Caddy config: {out or 'validation failed'}"

    for cmd in (
        ["systemctl", "reload", "caddy"],
        ["systemctl", "restart", "caddy"],
        ["caddy", "reload", "--config", str(written)],
    ):
        code, out = _run(cmd)
        if code == 0:
            ensure_caddy()
            return True, "Proxy configuration applied." + extra

    ensure_caddy()
    return True, (
        f"Caddy config saved to {written}. "
        "Run: sudo systemctl restart caddy" + extra
    )


async def set_gui_domain(domain: str, email: str) -> tuple[bool, str]:
    """Configure custom domain for the Syte web GUI via Caddy auto-HTTPS."""
    if email:
        settings.admin_email = email

    ok, proxy_msg = await apply_proxy_config()
    if ok:
        return True, (
            f"GUI domain set to {domain}. "
            f"Caddy will issue a TLS certificate automatically once DNS points to this server.\n"
            f"{proxy_msg}"
        )
    return False, proxy_msg
