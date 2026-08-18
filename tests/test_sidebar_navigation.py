from pathlib import Path


def test_coolify_style_sidebar_contains_requested_sections() -> None:
    html = Path("syte/static/index.html").read_text()
    for label in (
        "Home", "Projects", "Overview", "Schedules", "Traefik File System", "Docker",
        "Settings", "Profile", "Sessions", "Remote Servers", "Users", "Audit Logs",
        "SSH Keys", "AI", "Tags", "Git", "Registry", "Secrets", "DNS Providers",
        "S3 Destinations", "Certificates", "Notifications", "Billing", "License",
        "SSO", "Documentation", "Support",
    ):
        assert label in html
    assert "coolify-org-head" in html
    assert "coolify-account-card" in html


def test_web_ui_has_no_bootstrap_key_overlay_or_old_unlock_copy() -> None:
    html = Path("syte/static/index.html").read_text()
    app = Path("syte/static/app.js").read_text()
    auth = Path("syte/auth.py").read_text()
    docs = Path("syte/static/api-docs.html").read_text()

    assert 'id="login-screen"' not in html
    assert 'id="login-bootstrap-key"' not in html
    assert "Unlock the Syra web UI first." not in auth
    assert "Unlock Syra to manage API keys" not in app
    assert "unlock Syra first" not in docs
    assert "bootstrap key" not in html
