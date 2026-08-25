"""Regression coverage for the retained Sycord API, Git, support, and PWA notification workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from syte.config import settings


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


@pytest.mark.asyncio
async def test_project_pwa_notification_preference_and_history_are_persisted(tmp_data_dir: Path) -> None:
    from syte.database import (
        create_notification_event,
        create_project,
        init_db,
        list_notification_events,
        mark_notification_events_read,
    )

    await init_db()
    project = await create_project(
        {
            "id": "pwa-demo",
            "name": "PWA demo",
            "port": 8787,
            "in_app_notifications": True,
        }
    )
    assert project["in_app_notifications"] == 1

    event = await create_notification_event(
        event="deployment.succeeded",
        title="Deployment succeeded",
        message="PWA demo is running.",
        project_id=project["id"],
        payload={"trigger": "quick-deploy"},
    )
    events = await list_notification_events()
    assert events[0]["id"] == event["id"]
    assert events[0]["payload"] == {"trigger": "quick-deploy"}
    assert events[0]["is_read"] is False
    assert await mark_notification_events_read([event["id"]]) == 1
    assert (await list_notification_events())[0]["is_read"] is True


@pytest.mark.asyncio
async def test_notification_settings_hide_saved_smtp_password(tmp_data_dir: Path) -> None:
    from syte.database import init_db
    from syte.notifications import notification_settings_payload, save_notification_settings

    await init_db()
    saved = await save_notification_settings(
        {
            "email": {
                "enabled": True,
                "recipients": "ops@sycord.com",
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_username": "ops",
                "smtp_password": "secret-password",
                "sender": "alerts@sycord.com",
                "use_tls": True,
            },
            "webhook": {"enabled": True, "urls": "https://hooks.example.com/sycord"},
        }
    )
    assert saved["email"]["password_set"] is True
    assert "smtp_password" not in saved["email"]
    loaded = await notification_settings_payload()
    assert loaded["email"]["password_set"] is True
    assert loaded["webhook"]["urls"] == "https://hooks.example.com/sycord"


def test_navigation_routes_to_requested_functional_destinations():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")

    assert 'data-view="users"><i data-lucide="braces"></i><span>API</span>' in index
    assert 'href="/api/"' in index
    assert 'href="mailto:support@sycord.com"' in index
    assert "users: 'API'" in app
    assert "Quick Deploy" in app
    assert "/projects/git/github/disconnect" in app


def test_pwa_assets_and_notification_opt_in_are_present():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    worker = (ROOT / "syte/static/service-worker.js").read_text(encoding="utf-8")
    manifest = (ROOT / "syte/static/manifest.webmanifest").read_text(encoding="utf-8")
    main = (ROOT / "syte/main.py").read_text(encoding="utf-8")

    assert 'rel="manifest" href="/manifest.webmanifest"' in index
    assert 'id="create-in-app-notifications"' in index
    assert "navigator.serviceWorker.register('/service-worker.js'" in app
    assert "/notifications/push-subscriptions" in app
    assert "self.addEventListener('push'" in worker
    assert '"display": "standalone"' in manifest
    assert '"/api/notifications/settings"' in main
    assert '"/api/notifications/push-subscriptions"' in main
