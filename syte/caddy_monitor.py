"""Deep runtime monitor for the Caddy reverse proxy.

``ssl_status.caddy_server_monitor`` answers "is Caddy installed and enabled".
That is not enough to debug a broken HTTPS surface: Caddy can be *active* under
systemd while holding a stale configuration, while nothing listens on :443, or
while its admin API is unreachable because the process wedged.

This module probes the pieces an operator actually needs:

* the systemd unit state (active / enabled / uptime / restart count),
* whether something is really listening on :80 and :443,
* the admin API on 127.0.0.1:2019 and the config Caddy is *running*,
* a summary of the Caddyfile on disk (size, mtime, host blocks) so a config
  that was written but never loaded is obvious,
* the certificate store (how many certs, which issuers).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

CADDY_ADMIN_HOST = "127.0.0.1"
CADDY_ADMIN_PORT = 2019
CADDY_ADMIN_URL = f"http://{CADDY_ADMIN_HOST}:{CADDY_ADMIN_PORT}"

# Ports Caddy must own for public HTTP + HTTPS to work at all.
PUBLIC_PORTS = (80, 443)

def parse_host_blocks(text: str) -> list[str]:
    """Extract the site addresses of every top-level host block in a Caddyfile.

    A host block header is an *unindented* line ending in ``{`` — indentation
    reliably separates site addresses from directive sub-blocks (``tls {``,
    ``handle {``, ``reverse_proxy … {``). The global options block (a bare
    ``{``) is skipped, as are comments.
    """
    blocks: list[str] = []
    for raw_line in text.splitlines():
        # Only column-0 lines can open a site block.
        if not raw_line or raw_line[0].isspace():
            continue
        line = raw_line.strip()
        if not line.endswith("{") or line.startswith("#"):
            continue
        address = line[:-1].strip()
        if not address:
            # Bare "{" — Caddy's global options block, not a site.
            continue
        blocks.append(address)
    return blocks


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    """Run a command, returning (exit code, combined output)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 255, ""
    return result.returncode, (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------------------
# systemd unit
# ---------------------------------------------------------------------------


def _systemd_properties(unit: str = "caddy") -> dict[str, str]:
    """Read the systemd properties we care about in a single call."""
    if not shutil.which("systemctl"):
        return {}
    code, out = _run([
        "systemctl", "show", unit,
        "-p", "ActiveState",
        "-p", "SubState",
        "-p", "UnitFileState",
        "-p", "NRestarts",
        "-p", "MainPID",
        "-p", "ActiveEnterTimestampMonotonic",
        "-p", "ExecMainStatus",
        "-p", "FragmentPath",
    ])
    if code != 0:
        return {}
    props: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            props[key] = value.strip()
    return props


def _uptime_from_props(props: dict[str, str]) -> float | None:
    raw = props.get("ActiveEnterTimestampMonotonic", "")
    if not raw:
        return None
    try:
        started = float(raw)
    except (TypeError, ValueError):
        return None
    if started <= 0:
        return None
    return max(0.0, time.monotonic() - started / 1_000_000.0)


def systemd_unit_state(unit: str = "caddy") -> dict:
    """Summarise Caddy's systemd unit."""
    props = _systemd_properties(unit)
    if not props:
        return {
            "managed": False,
            "active": False,
            "enabled": False,
            "state": "unknown",
            "sub_state": "",
            "restarts": None,
            "main_pid": None,
            "uptime_seconds": None,
            "unit_path": "",
        }
    active_state = props.get("ActiveState", "")
    try:
        restarts = int(props.get("NRestarts", "") or 0)
    except (TypeError, ValueError):
        restarts = None
    try:
        main_pid = int(props.get("MainPID", "") or 0) or None
    except (TypeError, ValueError):
        main_pid = None
    uptime = _uptime_from_props(props)
    return {
        "managed": True,
        "active": active_state == "active",
        "enabled": props.get("UnitFileState", "") == "enabled",
        "state": active_state or "unknown",
        "sub_state": props.get("SubState", ""),
        "restarts": restarts,
        "main_pid": main_pid,
        "uptime_seconds": round(uptime, 1) if uptime is not None else None,
        "unit_path": props.get("FragmentPath", ""),
        "exec_status": props.get("ExecMainStatus", ""),
    }


# ---------------------------------------------------------------------------
# Listening ports
# ---------------------------------------------------------------------------


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    """True when a TCP connect to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _port_owner(port: int) -> str:
    """Best-effort process name holding a listening port (needs ss)."""
    if not shutil.which("ss"):
        return ""
    code, out = _run(["ss", "-tlnp"])
    if code != 0:
        return ""
    for line in out.splitlines():
        if f":{port} " not in line:
            continue
        match = re.search(r'users:\(\("([^"]+)"', line)
        if match:
            return match.group(1)
    return ""


def port_listeners() -> list[dict]:
    """Report whether :80 and :443 are being served, and by what."""
    listeners: list[dict] = []
    for port in PUBLIC_PORTS:
        listening = _port_open(port)
        owner = _port_owner(port)
        listeners.append({
            "port": port,
            "listening": listening,
            "owner": owner,
            # A non-Caddy owner explains "Caddy is running but HTTPS is dead".
            "owned_by_caddy": owner.lower().startswith("caddy") if owner else None,
            "role": "HTTP / ACME challenges" if port == 80 else "HTTPS",
        })
    return listeners


# ---------------------------------------------------------------------------
# Admin API (the config Caddy is actually running)
# ---------------------------------------------------------------------------


async def admin_api_status(timeout: float = 3.0) -> dict:
    """Query Caddy's local admin API for the live, loaded configuration.

    The admin API is the only source of truth for what Caddy is *running*; the
    Caddyfile on disk is merely what it was last asked to run.
    """
    if not _port_open(CADDY_ADMIN_PORT):
        return {
            "reachable": False,
            "url": CADDY_ADMIN_URL,
            "detail": f"nothing listening on {CADDY_ADMIN_HOST}:{CADDY_ADMIN_PORT}",
            "loaded_hosts": [],
            "loaded_host_count": 0,
            "automation_policies": 0,
        }

    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{CADDY_ADMIN_URL}/config/")
        if response.status_code >= 400:
            return {
                "reachable": False,
                "url": CADDY_ADMIN_URL,
                "detail": f"admin API returned HTTP {response.status_code}",
                "loaded_hosts": [],
                "loaded_host_count": 0,
                "automation_policies": 0,
            }
        config = response.json()
    except Exception as error:  # noqa: BLE001 - best-effort diagnostics
        return {
            "reachable": False,
            "url": CADDY_ADMIN_URL,
            "detail": f"{type(error).__name__}: {error}",
            "loaded_hosts": [],
            "loaded_host_count": 0,
            "automation_policies": 0,
        }

    hosts = _loaded_hosts(config)
    return {
        "reachable": True,
        "url": CADDY_ADMIN_URL,
        "detail": "admin API responding",
        "loaded_hosts": hosts,
        "loaded_host_count": len(hosts),
        "automation_policies": _automation_policy_count(config),
        "config_empty": not bool(config),
    }


def _loaded_hosts(config: dict | None) -> list[str]:
    """Extract every host matcher from a live Caddy JSON config."""
    if not isinstance(config, dict):
        return []
    hosts: list[str] = []
    servers = (((config.get("apps") or {}).get("http") or {}).get("servers") or {})
    if not isinstance(servers, dict):
        return []
    for server in servers.values():
        if not isinstance(server, dict):
            continue
        for route in server.get("routes") or []:
            if not isinstance(route, dict):
                continue
            for match in route.get("match") or []:
                if not isinstance(match, dict):
                    continue
                for host in match.get("host") or []:
                    if host not in hosts:
                        hosts.append(host)
    return sorted(hosts)


def _automation_policy_count(config: dict | None) -> int:
    """Number of TLS automation policies — i.e. distinct cert issuance rules."""
    if not isinstance(config, dict):
        return 0
    automation = (((config.get("apps") or {}).get("tls") or {}).get("automation") or {})
    policies = automation.get("policies") or []
    return len(policies) if isinstance(policies, list) else 0


# ---------------------------------------------------------------------------
# Caddyfile on disk
# ---------------------------------------------------------------------------


def caddyfile_summary(path: Path) -> dict:
    """Summarise the Caddyfile on disk so a stale/unloaded config is visible."""
    if not path or not path.is_file():
        return {
            "path": str(path) if path else "",
            "exists": False,
            "size_bytes": 0,
            "modified": None,
            "age_seconds": None,
            "host_blocks": [],
            "host_block_count": 0,
            "dns_challenge_blocks": 0,
        }
    try:
        text = path.read_text()
        stat = path.stat()
    except OSError as error:
        return {
            "path": str(path),
            "exists": True,
            "readable": False,
            "detail": str(error),
            "size_bytes": 0,
            "modified": None,
            "age_seconds": None,
            "host_blocks": [],
            "host_block_count": 0,
            "dns_challenge_blocks": 0,
        }

    hosts = parse_host_blocks(text)
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return {
        "path": str(path),
        "exists": True,
        "readable": True,
        "size_bytes": stat.st_size,
        "modified": modified.isoformat(),
        "age_seconds": round(max(0.0, time.time() - stat.st_mtime), 1),
        "host_blocks": hosts,
        "host_block_count": len(hosts),
        "dns_challenge_blocks": text.count("dns cloudflare"),
        "managed_by_syte": "Syte-managed Caddy configuration" in text,
    }


# ---------------------------------------------------------------------------
# Certificate store
# ---------------------------------------------------------------------------


def certificate_store_summary() -> dict:
    """Count stored certificates and group them by issuing authority."""
    from syte.ssl_status import _cert_dir, _read_cert_meta

    cert_root = _cert_dir()
    if not cert_root:
        return {
            "path": None,
            "exists": False,
            "total": 0,
            "dedicated": 0,
            "wildcard": 0,
            "self_signed": 0,
            "issuers": {},
        }

    total = 0
    wildcard = 0
    self_signed = 0
    issuers: dict[str, int] = {}
    for path in sorted(cert_root.rglob("*.crt")):
        total += 1
        if "wildcard_." in path.name or "wildcard_." in path.parent.name:
            wildcard += 1
        issuer, _valid_until = _read_cert_meta(path)
        label = (issuer or "unknown").strip()
        if "Caddy Local Authority" in label:
            self_signed += 1
            label = "Caddy Local Authority (untrusted)"
        elif "Let's Encrypt" in label or "letsencrypt" in label.lower():
            label = "Let's Encrypt"
        issuers[label] = issuers.get(label, 0) + 1

    return {
        "path": str(cert_root),
        "exists": True,
        "total": total,
        "dedicated": total - wildcard,
        "wildcard": wildcard,
        "self_signed": self_signed,
        "issuers": issuers,
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def _config_validity(config_path: Path) -> dict:
    """Run ``caddy validate`` so an invalid config is caught before a reload."""
    if not shutil.which("caddy") or not config_path.is_file():
        return {"checked": False, "valid": None, "detail": ""}
    code, out = _run(["caddy", "validate", "--config", str(config_path)], timeout=20.0)
    detail = (out or "").strip().splitlines()
    return {
        "checked": True,
        "valid": code == 0,
        "detail": detail[-1] if detail else ("valid" if code == 0 else "validation failed"),
    }


async def caddy_monitor() -> dict:
    """Full Caddy runtime monitor for the SSL dashboard."""
    from syte.certificates import CADDY_DROPIN_FILE, CADDY_ENV_PATH, caddy_has_cloudflare_plugin
    from syte.config import settings

    binary = shutil.which("caddy")
    version: str | None = None
    if binary:
        code, out = await asyncio.to_thread(_run, ["caddy", "version"])
        if code == 0:
            version = (out or "").strip().splitlines()[0] if out.strip() else None

    config_path = settings.caddy_config_path
    fallback = settings.data_dir / "Caddyfile"
    active_config = config_path if config_path.is_file() else fallback

    unit, listeners, admin, config, certs, plugin, validity = await asyncio.gather(
        asyncio.to_thread(systemd_unit_state),
        asyncio.to_thread(port_listeners),
        admin_api_status(),
        asyncio.to_thread(caddyfile_summary, active_config),
        asyncio.to_thread(certificate_store_summary),
        asyncio.to_thread(caddy_has_cloudflare_plugin),
        asyncio.to_thread(_config_validity, active_config),
    )

    running = bool(unit["active"]) or bool(admin.get("reachable"))
    https_serving = any(l["port"] == 443 and l["listening"] for l in listeners)

    problems: list[str] = []
    if not binary:
        problems.append("Caddy is not installed — no HTTPS is possible.")
    elif not running:
        problems.append("Caddy is installed but not running.")
    if running and not https_serving:
        problems.append("Caddy is running but nothing is listening on :443.")
    if not config["exists"]:
        problems.append(f"No Caddy configuration found at {config['path']}.")
    if validity.get("valid") is False:
        problems.append(f"Caddy configuration is invalid: {validity.get('detail')}")
    if config["exists"] and admin.get("reachable"):
        # A config on disk with more hosts than the running config means the
        # last reload never took effect.
        if config["host_block_count"] > admin["loaded_host_count"] > 0:
            problems.append(
                f"Caddyfile defines {config['host_block_count']} hosts but Caddy is "
                f"running {admin['loaded_host_count']} — reload the configuration."
            )
    if config.get("dns_challenge_blocks") and not plugin:
        problems.append(
            "Configuration uses the Cloudflare DNS challenge but the "
            "dns.providers.cloudflare plugin is not installed."
        )
    if config.get("dns_challenge_blocks") and not CADDY_DROPIN_FILE.is_file():
        problems.append(
            "DNS-01 is configured but the Caddy systemd EnvironmentFile drop-in is missing, "
            "so CLOUDFLARE_API_TOKEN is empty at runtime."
        )
    if certs.get("self_signed"):
        problems.append(
            f"{certs['self_signed']} stored certificate(s) were issued by Caddy's internal "
            "authority and are rejected by browsers."
        )

    if not binary or not running or not https_serving:
        health = "down"
    elif problems:
        health = "degraded"
    else:
        health = "healthy"

    return {
        "installed": bool(binary),
        "binary_path": binary or "",
        "version": version,
        "running": running,
        "https_serving": https_serving,
        "health": health,
        "problems": problems,
        "systemd": unit,
        "listeners": listeners,
        "admin_api": admin,
        "config": {**config, "validation": validity},
        "certificates": certs,
        "cloudflare": {
            "plugin_installed": plugin,
            "systemd_env_configured": CADDY_DROPIN_FILE.is_file(),
            "systemd_dropin_path": str(CADDY_DROPIN_FILE),
            "env_file_written": CADDY_ENV_PATH.is_file(),
            "env_file_path": str(CADDY_ENV_PATH),
        },
        # Kept flat for backwards compatibility with the previous payload.
        "active": running,
        "enabled": unit["enabled"],
        "uptime_seconds": unit["uptime_seconds"],
        "config_path": config["path"],
        "config_exists": config["exists"],
        "systemd_env_configured": CADDY_DROPIN_FILE.is_file(),
        "cloudflare_plugin_installed": plugin,
    }


def reload_caddy() -> tuple[bool, str]:
    """Reload Caddy in place so a freshly written config takes effect."""
    from syte.certificates import _caddy_env
    from syte.config import settings

    config_path = settings.caddy_config_path
    if not config_path.is_file():
        config_path = settings.data_dir / "Caddyfile"
    if not config_path.is_file():
        return False, "No Caddy configuration file to reload."

    if shutil.which("systemctl"):
        code, out = _run(["systemctl", "reload", "caddy"], timeout=30.0)
        if code == 0:
            return True, "Caddy reloaded via systemd."
    if shutil.which("caddy"):
        try:
            result = subprocess.run(
                ["caddy", "reload", "--config", str(config_path)],
                capture_output=True, text=True, timeout=30.0, env=_caddy_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, f"caddy reload failed: {error}"
        if result.returncode == 0:
            return True, f"Caddy reloaded with {config_path}."
        return False, (result.stderr or result.stdout or "caddy reload failed").strip()
    return False, "Caddy binary not found."
