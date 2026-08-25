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


def test_servers_checklist_and_mobile_git_workspace_are_scoped_and_responsive():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")

    assert "function renderRemoteServersWorkspace(target)" in app
    assert "server-checklist-page" in app
    assert "data-server-enroll" in app
    assert "data-server-country" in app
    assert "data-server-save" in app
    assert "server-checklist-metrics" in app
    assert "function serverChecklistPing(value)" in app
    assert ".server-checklist-page" in css
    assert ".server-checklist-row" in css
    assert ".git-workspace-page" in css
    assert ".git-repository-card" in css
    assert ".git-repository-toolbar" in css
    assert ".nav-server-performance>span { display:block!important; background:#16a34a!important; }" in css


def test_operational_project_workspaces_and_certificate_route_are_dedicated():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")

    for tab in ("domains", "env", "firewall", "cdn", "speed", "logs", "rollbacks", "settings"):
        assert f'data-svc-tab="{tab}"' in index
        assert f'data-svc-panel="{tab}"' in index
    assert 'id="svc-env-cards"' in index
    assert 'id="svc-env-modal"' in index
    assert 'id="svc-settings-auto-deploy"' in index
    assert 'id="svc-rollback-history"' in index
    assert "function renderServiceManagementWorkspaces(project)" in app
    assert "function renderServiceRollbackHistory(project)" in app
    assert "function renderCertificateWorkspace()" in app
    assert "if (isCertificates)" in app
    assert "data-certificate-issue" in app
    assert ".svc-domain-workspace" in css
    assert ".svc-env-workspace" in css
    assert ".svc-firewall-workspace" in css
    assert ".svc-cdn-workspace" in css
    assert ".certificate-workspace" in css


def test_sidebar_selection_is_scoped_to_the_current_navigation_context():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")

    assert "viewName === 'platform' && isPlatformLink" in app
    assert "viewName !== 'platform' && !isPlatformLink" in app
    assert "event.stopPropagation();" in app
    assert "const allowed = ['general', 'domains', 'env', 'firewall', 'cdn', 'speed', 'logs', 'rollbacks', 'preview', 'settings'];" in app


def test_lucide_is_locally_served_and_cannot_block_login_startup():
    vendor = ROOT / "syte/static/vendor/lucide.min.js"
    assets = (ROOT / "docs/external_assets.md").read_text(encoding="utf-8")

    assert vendor.stat().st_size > 100_000
    content = vendor.read_text(encoding="utf-8")
    assert "document.write" not in content
    assert "createIcons" in content
    assert "lucide@0.468.0" in assets


def test_console_login_head_has_no_parser_blocking_shoelace_module():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")

    assert "shoelace-autoloader.js" not in index
    assert "@shoelace-style/shoelace" not in index
    assert "fonts.googleapis.com" not in index
    assert '<script async src="/static/vendor/lucide.min.js?v=__VERSION__"></script>' in index


def test_native_account_gate_renders_before_the_main_application_bundle():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")

    assert 'id="inline-account-login-form"' in index
    assert "fetch('/api/auth/session', {credentials: 'same-origin'})" in index
    assert "fetch('/api/auth/login'" in index
    assert "new MutationObserver" in index
    assert index.index('id="inline-account-login-form"') < index.index('/static/app.js?v=__VERSION__')
    assert index.index('new MutationObserver') < index.index('/static/style.css?v=__VERSION__')


def test_legacy_syte_library_catalog_is_not_part_of_the_console():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")

    assert "Syte Library" not in index
    assert 'id="platform-store-panel"' not in index
    assert "activePlatformPage === 'docker') activePlatformPage = 'overview'" in app
    assert "if (activePlatformPage === 'docker') loadDockerStore();" not in app


def test_certification_is_a_dedicated_white_cloudflare_workspace():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")

    assert "is-certificates-workspace" in app
    assert "certificate-structured-workspace" in app
    assert "Certificate security" in app
    assert "certificate-structured-provider" in app
    assert "data-certificate-filter" in app
    assert "data-certificate-use-domain" in app
    assert "Issue certificate" in app
    assert "data-certificate-issue" in app
    assert "data-certificate-guide" in app
    assert "/static/vendor/cloudflare-svgl.svg" in app
    assert ".platform-workspace.is-certificates-workspace > .platform-page-head" in css
    assert ".certificate-structured-workspace" in css
    assert ".certificate-structured-list" in css
    assert "background:#fff" in css
