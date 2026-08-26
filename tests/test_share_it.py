from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_share_it_catalog_and_instance_boundary_are_syte_hosted_only():
    database = (ROOT / "syte/database.py").read_text(encoding="utf-8")
    service = (ROOT / "syte/share_template_service.py").read_text(encoding="utf-8")
    api = (ROOT / "syte/share_api.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS share_templates" in database
    assert "CREATE TABLE IF NOT EXISTS share_instances" in database
    assert '"is_syte_hosted": 1' not in service  # catalog uses a database flag, not external URLs
    assert "_TEMPLATE_ROOT" in service
    assert "source.is_dir()" in service
    assert 'destination = workspace / "app"' in service
    assert "SYTE_SHARE_INSTANCE_KEY" in service
    assert "instance_key_hash" in service
    assert "_secret" in api
    assert "return {\"ok\": True, **result" in api
    assert "x_share_instance_key" in api


def test_share_it_browser_and_nextjs_control_plane_template_are_shipped():
    index = (ROOT / "syte/static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")
    template = ROOT / "syte/share_templates/control-plane-nextjs"

    assert 'data-view="share-it"' in index
    assert 'id="view-share-it"' in index
    assert 'id="share-it-template-list"' in index
    assert "function loadShareItTemplates()" in app
    assert "function escapeHtml(value)" in app
    assert "/share/templates" in app
    assert "path.startsWith('/share/templates/')" in app
    assert "/provision" in app
    assert ".share-it-template-card" in css
    assert (template / "package.json").is_file()
    assert (template / "Dockerfile").is_file()
    assert "next" in (template / "package.json").read_text(encoding="utf-8")
    assert "SYTE_SHARE_INSTANCE_KEY" in (template / "app/api/control/route.ts").read_text(encoding="utf-8")
    assert "#101010" in (template / "app/globals.css").read_text(encoding="utf-8")


def test_generated_diagnostics_beacon_template_is_internal_and_scoped():
    service = (ROOT / "syte/share_template_service.py").read_text(encoding="utf-8")
    template = ROOT / "syte/share_templates/diagnostics-beacon-node"
    server = (template / "server.js").read_text(encoding="utf-8")

    assert '"id": "diagnostics-beacon-node"' in service
    assert '"source_dir": "diagnostics-beacon-node"' in service
    assert (template / "package.json").is_file()
    assert (template / "Dockerfile").is_file()
    assert "SYTE_SHARE_INSTANCE_KEY" in server
    assert '"x-share-instance-key"' in server
    assert 'encodeURIComponent(instanceId)}/overview' in server
    assert "fetch('/api/overview'" in server
    assert "server-side scoped channel" in server
    assert "@clerk" not in (template / "package.json").read_text(encoding="utf-8")


def test_template_source_copies_into_syte_precreated_empty_app_directory(tmp_path: Path):
    from syte.share_template_service import _copy_template_source

    source = tmp_path / "template"
    source.mkdir()
    (source / "package.json").write_text('{"name":"test-template"}\n', encoding="utf-8")
    (source / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    destination = tmp_path / "workspace" / "app"
    destination.mkdir(parents=True)

    _copy_template_source(source, destination)

    assert (destination / "package.json").is_file()
    assert (destination / "Dockerfile").is_file()
    assert list(destination.iterdir())


def test_template_source_does_not_merge_into_nonempty_app_directory(tmp_path: Path):
    from syte.share_template_service import _copy_template_source

    source = tmp_path / "template"
    source.mkdir()
    (source / "package.json").write_text('{"name":"test-template"}\n', encoding="utf-8")
    destination = tmp_path / "workspace" / "app"
    destination.mkdir(parents=True)
    (destination / "existing.txt").write_text("retain\n", encoding="utf-8")

    try:
        _copy_template_source(source, destination)
    except ValueError as error:
        assert "not empty" in str(error)
    else:
        raise AssertionError("Expected non-empty app directory to be protected")

    assert (destination / "existing.txt").read_text(encoding="utf-8") == "retain\n"


def test_syte_native_template_collection_is_internal_responsive_and_scoped():
    service = (ROOT / "syte/share_template_service.py").read_text(encoding="utf-8")
    templates = {
        "deployment-brief-node": "deployment-brief",
        "project-compass-node": "project-compass",
        "service-watch-node": "service-watch",
    }

    for source_dir, service_name in templates.items():
        source = ROOT / "syte/share_templates" / source_dir
        server = (source / "server.js").read_text(encoding="utf-8")
        package = (source / "package.json").read_text(encoding="utf-8")

        assert f'"id": "{source_dir}"' in service
        assert f'"source_dir": "{source_dir}"' in service
        assert (source / "Dockerfile").is_file()
        assert '"start": "node server.js"' in package
        assert "SYTE_SHARE_INSTANCE_KEY" in server
        assert 'encodeURIComponent(instanceId)}/overview' in server
        assert 'fetch(\'/api/overview\'' in server
        assert 'request.url === "/api/health"' in server
        assert service_name in server
        assert "@media(max-width:" in server
        assert "@clerk" not in package


def test_share_it_tiles_use_rendered_template_preview_assets_and_legacy_project_cards():
    app = (ROOT / "syte/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "syte/static/style.css").read_text(encoding="utf-8")
    preview_root = ROOT / "syte/static/template-previews"
    template_ids = {
        "control-plane-nextjs",
        "diagnostics-beacon-node",
        "deployment-brief-node",
        "project-compass-node",
        "service-watch-node",
    }

    assert "template-previews/${encodeURIComponent(template.id)}.png" in app
    assert "share-it-template-preview" in app
    assert "share-it-template-select" in app
    assert 'class="project-card project-card-reference"' in app
    assert "project-card-site-icon" in app
    assert "projectCardFavicon" in app
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in css
    assert "bottom:-56px" in css
    assert "transform:rotate(-6deg)" in css
    assert ".project-card-reference" in css

    for template_id in template_ids:
        image = preview_root / f"{template_id}.png"
        assert image.is_file()
        assert image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
