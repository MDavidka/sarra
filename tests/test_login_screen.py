"""Regression coverage for the responsive Sycord account gate."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_login_screen_loads_the_responsive_auth_stylesheet():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/auth.css").read_text(encoding="utf-8")

    assert '/static/auth.css?v=__VERSION__' in index
    assert '.account-auth-layout' in css
    assert '.account-auth-aside' in css
    assert '.account-auth-main' in css
    assert '@media (max-width: 700px)' in css
    assert '.account-auth-aside { display: none;' in css


def test_login_renderer_retains_setup_and_login_controls():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")

    assert 'function legacyAccountLoginMarkup(setup)' in app
    assert 'legacy-account-setup-switch' in app
    assert 'legacy-account-login-switch' in app
    assert "'/auth/setup'" in app
    assert "'/auth/login'" in app
    assert 'account-auth-layout' in app
