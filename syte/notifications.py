"""Notification delivery for Sycord projects and the installed PWA.

The notification centre is intentionally durable: project actions are saved for
in-app review first, while email, webhooks, and browser push are independent
best-effort delivery channels. Secret configuration is never returned to clients.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from syte.database import (
    create_notification_event,
    delete_pwa_push_subscription,
    get_project,
    get_setting,
    list_pwa_push_subscriptions,
    set_setting,
)

logger = logging.getLogger(__name__)

_EVENT_TITLE = {
    "project.created": "Project created",
    "project.imported": "Source imported",
    "project.updated": "Project updated",
    "project.deleted": "Project removed",
    "project.started": "Project started",
    "project.stopped": "Project stopped",
    "project.repository_updated": "Repository updated",
    "project.domain_updated": "Domain updated",
    "project.preview_started": "Preview started",
    "project.preview_stopped": "Preview stopped",
    "project.health_failed": "Health check failed",
    "project.deployment_config_updated": "Deployment configuration updated",
    "deployment.queued": "Deployment queued",
    "deployment.succeeded": "Deployment succeeded",
    "deployment.failed": "Deployment failed",
    "deployment.cancelled": "Deployment cancelled",
    "notification.test": "Test notification",
}


async def _setting_bool(key: str) -> bool:
    return (await get_setting(key, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _split_values(raw: str, *, prefix: str | None = None) -> list[str]:
    values: list[str] = []
    for value in raw.replace(",", "\n").splitlines():
        cleaned = value.strip()
        if not cleaned or "\r" in cleaned or "\n" in cleaned:
            continue
        if prefix and not cleaned.startswith(prefix):
            continue
        values.append(cleaned)
    return values[:10]


async def notification_settings_payload() -> dict[str, Any]:
    """Return configuration state without exposing saved SMTP or VAPID secrets."""
    return {
        "email": {
            "enabled": await _setting_bool("notification_email_enabled"),
            "recipients": await get_setting("notification_email_recipients", ""),
            "smtp_host": await get_setting("notification_smtp_host", ""),
            "smtp_port": int(await get_setting("notification_smtp_port", "587") or 587),
            "smtp_username": await get_setting("notification_smtp_username", ""),
            "sender": await get_setting("notification_email_sender", ""),
            "use_tls": await _setting_bool("notification_smtp_use_tls"),
            "password_set": bool(await get_setting("notification_smtp_password", "")),
        },
        "webhook": {
            "enabled": await _setting_bool("notification_webhook_enabled"),
            "urls": await get_setting("notification_webhook_urls", ""),
        },
        "pwa": {
            "push_enabled": bool(await get_setting("notification_vapid_public_key", "")),
            "requires_installed_app_on_ios": True,
        },
    }


async def save_notification_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Persist notification settings, retaining sensitive fields when omitted."""
    email = values.get("email") or {}
    webhook = values.get("webhook") or {}
    smtp_host = str(email.get("smtp_host") or "").strip()
    sender = str(email.get("sender") or "").strip()
    recipients = str(email.get("recipients") or "").strip()
    smtp_port = int(email.get("smtp_port") or 587)
    urls = str(webhook.get("urls") or "").strip()
    if smtp_port < 1 or smtp_port > 65535:
        raise ValueError("SMTP port must be between 1 and 65535.")
    if bool(email.get("enabled")) and (not smtp_host or not sender or not _split_values(recipients)):
        raise ValueError("Email notifications require an SMTP host, sender, and at least one recipient.")
    if bool(webhook.get("enabled")) and not _split_values(urls, prefix="http"):
        raise ValueError("Webhook notifications require at least one valid http(s) URL.")
    if any(not item.startswith(("http://", "https://")) for item in _split_values(urls)):
        raise ValueError("Webhook URLs must use http:// or https://.")

    writes = {
        "notification_email_enabled": "1" if bool(email.get("enabled")) else "0",
        "notification_email_recipients": recipients,
        "notification_smtp_host": smtp_host,
        "notification_smtp_port": str(smtp_port),
        "notification_smtp_username": str(email.get("smtp_username") or "").strip(),
        "notification_email_sender": sender,
        "notification_smtp_use_tls": "1" if bool(email.get("use_tls")) else "0",
        "notification_webhook_enabled": "1" if bool(webhook.get("enabled")) else "0",
        "notification_webhook_urls": urls,
    }
    for key, value in writes.items():
        await set_setting(key, value)
    password = email.get("smtp_password")
    if password is not None and str(password):
        await set_setting("notification_smtp_password", str(password))
    return await notification_settings_payload()


async def vapid_public_key() -> str:
    existing = await get_setting("notification_vapid_public_key", "")
    private = await get_setting("notification_vapid_private_key", "")
    if existing and private:
        return existing
    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    public_point = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public = base64.urlsafe_b64encode(public_point).rstrip(b"=").decode("ascii")
    await set_setting("notification_vapid_public_key", public)
    await set_setting("notification_vapid_private_key", private_pem)
    return public


async def _email_delivery(record: dict[str, Any]) -> None:
    if not await _setting_bool("notification_email_enabled"):
        return
    host = await get_setting("notification_smtp_host", "")
    sender = await get_setting("notification_email_sender", "")
    recipients = _split_values(await get_setting("notification_email_recipients", ""))
    if not host or not sender or not recipients:
        logger.warning("Email notifications are enabled but SMTP configuration is incomplete")
        return
    port = int(await get_setting("notification_smtp_port", "587") or 587)
    username = await get_setting("notification_smtp_username", "")
    password = await get_setting("notification_smtp_password", "")
    use_tls = await _setting_bool("notification_smtp_use_tls")
    message = EmailMessage()
    message["Subject"] = f"Sycord: {record['title']}"
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(
        f"{record['message']}\n\nEvent: {record['event']}\nProject: {record.get('project_id') or 'Workspace'}\n"
    )

    def send() -> None:
        with smtplib.SMTP(host, port, timeout=10) as client:
            client.ehlo()
            if use_tls:
                client.starttls()
                client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(message)

    await asyncio.to_thread(send)


async def _webhook_delivery(record: dict[str, Any]) -> None:
    if not await _setting_bool("notification_webhook_enabled"):
        return
    urls = _split_values(await get_setting("notification_webhook_urls", ""))
    urls = [url for url in urls if url.startswith(("http://", "https://"))]
    if not urls:
        return
    body = {
        "event": record["event"],
        "title": record["title"],
        "message": record["message"],
        "project_id": record.get("project_id"),
        "created_at": record["created_at"],
        "payload": record.get("payload") or {},
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        for url in urls:
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
            except Exception as exc:
                logger.warning("Notification webhook delivery failed for %s: %s", url, exc)


async def _push_delivery(record: dict[str, Any]) -> None:
    subscriptions = await list_pwa_push_subscriptions()
    if not subscriptions:
        return
    private_key = await get_setting("notification_vapid_private_key", "")
    if not private_key:
        return
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("PWA push delivery is unavailable because pywebpush is not installed")
        return
    body = json.dumps(
        {
            "title": record["title"],
            "body": record["message"],
            "event": record["event"],
            "project_id": record.get("project_id"),
            "url": "/",
        }
    )
    claims = {"sub": "mailto:support@sycord.com"}
    for subscription in subscriptions:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=subscription,
                data=body,
                vapid_private_key=private_key,
                vapid_claims=claims,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {404, 410}:
                await delete_pwa_push_subscription(str(subscription.get("endpoint") or ""))
            else:
                logger.warning("PWA push delivery failed: %s", exc)
        except Exception as exc:
            logger.warning("PWA push delivery failed: %s", exc)


async def _deliver(record: dict[str, Any], *, push: bool) -> None:
    jobs = [_email_delivery(record), _webhook_delivery(record)]
    if push:
        jobs.append(_push_delivery(record))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Notification delivery error: %s", result)


async def publish_project_event(
    event: str,
    *,
    project_id: str | None,
    message: str,
    payload: dict[str, Any] | None = None,
    force_in_app: bool = False,
) -> dict[str, Any] | None:
    """Publish a completed project action to all configured delivery channels.

    In-app history and PWA push honour the project opt-in, whereas workspace email
    and webhook channels deliver every supported action once configured.
    """
    project = await get_project(project_id) if project_id else None
    in_app = force_in_app or bool(project and project.get("in_app_notifications"))
    record: dict[str, Any] | None = None
    title = _EVENT_TITLE.get(event, event.replace(".", " ").title())
    if in_app:
        record = await create_notification_event(
            event=event,
            title=title,
            message=message,
            project_id=project_id,
            payload=payload,
        )
    else:
        record = {
            "event": event,
            "title": title,
            "message": message,
            "project_id": project_id,
            "payload": payload or {},
            "created_at": datetime.now(UTC).isoformat(),
        }
    asyncio.create_task(_deliver(record, push=in_app))
    return record if in_app else None
