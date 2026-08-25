"""Regression coverage for the AI-, provider-, and model-free Sycord surface."""

from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_module_imports_after_feature_removal():
    module = import_module("syte.main")
    assert module.app is not None


def test_application_exposes_no_ai_provider_model_or_router_routes():
    app = import_module("syte.main").app
    paths = {route.path for route in app.routes}
    forbidden_terms = ("agent", "model", "provider", "router", "syra", "litellm", "ai.json")
    assert not [path for path in paths if any(term in path.lower() for term in forbidden_terms)]


def test_legacy_frontend_hides_retired_feature_tabs_and_controls():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    retired_markers = (
        'data-view="ai"',
        'data-view="models"',
        'data-view="router"',
        'id="view-ai"',
        'id="view-models"',
        'id="view-router"',
        'id="nine-router-tls-card"',
        'id="new-feature-card"',
        "Models &amp; Providers",
        "9Router",
        "LiteLLM",
    )
    assert not [marker for marker in retired_markers if marker in index]
