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


def test_selected_sidebar_card_and_overview_workspace_are_reference_aligned():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")

    assert "overview-workspace" in app
    assert "Managed apps" in app
    assert "9Router" not in app[app.index("function renderOverviewHealth"):app.index("async function renderProfileWorkspace")]
    assert "width:32px; height:32px" in css
    assert ".git-nav-logo { width:18px; height:18px; object-fit:contain; opacity:1; filter:invert(1); }" in css
    assert "rgba(24,24,27,.055)" in css
    assert ".nav-sublink.active::before { display:none; }" in css
    assert "width:25px; border-radius:0 6px 6px 0" not in css


def test_servers_navigation_has_live_database_backed_performance_bar():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")

    assert 'id="server-nav-performance"' in index
    assert 'role="progressbar"' in index
    assert "function renderServerNavigationPerformance(nodes = [])" in app
    assert "api('/platform/fleet')" in app
    assert "Average server load" in app
    assert ".nav-server-performance" in css
    assert "border-radius:999px" in css
