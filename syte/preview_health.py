"""Preview readiness helpers for post-turn verification."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def wait_for_preview_ready(
    project_id: str,
    *,
    max_wait_s: int = 120,
    poll_s: float = 3.0,
) -> tuple[bool, str, dict[str, Any]]:
    """Poll preview until it responds with a non-5xx status.

    Returns ``(ok, preview_url, status_payload)``.
    """
    from syte.preview_access import run_access_action

    deadline = asyncio.get_event_loop().time() + max(5, int(max_wait_s))
    last_status: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        try:
            status = await run_access_action(project_id, "status")
        except Exception as exc:
            logger.debug("preview status poll failed: %s", exc)
            status = {"ok": False, "message": str(exc)}
        last_status = status if isinstance(status, dict) else {}
        preview_url = str(
            last_status.get("preview_url")
            or last_status.get("preview_direct_url")
            or last_status.get("preview_domain_url")
            or ""
        ).strip()
        running = bool(last_status.get("preview_running") or last_status.get("preview_ready"))
        if running and preview_url:
            try:
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                    resp = await client.get(
                        preview_url,
                        headers={"User-Agent": "Syte-Preview-Health/1.0"},
                    )
                    if resp.status_code < 500:
                        return True, preview_url, {**last_status, "http_status": resp.status_code}
            except Exception as exc:
                last_status["probe_error"] = str(exc)
        await asyncio.sleep(max(0.5, float(poll_s)))
    return False, "", last_status
