"""Preview URL access helpers for the debug-chat agent (fetch, logs, screenshot)."""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from syte.agent_skills import read_access_config
from syte.database import get_project
from syte.preview_manager import get_preview_logs, get_preview_status, preview_meta


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_allowed_url(url: str, preview_url: str, custom_urls: list[str]) -> bool:
    if not url:
        return False
    allowed = {preview_url}
    allowed.update(u for u in custom_urls if u)
    if url in allowed:
        return True
    try:
        p = urlparse(url)
        prev = urlparse(preview_url) if preview_url else None
        if prev and p.netloc == prev.netloc:
            return True
    except Exception:
        return False
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
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
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
    results: dict[str, dict[str, Any]] = {}
    for name in viewports:
        size = VIEWPORTS.get(name) or VIEWPORTS["desktop"]
        results[name] = await _capture_screenshot(url, width=size[0], height=size[1], viewport=name)
    return results


async def _capture_screenshot(
    url: str,
    *,
    width: int = 1280,
    height: int = 800,
    viewport: str = "desktop",
) -> dict[str, Any]:
    browser = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
    )
    if not browser:
        return {
            "ok": False,
            "error": "no_browser",
            "message": "No headless browser found. Use syte-access fetch to read HTML instead.",
            "viewport": viewport,
            "width": width,
            "height": height,
        }

    def _run() -> dict[str, Any]:
        proc = subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                f"--window-size={int(width)},{int(height)}",
                "--screenshot=-",
                url,
            ],
            capture_output=True,
            timeout=45,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")[:500]
            return {
                "ok": False,
                "error": "screenshot_failed",
                "message": err or "Screenshot command failed",
                "viewport": viewport,
                "width": width,
                "height": height,
            }
        data = proc.stdout
        if not data:
            return {
                "ok": False,
                "error": "screenshot_empty",
                "message": "Screenshot produced no data",
                "viewport": viewport,
                "width": width,
                "height": height,
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
        }

    try:
        return await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "screenshot_timeout",
            "message": "Screenshot timed out",
            "viewport": viewport,
            "width": width,
            "height": height,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "screenshot_failed",
            "message": str(exc),
            "viewport": viewport,
            "width": width,
            "height": height,
        }