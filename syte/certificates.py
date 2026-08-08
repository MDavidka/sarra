import asyncio
import ipaddress
import os
import shutil
import subprocess
from pathlib import Path

from syte.caddy_routes import (
    NINE_ROUTER_PUBLIC_HOST,
    NINE_ROUTER_UPSTREAM_DEFAULT,
    host_zone,
    render_9router_route,
    render_all_service_routes,
    render_litellm_api_route,
    render_managed_9router_route,
)
from syte.config import settings
from syte.database import get_setting, list_projects
from syte.domain_utils import is_safe_caddy_hostname, normalize_domain
from syte.litellm_config import LITELLM_HOST_PORT, LITELLM_PUBLIC_HOST
from syte.nine_router_manager import NINE_ROUTER_HOST_PORT
from syte.preview_domains import preview_frame_ancestors_csp

CADDY_DROPIN_DIR = Path("/etc/systemd/system/caddy.service.d")
CADDY_DROPIN_FILE = CADDY_DROPIN_DIR / "syte-cloudflare.conf"
# Canonical env path — must be stable regardless of settings.data_dir so the
# systemd EnvironmentFile drop-in and the file we write always point at the
# same location. Other code (and deployed drop-ins) reference this exact path.
CADDY_ENV_PATH = Path("/var/lib/syte/caddy.env")


def _run(cmd: list[str], timeout: float = 60.0, env: dict | None = None) -> tuple[int, str]:
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
            env=env,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output.strip()
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out after {timeout:g}s: {' '.join(cmd)}"
    except OSError as error:
        return 1, f"Could not run {' '.join(cmd)}: {error}"


def _caddy_env() -> dict | None:
    """Build an environment with the saved Cloudflare token for one-shot Caddy
    commands (validate/reload). The running systemd service loads the same
    values through its EnvironmentFile; these one-shot invocations need them
    explicitly or the DNS-01 placeholder resolves empty and validation fails.
    """
    env_path = CADDY_ENV_PATH
    try:
        if not env_path.is_file():
            return None
        merged = dict(os.environ)
        for line in env_path.read_text().splitlines():
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key:
                merged[key] = value.strip()
        return merged
    except OSError:
        return None


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
            caddy_env = _caddy_env()
            subprocess.Popen(
                ["caddy", "start", "--config", str(cfg), "--adapter", "caddyfile"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=caddy_env,
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
    code, out = _run(["caddy", "add-package", "github.com/caddy-dns/cloudflare"])
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
    env_path = CADDY_ENV_PATH
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
    env_path = CADDY_ENV_PATH
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


async def nine_router_backend_port() -> int:
    """Loopback port of the local 9Router AI gateway behind Caddy.

    Legacy helper — kept for backwards compatibility. New installs proxy
    9Router to a dedicated remote gateway host via ``nine_router_upstream``;
    this only applies when the legacy ``nine_router_backend_port`` setting is
    explicitly configured.
    """
    raw = (await get_setting("nine_router_backend_port", "")).strip()
    if not raw:
        return LITELLM_HOST_PORT
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return LITELLM_HOST_PORT
    if 1 <= port <= 65535:
        return port
    return LITELLM_HOST_PORT


def normalize_remote_nine_router_upstream(raw: str) -> str:
    """Validate a 9Router upstream and reject local/private destinations."""
    host, separator, port_str = (raw or "").strip().rpartition(":")
    if not separator:
        return ""
    host = normalize_domain(host)
    if not host or not is_safe_caddy_hostname(host):
        return ""
    lowered = host.lower()
    if lowered in {"localhost", "localhost.localdomain", "local"} or lowered.endswith((".localhost", ".local")):
        return ""
    try:
        port = int(port_str)
    except (TypeError, ValueError):
        return ""
    if not 1 <= port <= 65535:
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Hostnames are allowed when they are not obvious local-only names.
        return f"{host}:{port}"
    if address.version != 4 or not address.is_global:
        return ""
    return f"{address}:{port}"


async def nine_router_upstream() -> str:
    """Upstream host:port the 9Router gateway is proxied to.

    Defaults to ``65.75.203.134:20128`` — the dedicated gateway host. An
    explicit ``nine_router_upstream`` may override it with a remote hostname
    or global IPv4 address. Legacy local backend-port settings are deliberately
    ignored because they point Caddy at localhost and break the public route.
    """
    raw = (await get_setting("nine_router_upstream", "")).strip()
    upstream = normalize_remote_nine_router_upstream(raw)
    return upstream or NINE_ROUTER_UPSTREAM_DEFAULT


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

    managed_router_enabled = (await get_setting("nine_router_public_enabled", "0")).strip() == "1"
    if managed_router_enabled:
        # The Router tab temporarily owns api.sycord.site so the dashboard and
        # /v1 API share the origin advertised by the official Docker image.
        # Operators should configure a separate gui_domain before enabling it.
        lines.extend(
            render_managed_9router_route(
                NINE_ROUTER_PUBLIC_HOST,
                NINE_ROUTER_HOST_PORT,
                use_wildcard_tls=use_wildcard_tls,
            )
        )
    else:
        lines.extend(
            render_litellm_api_route(
                LITELLM_PUBLIC_HOST,
                LITELLM_HOST_PORT,
                use_wildcard_tls=use_wildcard_tls,
                gui_port=settings.port,
            )
        )

        # Keep the existing remote 9Router gateway route when the managed
        # container is not enabled. Its loopback TLS probe would conflict with
        # the local Docker binding while the managed service is running.
        from syte.ai_providers import NINE_ROUTER_API_BASE

        nine_host = normalize_domain(NINE_ROUTER_API_BASE)
        if is_safe_caddy_hostname(nine_host):
            lines.extend(
                render_9router_route(
                    nine_host,
                    await nine_router_upstream(),
                    use_wildcard_tls=use_wildcard_tls,
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

    # Global custom TLS host (e.g. dedicated sycord.site/zone SSL with its own
    # cert) — a standalone host block forwarding to a configured port.
    custom_host = normalize_domain(await get_setting("custom_tls_host", "") or "")
    custom_port_raw = (await get_setting("custom_tls_port", "") or "").strip()
    if custom_host and is_safe_caddy_hostname(custom_host):
        try:
            custom_port = int(custom_port_raw)
        except (TypeError, ValueError):
            custom_port = settings.port
        lines.extend([
            f"# Global custom TLS — {custom_host}",
            f"{custom_host} {{",
            f"    reverse_proxy 127.0.0.1:{custom_port}",
            "}",
            "",
        ])

    lines.append(f"# Public IP: {public_ip}")
    return "\n".join(lines)


async def apply_proxy_config() -> tuple[bool, str]:
    cf_messages = await apply_cloudflare_integration()
    config = await async_generate_caddyfile()
    config_path = settings.caddy_config_path
    fallback = settings.data_dir / "Caddyfile"
    env_path = await _write_caddy_env()

    # Fail loudly if a token is configured but the env file still isn't there —
    # otherwise Caddy starts with an empty CLOUDFLARE_API_TOKEN and DNS-01 fails
    # with Cloudflare "Authentication error", leaving a self-signed placeholder.
    token_set = bool((await get_setting("cloudflare_api_token", "")).strip())
    if token_set and not CADDY_ENV_PATH.is_file():
        return False, (
            f"Cloudflare token is saved but {CADDY_ENV_PATH} could not be written. "
            "DNS-01 wildcard TLS will fail. Check write permission to /var/lib/syte."
        )

    written = None
    write_errors: list[str] = []
    for target in (config_path, fallback):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(config)
            written = target
            break
        except OSError as exc:
            write_errors.append(f"{target}: {exc}")
            continue
    if written is None:
        detail = "; ".join(write_errors) or "permission denied"
        return False, f"Could not write Caddy configuration ({detail})."

    extra = ""
    if env_path:
        extra = f" Wildcard SSL env: {env_path}."
    if cf_messages:
        extra += " " + " ".join(cf_messages)

    if not shutil.which("caddy"):
        return True, (
            f"Caddy config saved to {written}. "
            "Install Caddy and run: sudo caddy reload --config " + str(written) + extra
        )

    caddy_env = _caddy_env()
    code, out = await asyncio.to_thread(
        _run, ["caddy", "validate", "--config", str(written)], env=caddy_env
    )
    if code != 0:
        return False, f"Invalid Caddy config: {out or 'validation failed'}"

    for cmd in (
        ["systemctl", "restart", "caddy"],
        ["systemctl", "reload", "caddy"],
        ["caddy", "reload", "--config", str(written)],
    ):
        cmd_env = caddy_env if cmd[0] == "caddy" else None
        code, out = await asyncio.to_thread(_run, cmd, env=cmd_env)
        if code == 0:
            caddy_ok, caddy_message = await asyncio.to_thread(ensure_caddy)
            if not caddy_ok:
                return False, f"Caddy command succeeded but Caddy is not active: {caddy_message}{extra}"
            return True, "Proxy configuration applied (production + preview SSL)." + extra

    caddy_ok, caddy_message = await asyncio.to_thread(ensure_caddy)
    if not caddy_ok:
        return False, f"Caddy configuration saved but Caddy could not be started: {caddy_message}{extra}"
    return True, (
        f"Caddy config saved to {written}; {caddy_message}" + extra
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
