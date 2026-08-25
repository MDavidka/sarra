"""Regression coverage for the supported deployment-only product surface."""

from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_module_imports_without_retired_ai_modules():
    module = import_module("syte.main")
    assert module.app is not None


def test_application_exposes_no_ai_provider_or_router_routes():
    app = import_module("syte.main").app
    paths = {route.path for route in app.routes}
    forbidden_terms = ("agent", "model", "provider", "router", "syra", "litellm", "ai.json")
    assert not [path for path in paths if any(term in path.lower() for term in forbidden_terms)]


def test_home_and_navigation_are_minimal_and_modular():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    nav_css = (ROOT / "syte/static/styles/navigation.css").read_text(encoding="utf-8")
    home_css = (ROOT / "syte/static/styles/home.css").read_text(encoding="utf-8")

    assert "home-dashboard-metrics" not in index
    assert "Models &amp; Providers" not in index
    assert "data-view=\"remote-servers\"" in index
    assert "navigation.css" in index and "home.css" in index and "projects.css" in index
    assert ".nav-item.is-active::before" in nav_css
    assert 'content: "|"' in nav_css
    assert "background: transparent" in nav_css
    assert ".home-intro" in home_css
