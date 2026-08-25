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
    assert ".nav-sublink.active > span:not(.nav-server-performance) { background:transparent!important;" in css
    assert ".nav-sublink.active::before { display:none; }" in css
    assert "width:25px; border-radius:0 6px 6px 0" not in css


def test_servers_navigation_uses_globally_refreshed_combined_ram_and_cpu_load():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")

    assert 'id="server-nav-performance"' in index
    assert 'role="progressbar"' in index
    assert "function renderServerNavigationPerformance(metrics = liveSystemMetrics)" in app
    assert "const load = Math.round((cpu + ram) / 2);" in app
    assert "recordLiveSystemMetrics(sys);" in app
    assert app.index("recordLiveSystemMetrics(sys);") < app.index("renderServerSwarm(sys);")
    assert "setInterval(loadSystem, 10000)" in app
    assert "Combined server load" in app
    assert ".nav-server-performance" in css
    assert "border-radius:999px" in css


def test_overview_has_live_ram_cpu_disk_cards_and_system_disk_exposure():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")
    main = (ROOT / "syte/main.py").read_text(encoding="utf-8")

    assert "overviewMetricHistory" in app
    assert "function renderOverviewLiveMetrics()" in app
    assert "metricCard('ram', 'RAM'" in app
    assert "metricCard('cpu', 'CPU'" in app
    assert "metricCard('disk', 'Disk'" in app
    assert '"disk_percent": stats["disk_percent"]' in main
    assert ".overview-metric-grid" in css
    assert ".overview-sparkline" in css
    assert ".overview-workspace { display:grid; gap:16px; width:100%; max-width:none; margin:0; }" in css
    assert ".overview-services-card { width:100%; max-width:none;" in css
    assert "flex:0 0 76px" in css
