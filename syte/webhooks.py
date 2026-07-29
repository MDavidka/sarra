"""Outbound webhooks for deployments and agent session completions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from syte.database import get_setting

logger = logging.getLogger(__name__)

EVENT_SITE_DEPLOYED = "site.deployed"
EVENT_PAGE_UPDATED = "page.updated"
EVENT_AGENT_SESSION_COMPLETED = "agent.session.completed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def webhook_urls() -> list[str]:
    raw = (await get_setting("webhook_urls", "")).strip()
    if not raw:
        return []
    urls: list[str] = []
    # Support JSON array or newline/comma separated URLs.
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                urls = [str(u).strip() for u in parsed if str(u).strip()]
        except json.JSONDecodeError:
            pass
    if not urls:
        for part in re_split_urls(raw):
            urls.append(part)
    return urls[:10]


def re_split_urls(raw: str) -> list[str]:
    parts: list[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        chunk = chunk.strip()
        if chunk.startswith("http://") or chunk.startswith("https://"):
            parts.append(chunk)
    return parts


async def emit_webhook(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``{event, created_at, ...payload}`` to each configured webhook URL."""
    urls = await webhook_urls()
    if not urls:
        return {"ok": True, "delivered": 0, "skipped": True}
    body = {"event": event, "created_at": _now(), **payload}
    delivered = 0
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=8.0) as client:
        for url in urls:
            try:
                resp = await client.post(url, json=body)
                if 200 <= resp.status_code < 300:
                    delivered += 1
                else:
                    errors.append(f"{url} → HTTP {resp.status_code}")
            except Exception as exc:
                logger.warning("Webhook delivery failed for %s: %s", url, exc)
                errors.append(f"{url} → {exc}")
    return {"ok": not errors, "delivered": delivered, "errors": errors, "event": event}
