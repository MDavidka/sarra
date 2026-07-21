"""Preview URL access helpers for the debug-chat agent (fetch, logs, screenshot)."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from syte.agent_skills import read_access_config
from syte.database import get_project
from syte.preview_manager import get_preview_logs, get_preview_status, preview_meta

logger = logging.getLogger(__name__)

# Common Chromium/Chrome locations on Ubuntu/Debian Syte hosts (beyond PATH).
_BROWSER_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chromium-headless-shell",
    "chrome",
)
_BROWSER_PATH_CANDIDATES = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/local/bin/google-chrome",
    "/usr/local/bin/chromium",
    "/snap/bin/chromium",
    "/usr/lib/chromium/chromium",
    "/usr/lib/chromium-browser/chromium-browser",
    "/opt/google/chrome/chrome",
)

_resolved_browser: str | None | bool = False  # False = unset, None = missing, str = path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_headless_browser(*, force_refresh: bool = False) -> str | None:
    """Locate a Chromium/Chrome binary for headless screenshots.

    Order: ``SYTE_CHROMIUM_PATH`` env → PATH names → well-known absolute paths.
    Result is cached for the process lifetime.
    """
    global _resolved_browser
    if not force_refresh and _resolved_browser is not False:
        return _resolved_browser if isinstance(_resolved_browser, str) else None

    env_path = (os.environ.get("SYTE_CHROMIUM_PATH") or "").strip()
    if env_path and Path(env_path).is_file() and os.access(env_path, os.X_OK):
        _resolved_browser = env_path
        return env_path

    for name in _BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found and Path(found).is_file():
            _resolved_browser = found
            return found

    for path in _BROWSER_PATH_CANDIDATES:
        if Path(path).is_file() and os.access(path, os.X_OK):
            _resolved_browser = path
            return path

    _resolved_browser = None
    return None


def browser_install_hint() -> str:
    return (
        "No headless browser found for screenshots. On the Syte host install Chromium, e.g. "
        "`sudo apt-get install -y chromium-browser` or `sudo apt-get install -y chromium`, "
        "or set SYTE_CHROMIUM_PATH to the chrome/chromium binary, then restart Syte."
    )

def _is_allowed_url(url: str, preview_url: str, custom_urls: list[str]) -> bool:
    """Allow only preview / explicitly configured custom URLs (SSRF-hardened).

    Compares hostname (not raw netloc) and rejects userinfo tricks, non-http(s)
    schemes, and private/link-local/metadata destinations when resolvable.
    """
    import ipaddress
    import socket

    if not url:
        return False

    def _parse(u: str):
        p = urlparse((u or "").strip())
        if p.scheme not in ("http", "https"):
            return None
        if p.username or p.password:
            return None
        host = (p.hostname or "").lower().rstrip(".")
        if not host:
            return None
        return p, host

    try:
        parsed = _parse(url)
        if not parsed:
            return False
        p, host = parsed

        allowed_hosts: set[str] = set()
        for candidate in [preview_url, *(custom_urls or [])]:
            c = _parse(candidate)
            if c:
                allowed_hosts.add(c[1])

        # Exact URL match still allowed for configured custom URLs.
        exact = {u for u in [preview_url, *(custom_urls or [])] if u}
        if url in exact and host in allowed_hosts:
            pass  # still subject to private-IP check below
        elif host not in allowed_hosts:
            return False

        # Block literal private / link-local / metadata IPs.
        try:
            ip = ipaddress.ip_address(host)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                # Allow only if the preview itself is on that host (local preview).
                prev = _parse(preview_url)
                if not prev or prev[1] != host:
                    return False
        except ValueError:
            # Hostname — resolve and reject private destinations unless preview host.
            try:
                infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
            except OSError:
                # Explicitly allowlisted host that does not resolve here is still OK
                # (preview may be DNS-only on the public edge). Private-IP SSRF is
                # covered when resolution succeeds.
                return host in allowed_hosts
            prev = _parse(preview_url)
            preview_host = prev[1] if prev else ""
            for info in infos:
                addr = info[4][0]
                try:
                    ip = ipaddress.ip_address(addr)
                except ValueError:
                    continue
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ) and host != preview_host:
                    return False
        return True
    except Exception:
        return False


async def _preview_context(project_id: str) -> tuple[dict | None, dict[str, Any]]:
    project = await get_project(project_id)
    if not project:
        return None, {}
    meta, _ = await get_preview_status(project_id)
    access = await read_access_config(project_id)
    urls = preview_meta(project)
    if meta:
        urls.update(meta)
    return project, {"urls": urls, "access": access}


async def list_access_capabilities(project_id: str) -> dict[str, Any]:
    project, ctx = await _preview_context(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}
    urls = ctx["urls"]
    access = ctx["access"]
    return {
        "ok": True,
        "project_id": project_id,
        "preview_url": urls.get("preview_url"),
        "preview_running": urls.get("preview_running"),
        "preview_ready": urls.get("preview_ready"),
        "custom_urls": access.get("custom_urls") or [],
        "actions": [
            {"action": "status", "description": "Preview status and URLs"},
            {"action": "url", "description": "Return preview URL"},
            {"action": "fetch", "description": "Fetch HTML/text from preview or allowed URL"},
            {"action": "read", "description": "Alias for fetch"},
            {"action": "logs", "description": "Read preview dev-server log"},
            {"action": "screenshot", "description": "Capture preview screenshot (desktop + phone when chromium available)"},
        ],
        "viewports": {
            "desktop": {"width": 1280, "height": 800},
            "phone": {"width": 390, "height": 844},
        },
        "browser": find_headless_browser() or None,
        "browser_available": bool(find_headless_browser()),
        "browser_hint": None if find_headless_browser() else browser_install_hint(),
        "cli": "syte-access <action> [url|lines]",
    }


async def run_access_action(
    project_id: str,
    action: str,
    *,
    url: str | None = None,
    lines: int = 200,
) -> dict[str, Any]:
    project, ctx = await _preview_context(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    act = (action or "status").strip().lower()
    urls = ctx["urls"]
    access = ctx["access"]
    custom_urls = [str(u) for u in (access.get("custom_urls") or [])]
    preview_url = str(urls.get("preview_fetch_url") or urls.get("preview_url") or "")

    if act == "status":
        return {
            "ok": True,
            "action": "status",
            "preview_running": urls.get("preview_running"),
            "preview_ready": urls.get("preview_ready"),
            "preview_url": urls.get("preview_url"),
            "preview_domain_url": urls.get("preview_domain_url"),
            "preview_direct_url": urls.get("preview_direct_url"),
            "preview_port": urls.get("preview_port"),
            "custom_urls": custom_urls,
            "at": _now(),
        }

    if act == "url":
        return {
            "ok": bool(preview_url),
            "action": "url",
            "preview_url": preview_url or None,
            "message": preview_url or "Preview URL not available — start preview first",
        }

    if act in ("fetch", "read"):
        target = (url or "").strip() or preview_url
        if not target:
            return {"ok": False, "error": "no_url", "message": "No preview URL — start preview or pass url"}
        if not _is_allowed_url(target, preview_url, custom_urls):
            return {
                "ok": False,
                "error": "url_not_allowed",
                "message": "URL not allowed — use preview URL or add it in Debug Chat access settings",
            }
        try:
            # Do not follow redirects off the allowlist (SSRF via Location).
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                response = await client.get(target, headers={"User-Agent": "Syte-Agent-Access/1.0"})
                # Manually follow a small number of same-host redirects.
                for _ in range(3):
                    if response.status_code not in (301, 302, 303, 307, 308):
                        break
                    location = response.headers.get("location")
                    if not location:
                        break
                    next_url = str(httpx.URL(target).join(location))
                    if not _is_allowed_url(next_url, preview_url, custom_urls):
                        return {
                            "ok": False,
                            "error": "url_not_allowed",
                            "message": "Redirect target not allowed",
                            "url": next_url,
                        }
                    target = next_url
                    response = await client.get(target, headers={"User-Agent": "Syte-Agent-Access/1.0"})
            content_type = response.headers.get("content-type", "")
            text = response.text
            if len(text) > 120_000:
                text = text[:120_000] + "\n… [truncated]"
            return {
                "ok": response.status_code < 400,
                "action": act,
                "url": target,
                "status_code": response.status_code,
                "content_type": content_type,
                "content": text,
                "length": len(response.content),
            }
        except Exception as exc:
            return {"ok": False, "error": "fetch_failed", "message": str(exc), "url": target}

    if act == "logs":
        n = max(20, min(int(lines or 200), 2000))
        log_text = get_preview_logs(project_id, lines=n)
        return {
            "ok": True,
            "action": "logs",
            "lines": n,
            "logs": log_text,
            "preview_running": urls.get("preview_running"),
        }

    if act == "screenshot":
        target = (url or "").strip() or preview_url
        if not target:
            return {"ok": False, "error": "no_url", "message": "No preview URL for screenshot"}
        if not _is_allowed_url(target, preview_url, custom_urls):
            return {"ok": False, "error": "url_not_allowed", "message": "URL not allowed for screenshot"}
        # Legacy single-shot (desktop) plus dual-viewport helpers for agents.
        shots = await capture_preview_screenshots(target, viewports=("desktop", "phone"))
        public = {
            name: {k: v for k, v in (shot or {}).items() if k != "png_bytes"}
            for name, shot in shots.items()
        }
        desktop = public.get("desktop") or {}
        return {
            "ok": bool(desktop.get("ok")),
            "action": "screenshot",
            "url": target,
            **desktop,
            "viewports": public,
        }

    return {"ok": False, "error": "unknown_action", "message": f"Unknown action: {action}"}


VIEWPORTS: dict[str, tuple[int, int]] = {
    "desktop": (1280, 800),
    "phone": (390, 844),
    "thumb": (480, 300),
}


async def capture_preview_screenshots(
    url: str,
    *,
    viewports: tuple[str, ...] = ("desktop", "phone"),
) -> dict[str, dict[str, Any]]:
    """Capture one or more viewport screenshots of ``url``."""
    browser = find_headless_browser()
    if not browser:
        missing = {
            "ok": False,
            "error": "no_browser",
            "message": browser_install_hint(),
        }
        return {
            name: {
                **missing,
                "viewport": name,
                "width": (VIEWPORTS.get(name) or VIEWPORTS["desktop"])[0],
                "height": (VIEWPORTS.get(name) or VIEWPORTS["desktop"])[1],
            }
            for name in viewports
        }

    async def _one(name: str) -> tuple[str, dict[str, Any]]:
        size = VIEWPORTS.get(name) or VIEWPORTS["desktop"]
        shot = await _capture_screenshot(
            url, width=size[0], height=size[1], viewport=name, browser=browser,
        )
        return name, shot

    pairs = await asyncio.gather(*(_one(name) for name in viewports))
    return {name: shot for name, shot in pairs}

async def _capture_screenshot(
    url: str,
    *,
    width: int = 1280,
    height: int = 800,
    viewport: str = "desktop",
    browser: str | None = None,
) -> dict[str, Any]:
    browser = browser or find_headless_browser()
    if not browser:
        return {
            "ok": False,
            "error": "no_browser",
            "message": browser_install_hint(),
            "viewport": viewport,
            "width": width,
            "height": height,
        }

    def _run() -> dict[str, Any]:
        # Chromium writes --screenshot to a filesystem path; stdout "-" is not reliable.
        # Use a fresh --user-data-dir so concurrent/service chrome profile locks cannot abort us.
        with tempfile.TemporaryDirectory(prefix="syte-shot-", ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            out_path = tmp_path / f"{viewport}.png"
            profile_dir = tmp_path / "chrome-profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--user-data-dir={profile_dir}",
                f"--window-size={int(width)},{int(height)}",
                f"--screenshot={out_path}",
                url,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=45,
                    cwd=tmp,
                )
            except subprocess.TimeoutExpired as exc:
                # Prefer a partial screenshot if chrome wrote it before hanging.
                for candidate in (out_path, tmp_path / "screenshot.png"):
                    if candidate.is_file() and candidate.stat().st_size >= 32:
                        data = candidate.read_bytes()
                        if data.startswith(b"\x89PNG\r\n\x1a\n"):
                            return {
                                "ok": True,
                                "format": "png",
                                "viewport": viewport,
                                "width": width,
                                "height": height,
                                "image_base64": base64.b64encode(data).decode("ascii"),
                                "bytes": len(data),
                                "png_bytes": data,
                                "browser": browser,
                                "partial": True,
                            }
                return {
                    "ok": False,
                    "error": "screenshot_timeout",
                    "message": f"Screenshot timed out after {exc.timeout}s",
                    "viewport": viewport,
                    "width": width,
                    "height": height,
                    "browser": browser,
                }
            if not out_path.is_file() or out_path.stat().st_size < 32:
                err = (proc.stderr or b"").decode(errors="replace")[:800]
                # Some builds ignore --screenshot=path and write ./screenshot.png in cwd.
                fallback = tmp_path / "screenshot.png"
                if fallback.is_file() and fallback.stat().st_size >= 32:
                    data = fallback.read_bytes()
                else:
                    return {
                        "ok": False,
                        "error": "screenshot_failed" if proc.returncode != 0 else "screenshot_empty",
                        "message": err
                        or (
                            f"Screenshot produced no PNG (exit {proc.returncode}). "
                            "Confirm the preview URL is reachable from the Syte host."
                        ),
                        "viewport": viewport,
                        "width": width,
                        "height": height,
                        "browser": browser,
                    }
            else:
                data = out_path.read_bytes()
            # Basic PNG signature check.
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return {
                    "ok": False,
                    "error": "screenshot_invalid",
                    "message": "Browser wrote a non-PNG screenshot file",
                    "viewport": viewport,
                    "width": width,
                    "height": height,
                    "browser": browser,
                }
            return {
                "ok": True,
                "format": "png",
                "viewport": viewport,
                "width": width,
                "height": height,
                "image_base64": base64.b64encode(data).decode("ascii"),
                "bytes": len(data),
                "png_bytes": data,
                "browser": browser,
            }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("Screenshot capture failed for %s", url)
        return {
            "ok": False,
            "error": "screenshot_failed",
            "message": str(exc),
            "viewport": viewport,
            "width": width,
            "height": height,
            "browser": browser,
        }