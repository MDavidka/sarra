import os
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_crash_screen_html_structure():
    index_path = REPO_ROOT / "syte" / "static" / "index.html"
    assert index_path.exists(), "index.html must exist"
    content = index_path.read_text(encoding="utf-8")

    assert 'id="crash-screen"' in content
    assert 'class="crash-screen-overlay hidden"' in content
    assert 'id="crash-title"' in content
    assert 'id="crash-subtitle"' in content
    assert 'id="crash-message"' in content
    assert 'id="crash-details"' in content
    assert 'id="crash-reload-btn"' in content
    assert 'id="crash-retry-btn"' in content
    assert 'id="crash-dismiss-btn"' in content


def test_crash_screen_css_styles():
    css_path = REPO_ROOT / "syte" / "static" / "style.css"
    assert css_path.exists(), "style.css must exist"
    content = css_path.read_text(encoding="utf-8")

    assert ".crash-screen-overlay" in content
    assert ".crash-card" in content
    assert ".crash-header" in content
    assert ".crash-actions" in content


def test_crash_screen_app_js_logic():
    js_path = REPO_ROOT / "syte" / "static" / "app.js"
    assert js_path.exists(), "app.js must exist"
    content = js_path.read_text(encoding="utf-8")

    assert "function showCrashScreen" in content
    assert "function hideCrashScreen" in content
    assert "setupCrashScreenHandlers" in content
    assert "highLoadNetworkErrorCount" in content
    assert "window.addEventListener('unhandledrejection'" in content
    assert "window.addEventListener('error'" in content
    assert "Server may be down or under high load." in content
