"""Regression checks for the refined projects screen and Git identity controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_projects_screen_has_no_dashboard_statistics():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")

    assert 'id="home-dashboard-metrics"' not in index
    assert "if (name === 'dashboard') { activeServiceId = null; }" in app
    assert "if (name === 'dashboard') { activeServiceId = null; loadOverviewMonitor(); }" not in app


def test_git_subtab_uses_local_svgl_asset_and_connected_profile_control():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    asset = (ROOT / "syte/static/vendor/github-svgl.svg").read_text(encoding="utf-8")

    assert '/static/vendor/github-svgl.svg?v=__VERSION__' in index
    assert 'id="topbar-git-profile"' in index
    assert '<svg' in asset
    assert "function renderTopbarGitProfile(status)" in app
    assert "renderTopbarGitProfile(githubSourceStatus);" in app
    assert "activePlatformPage = 'git';" in app
