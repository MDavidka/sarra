"""Idempotent AlmaLinux host preparation for the Syra public endpoint."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from syte.config import settings
from syte.database import get_setting
from syte.domain_utils import normalize_domain
from syte.litellm_config import LITELLM_PUBLIC_HOST

DOCKER_REPO = "https://download.docker.com/linux/centos/docker-ce.repo"
CADDY_REPO_SETUP = "https://dl.cloudsmith.io/public/caddy/stable/setup.rpm.sh"


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _systemd_unit_loaded(unit: str) -> bool:
    """Return whether systemd knows a usable unit, not merely a CLI binary."""
    if not _command_exists("systemctl"):
        return False
    code, output = _run(
        ["systemctl", "show", unit, "--property=LoadState", "--value"],
        timeout=20.0,
    )
    return code == 0 and output.strip() == "loaded"


def _docker_unit_loaded() -> bool:
    """Check for the Docker engine unit separately from the Docker CLI."""
    return _systemd_unit_loaded("docker.service")


def _run(command: list[str], timeout: float = 120.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, f"Command not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out: {' '.join(command)}"
    except OSError as error:
        return 1, f"Could not run {' '.join(command)}: {error}"
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode, output


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def _is_almalinux(values: dict[str, str]) -> bool:
    return values.get("ID", "").lower() == "almalinux"


def _run_checked(command: list[str], label: str, *, timeout: float = 600.0) -> tuple[bool, str]:
    code, output = _run(command, timeout=timeout)
    if code == 0:
        return True, output
    return False, f"{label} failed (exit {code}): {output or 'no output'}"


def _ensure_almalinux_packages() -> tuple[bool, list[str]]:
    """Install Docker CE, Caddy, and firewalld using idempotent dnf checks."""
    if not _command_exists("dnf"):
        return False, ["AlmaLinux host setup requires dnf."]

    messages: list[str] = []
    if not _command_exists("curl"):
        ok, detail = _run_checked(["dnf", "-y", "install", "curl"], "curl")
        if not ok:
            return False, [detail]
        messages.append("curl installed.")

    docker_ready = _command_exists("docker") and _docker_unit_loaded()
    if not docker_ready:
        if _command_exists("docker"):
            messages.append("Docker CLI found but docker.service is missing; installing Docker CE engine.")
        commands: list[tuple[list[str], str]] = [
            (["dnf", "-y", "install", "dnf-plugins-core"], "Docker prerequisites"),
        ]
        if not Path("/etc/yum.repos.d/docker-ce.repo").exists():
            commands.append(
                (["dnf", "config-manager", "--add-repo", DOCKER_REPO], "Docker repository")
            )
        commands.append(
            (
                [
                    "dnf", "-y", "install",
                    "docker-ce", "docker-ce-cli", "containerd.io",
                    "docker-buildx-plugin", "docker-compose-plugin",
                ],
                "Docker CE",
            )
        )
        for command, label in commands:
            ok, detail = _run_checked(command, label)
            if not ok:
                return False, [detail]
            messages.append(f"{label} ready.")
    else:
        messages.append("Docker already installed.")

    if not _command_exists("caddy"):
        ok, detail = _run_checked(
            ["bash", "-lc", f"curl -1sLf '{CADDY_REPO_SETUP}' | bash"],
            "Caddy repository",
        )
        if not ok:
            return False, [detail]
        ok, detail = _run_checked(["dnf", "-y", "install", "caddy"], "Caddy")
        if not ok:
            return False, [detail]
        messages.append("Caddy installed.")
    else:
        messages.append("Caddy already installed.")

    if not _command_exists("firewall-cmd"):
        ok, detail = _run_checked(["dnf", "-y", "install", "firewalld"], "firewalld")
        if not ok:
            return False, [detail]
        messages.append("firewalld installed.")

    return True, messages


def _ensure_services_and_firewall() -> tuple[bool, list[str]]:
    messages: list[str] = []
    if not _command_exists("systemctl"):
        return False, ["systemctl is required to manage Docker, Caddy, and firewalld."]

    for service in ("docker", "firewalld"):
        ok, detail = _run_checked(
            ["systemctl", "enable", "--now", service],
            f"Enable {service}",
            timeout=120.0,
        )
        if not ok:
            return False, [detail]
        messages.append(f"{service} is enabled and running.")

    for service in ("http", "https"):
        ok, detail = _run_checked(
            ["firewall-cmd", "--permanent", "--add-service", service],
            f"Open firewall service {service}",
            timeout=60.0,
        )
        if not ok and "ALREADY_ENABLED" not in detail:
            return False, [detail]
    ok, detail = _run_checked(["firewall-cmd", "--reload"], "Reload firewalld", timeout=60.0)
    if not ok:
        return False, [detail]
    messages.append("Firewall permanent rules include HTTP/HTTPS; existing firewall rules are preserved.")
    return True, messages


async def _wait_for_docker() -> tuple[bool, str]:
    for _ in range(15):
        ok, detail = await asyncio.to_thread(_run_checked, ["docker", "info"], "Docker readiness", timeout=20.0)
        if ok:
            return True, "Docker daemon is ready."
        await asyncio.sleep(1.0)
    return False, detail


async def _resolve_public_ipv4() -> str:
    """Return a routable IPv4 address before changing public DNS."""
    configured = settings.public_ip.strip()
    if configured:
        candidate = configured
    else:
        candidate = ""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("https://api.ipify.org", params={"format": "text"})
                if response.status_code == 200:
                    candidate = response.text.strip()
        except Exception:  # noqa: BLE001 - DNS setup returns a readable diagnostic
            candidate = ""
        if not candidate:
            candidate = settings.resolved_public_ip

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return ""
    if address.version != 4 or not address.is_global:
        return ""
    return str(address)


async def _ensure_dns_record() -> tuple[bool, str]:
    """Ensure api.sycord.site points at this host, using the saved Cloudflare token."""
    public_ip = await _resolve_public_ipv4()
    token = (await get_setting("cloudflare_api_token", "")).strip()
    if not public_ip:
        return False, (
            "Could not determine a routable public IPv4 address. "
            "Set the server public_ip setting or SYTE_PUBLIC_IP before DNS setup."
        )

    if not token:
        try:
            resolved = socket.gethostbyname(LITELLM_PUBLIC_HOST)
        except OSError:
            resolved = ""
        if resolved == public_ip:
            return True, f"DNS already resolves {LITELLM_PUBLIC_HOST} to {public_ip}."
        return False, (
            f"{LITELLM_PUBLIC_HOST} does not resolve to {public_ip}. "
            "Save a Cloudflare DNS-edit token or create the A record manually."
        )

    import httpx

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        zones_response = await client.get(
            "https://api.cloudflare.com/client/v4/zones",
            params={"name": "sycord.site", "status": "active"},
            headers=headers,
        )
        zones_payload = zones_response.json()
        zones = zones_payload.get("result", []) if isinstance(zones_payload, dict) else []
        if zones_response.status_code != 200 or not zones:
            return False, "Cloudflare token could not find the active sycord.site zone."
        zone_id = zones[0].get("id")
        if not zone_id:
            return False, "Cloudflare returned no zone id for sycord.site."

        records_response = await client.get(
            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
            params={"type": "A", "name": LITELLM_PUBLIC_HOST},
            headers=headers,
        )
        records_payload = records_response.json()
        records = records_payload.get("result", []) if isinstance(records_payload, dict) else []
        if records_response.status_code != 200:
            return False, "Cloudflare DNS records could not be read; check token permissions."

        record_body = {
            "type": "A",
            "name": LITELLM_PUBLIC_HOST,
            "content": public_ip,
            "ttl": 120,
            "proxied": False,
        }
        if records:
            record = records[0]
            if record.get("content") == public_ip and not record.get("proxied", False):
                return True, f"Cloudflare DNS already points {LITELLM_PUBLIC_HOST} to {public_ip}."
            response = await client.put(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record['id']}",
                headers=headers,
                json=record_body,
            )
            action = "updated"
        else:
            response = await client.post(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                headers=headers,
                json=record_body,
            )
            action = "created"
        payload = response.json()
        if response.status_code not in (200, 201) or not payload.get("success"):
            return False, "Cloudflare could not update the api.sycord.site DNS record."
        return True, f"Cloudflare DNS record {action}: {LITELLM_PUBLIC_HOST} → {public_ip}."


async def prepare_syra_host() -> dict[str, Any]:
    """Prepare an AlmaLinux host for the Syra GUI/API and LiteLLM container."""
    if os.geteuid() != 0:
        return {
            "ok": False,
            "message": "Host setup must run as root through the Syte systemd service.",
            "steps": [],
        }

    release = _os_release()
    if not _is_almalinux(release):
        return {
            "ok": False,
            "message": f"Automatic host setup supports AlmaLinux only (detected {release.get('PRETTY_NAME', 'unknown')}).",
            "steps": [],
        }

    steps: list[str] = []
    ok, package_messages = await asyncio.to_thread(_ensure_almalinux_packages)
    steps.extend(package_messages)
    if not ok:
        return {"ok": False, "message": package_messages[-1], "steps": steps}

    ok, service_messages = await asyncio.to_thread(_ensure_services_and_firewall)
    steps.extend(service_messages)
    if not ok:
        return {"ok": False, "message": service_messages[-1], "steps": steps}

    ok, docker_message = await _wait_for_docker()
    steps.append(docker_message)
    if not ok:
        return {"ok": False, "message": docker_message, "steps": steps}

    try:
        dns_ok, dns_message = await _ensure_dns_record()
    except Exception as error:  # noqa: BLE001 - returned as setup diagnostics
        dns_ok = False
        dns_message = f"DNS setup failed: {type(error).__name__}: {error}"
    steps.append(dns_message)
    if not dns_ok:
        return {"ok": False, "message": dns_message, "steps": steps}

    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    if gui_domain:
        steps.append(
            f"Preserved existing GUI hostname https://{gui_domain}; "
            f"combined endpoint is also available at https://{LITELLM_PUBLIC_HOST}/."
        )
    else:
        steps.append(
            f"Syte GUI available at the direct URL; https://{LITELLM_PUBLIC_HOST}/ serves LiteLLM and previews."
        )
    return {
        "ok": True,
        "message": "AlmaLinux host is prepared for Syte, Caddy, Docker, and LiteLLM.",
        "steps": steps,
    }
