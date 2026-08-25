"""Regression coverage for application-module start-up."""

from importlib import import_module


def test_main_module_imports_after_agent_feature_removal():
    """The server must not import modules removed with the optional agent feature."""
    module = import_module("syte.main")

    assert module.app is not None


def test_running_uses_project_deployment_type(monkeypatch):
    module = import_module("syte.main")
    monkeypatch.setattr("syte.process_manager.is_running", lambda project_id, deploy_type: (project_id, deploy_type) == ("project-1", "docker"))

    assert module._running({"id": "project-1", "deploy_type": "docker"}) is True
