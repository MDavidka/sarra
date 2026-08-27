"""Regression coverage for server-only project environment values."""

from __future__ import annotations

import json

from syte import main


def test_enrich_keeps_project_environment_values_and_paths_server_only(monkeypatch) -> None:
    monkeypatch.setattr(main, "_running", lambda _project: False)
    monkeypatch.setattr(main, "_project_url", lambda _project: "https://example.test")
    monkeypatch.setattr("syte.workspace.ensure_workspace", lambda _project_id: None)
    monkeypatch.setattr("syte.preview_manager.preview_meta", lambda _project: {})
    monkeypatch.setattr("syte.project_enrich.enrich_ssl", lambda _project: {})

    safe = main._enrich(
        {
            "id": "project-safe-response",
            "name": "safe-response",
            "port": 3000,
            "env_vars": json.dumps(
                {
                    "PUBLIC_LABEL": "documented-label",
                    "SERVER_ONLY_TEST_KEY": "server-only-test-value",
                    "SYTE_STACK": "nextjs",
                }
            ),
            "workspace_path": "/srv/private/workspace",
            "app_path": "/srv/private/workspace/app",
            "data_path": "/srv/private/workspace/data",
        }
    )

    assert safe["environment_keys"] == ["PUBLIC_LABEL", "SERVER_ONLY_TEST_KEY", "SYTE_STACK"]
    assert safe["environment_count"] == 3
    assert safe["stack"] == "nextjs"
    assert "env_vars" not in safe
    assert "workspace_path" not in safe
    assert "app_path" not in safe
    assert "data_path" not in safe
    assert "server-only-test-value" not in json.dumps(safe)


def test_variables_panel_uses_safe_key_metadata_and_single_variable_mutations() -> None:
    app = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    source = main.__file__
    assert source is not None
    server = open(source, encoding="utf-8").read()

    assert "const keys = project?.environment_keys;" in app
    assert "Stored server-side" in app
    assert "/environment/${encodeURIComponent(key)}" in app
    assert "Existing values are never shown in the browser." in app
    assert '@app.put("/api/projects/{project_id}/environment")' in server
    assert '@app.delete("/api/projects/{project_id}/environment/{key}")' in server
    assert 'p.pop("env_vars", None)' in server
    assert 'p.pop("workspace_path", None)' in server
