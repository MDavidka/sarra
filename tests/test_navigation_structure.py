"""Regression coverage for the requested Sycord sidebar arrangement."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _main_navigation_html() -> str:
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    start = index.index('<div id="nav-block-home"')
    end = index.index('<div id="nav-block-service"', start)
    return index[start:end]


def test_main_navigation_has_exact_requested_subtab_groups():
    navigation = _main_navigation_html()
    group_labels = re.findall(r'<p class="nav-section-label">([^<]+)</p>', navigation)
    entries = re.findall(r'<(?:button|a)[^>]*class="nav-sublink[^>]*>.*?<span>([^<]+)</span>', navigation)

    assert group_labels == ["Overview", "Settings", "Account", "Help"]
    assert entries == [
        "Home", "Overview", "Servers", "Certification",
        "Settings", "API", "Session", "Git", "DNS", "Notify",
        "Bill", "License", "SSO",
        "Documentation", "Support",
    ]


def test_api_navigation_target_is_registered():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    assert "api: 'API Access'" in app
    assert "api: {heading:'API access'" in app
