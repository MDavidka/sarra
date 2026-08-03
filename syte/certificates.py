import asyncio
import os
import shutil
import stat
import subprocess
from pathlib import Path
from uuid import uuid4

from syte.caddy_routes import (
    host_zone,
    render_all_service_routes,
    render_litellm_api_route,
)
from syte.config import settings
from syte.database import get_setting, list_projects
from syte.domain_utils import normalize_domain
from syte.litellm_config import LITELLM_HOST_PORT, LITELLM_PUBLIC_HOST
from syte.preview_domains import preview_frame_ancestors_csp

CADDY_DROPIN_DIR = Path("/etc/systemd/system/caddy.service.d")
CADDY_DROPIN_FILE = CADDY_DROPIN_DIR / "syte-cloudflare.conf"


def _run(cmd: list[str], timeout: float = 60.0) -> tuple[int, str]:
    """Run a command, converting every failure into an inspectable result.

    Any uncaught OSError here would surface as an opaque HTTP 500 in the GUI,
    and a missing timeout could hang an operator request indefinitely.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output.strip()
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out after {timeout:g}s: {' '.join(cmd)}"
    except OSError as error:
        return 1, f"Could not run {' '.join(cmd)}: {error}"


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
        # `caddy run` stays in the foreground; never wait on it inside a request.
        try:
            subprocess.Popen(
                ["caddy", "start", "--config", str(cfg), "--adapter", "caddyfile"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, "Caddy started."
        except OSError as error:
            messages.append(f"Could not start Caddy directly: {error}")

    return False, "; ".join(messages) or "Could not start Caddy."


def caddy_has_cloudflare_plugin() -> bool:
    code, out = _run(["caddy", "list-modules"])
    if code != 0:
        return False
    return "dns.providers.cloudflare" in out


def ensure_caddy_cloudflare_plugin() -> tuple[bool, str]:
    if not shutil.which("caddy"):
        return False, "Caddy not installed."
    if caddy_has_cloudflare_plugin():
        return True, "Caddy Cloudflare DNS plugin is installed."
    code, _out = _run(["caddy", "add-package", "github.com/caddy-dns/cloudflare"])
    if code == 0 and caddy_has_cloudflare_plugin():
        return True, "Installed Caddy Cloudflare DNS plugin."
    return (
        False,
        "Install Caddy Cloudflare plugin: caddy add-package github.com/caddy-dns/cloudflare",
    )


def ensure_caddy_systemd_env(env_path: str) -> tuple[bool, str]:
    if not shutil.which("systemctl"):
        return False, "systemctl not available."
    dropin = (
        "[Service]\n"
        f"EnvironmentFile={env_path}\n"
    )
    try:
        CADDY_DROPIN_DIR.mkdir(parents=True, exist_ok=True)
        if CADDY_DROPIN_FILE.exists() and CADDY_DROPIN_FILE.read_text() == dropin:
            return True, "Caddy systemd EnvironmentFile already configured."
        CADDY_DROPIN_FILE.write_text(dropin)
        _run(["systemctl", "daemon-reload"])
        return True, f"Caddy systemd EnvironmentFile set to {env_path}"
    except (OSError, PermissionError) as exc:
        return (
            False,
            f"Add to Caddy systemd manually: EnvironmentFile={env_path} ({exc})",
        )


async def _write_caddy_env() -> str | None:
    """Write Cloudflare token for Caddy DNS TLS (wildcard production + preview)."""
    token = (await get_setting("cloudflare_api_token", "")).strip()
    if not token:
        return None
    env_path = settings.data_dir / "caddy.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(f"CLOUDFLARE_API_TOKEN={token}\n")
    env_path.chmod(0o600)
    return str(env_path)


async def apply_cloudflare_integration() -> list[str]:
    """Install Caddy DNS plugin and systemd env when a Cloudflare token is saved."""
    token = (await get_setting("cloudflare_api_token", "")).strip()
    if not token:
        return []
    messages: list[str] = []
    ok, msg = await asyncio.to_thread(ensure_caddy_cloudflare_plugin)
    messages.append(msg if ok else f"Cloudflare plugin: {msg}")
    env_path = await _write_caddy_env()
    if env_path:
        ok, msg = await asyncio.to_thread(ensure_caddy_systemd_env, env_path)
        messages.append(msg if ok else f"Systemd env: {msg}")
    return messages


async def cloudflare_tls_status() -> dict:
    token_set = bool((await get_setting("cloudflare_api_token", "")).strip())
    env_path = settings.data_dir / "caddy.env"
    env_written = env_path.is_file() and token_set
    wildcard_enabled = await _use_wildcard_tls()
    plugin = caddy_has_cloudflare_plugin()
    dropin = CADDY_DROPIN_FILE.is_file()
    hints: list[str] = []
    if token_set and not plugin:
        hints.append("Run: caddy add-package github.com/caddy-dns/cloudflare")
    if token_set and env_written and not dropin:
        hints.append(f"Point Caddy systemd at EnvironmentFile={env_path}")
    ready = token_set and env_written and wildcard_enabled and plugin
    return {
        "token_configured": token_set,
        "env_file_written": env_written,
        "env_file_path": str(env_path) if env_written else None,
        "wildcard_tls_enabled": wildcard_enabled,
        "caddy_plugin_installed": plugin,
        "systemd_env_configured": dropin,
        "ready": ready,
        "hints": hints,
    }


async def _use_wildcard_tls() -> bool:
    cf_token = (await get_setting("cloudflare_api_token", "")).strip()
    mode = (await get_setting("preview_wildcard_tls", "auto")).strip().lower()
    return bool(cf_token) and mode in ("1", "true", "yes", "on", "auto")


async def async_generate_caddyfile() -> str:
    from syte.caddy_routes import preview_cors_origin

    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    public_ip = settings.resolved_public_ip
    email = settings.admin_email
    # Default restricted: only sycord.com + GUI domain may embed previews.
    embed_mode = (await get_setting("preview_embed_mode", "restricted")).strip().lower()
    frame_csp = preview_frame_ancestors_csp(gui_domain, allow_any=embed_mode == "any")
    cors_origin = preview_cors_origin(gui_domain)
    use_wildcard_tls = await _use_wildcard_tls()

    lines = [
        "# Syte-managed Caddy configuration",
        "# Auto-generated — do not edit manually",
        "# Production + preview HTTPS via Caddy (wildcard DNS TLS when Cloudflare token set).",
        "",
    ]

    if use_wildcard_tls:
        lines.extend([
            "# Wildcard SSL: caddy add-package github.com/caddy-dns/cloudflare",
            "# Caddy systemd: EnvironmentFile=/var/lib/syte/caddy.env",
            "",
        ])

    if email and "@" in email and not email.endswith("@localhost"):
        lines.extend([
            "{",
            f"    email {email}",
            "}",
            "",
        ])

    if gui_domain and gui_domain != LITELLM_PUBLIC_HOST:
        if gui_domain == host_zone(gui_domain) or not use_wildcard_tls:
            lines.extend([
                f"{gui_domain} {{",
                f"    reverse_proxy 127.0.0.1:{settings.port}",
                "}",
                "",
            ])
        else:
            lines.extend([
                f"# GUI — {gui_domain}",
                f"{gui_domain} {{",
                f"    reverse_proxy 127.0.0.1:{settings.port}",
                "}",
                "",
            ])
    else:
        lines.extend([
            f"# GUI: https://{LITELLM_PUBLIC_HOST}/",
            "",
        ])

    lines.extend(
        render_litellm_api_route(
            LITELLM_PUBLIC_HOST,
            LITELLM_HOST_PORT,
            use_wildcard_tls=use_wildcard_tls,
            gui_port=settings.port,
        )
    )

    projects = await list_projects()
    lines.extend(
        render_all_service_routes(
            projects,
            frame_csp=frame_csp,
            use_wildcard_tls=use_wildcard_tls,
            cors_origin=cors_origin,
        )
    )

    lines.append(f"# Public IP: {public_ip}")
    return "\n".join(lines)


async def apply_proxy_config() -> tuple[bool, str]:
    cf_messages = await apply_cloudflare_integration()
    config = await async_generate_caddyfile()
    config_path = settings.caddy_config_path
    fallback = settings.data_dir / "Caddyfile"
    env_path = await _write_caddy_env()

    extra = ""
    if env_path:
        extra = f" Wildcard SSL env: {env_path}."
    if cf_messages:
        extra += " " + " ".join(cf_messages)

    if not shutil.which("caddy"):
        return False, (
            "Caddy is not installed; the active proxy configuration was not changed. "
            "Install Caddy before publishing HTTPS routes." + extra
        )

    written: Path | None = None
    write_errors: list[str] = []
    for target in (config_path, fallback):
        candidate = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(config)
            if target.exists():
                target_stat = target.stat()
                os.chown(candidate, target_stat.st_uid, target_stat.st_gid)
                candidate.chmod(stat.S_IMODE(target_stat.st_mode))

            code, out = await asyncio.to_thread(
                _run,
                [
                    "caddy",
                    "validate",
                    "--config",
                    str(candidate),
                    "--adapter",
                    "caddyfile",
                ],
            )
            if code != 0:
                return False, (
                    "Invalid Caddy config; the active configuration was preserved: "
                    f"{out or 'validation failed'}"
                )

            candidate.replace(target)
            written = target
            break
        except OSError as exc:
            write_errors.append(f"{target}: {exc}")
        finally:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass

    if written is None:
        detail = "; ".join(write_errors) or "permission denied"
        return False, f"Could not write Caddy configuration ({detail})."

    command_errors: list[str] = []
    for cmd in (
        [
            "caddy",
            "reload",
            "--config",
            str(written),
            "--adapter",
            "caddyfile",
        ],
        ["systemctl", "reload", "caddy"],
        ["systemctl", "restart", "caddy"],
    ):
        code, out = await asyncio.to_thread(_run, cmd)
        if code != 0:
            command_errors.append(f"{' '.join(cmd)}: {out or f'exit {code}'}")
            continue

        caddy_ok, caddy_message = await asyncio.to_thread(ensure_caddy)
        if not caddy_ok:
            return False, (
                "Caddy command succeeded but Caddy is not active: "
                f"{caddy_message}{extra}"
            )
        return True, "Proxy configuration applied (production + preview SSL)." + extra

    detail = "; ".join(command_errors)
    return False, (
        f"Caddy configuration saved to {written}, but reload and restart failed"
        f" ({detail}).{extra}"
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
