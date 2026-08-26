"""Regression coverage for project status enrichment."""

from __future__ import annotations


def test_running_helper_uses_process_manager(monkeypatch):
    from syte import main
    from syte import process_manager

    observed: dict[str, str] = {}

    def fake_is_running(project_id: str, deploy_type: str) -> bool:
        observed["project_id"] = project_id
        observed["deploy_type"] = deploy_type
        return True

    monkeypatch.setattr(process_manager, "is_running", fake_is_running)

    assert main._running({"id": "project-1", "deploy_type": "docker"}) is True
    assert observed == {"project_id": "project-1", "deploy_type": "docker"}
