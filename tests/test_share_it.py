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
