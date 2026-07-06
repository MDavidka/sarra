"""Tests for preview iframe checklist and Caddy header generation."""

from syte.caddy_routes import preview_iframe_header_lines, reverse_proxy_lines
from syte.preview_iframe import PREVIEW_STRIP_HEADERS, build_iframe_checklist


def test_preview_iframe_header_lines_strip_blocking_headers() -> None:
    csp = "frame-ancestors 'self' https://sycord.com https://*.sycord.com"
    lines = "\n".join(preview_iframe_header_lines(csp, indent="    "))
    assert "-X-Frame-Options" in lines
    assert "-Strict-Transport-Security" in lines
    assert "-Permissions-Policy" in lines
    assert "-Cross-Origin-Opener-Policy" in lines
    assert csp in lines


def test_reverse_proxy_strips_upstream_headers() -> None:
    lines = "\n".join(reverse_proxy_lines(4000, strip_frame_headers=True, indent="    "))
    for name in PREVIEW_STRIP_HEADERS:
        assert f"header_down -{name}" in lines


def test_iframe_checklist_all_ok_when_configured() -> None:
    project = {
        "preview_domain": "previewwg-tsst76.sycord.com",
        "preview_port": 4001,
        "preview_status": "running",
    }
    csp = "frame-ancestors 'self' https://sycord.com https://*.sycord.com *"
    result = build_iframe_checklist(project, frame_csp=csp, live_headers=None)
    assert result["preview_url"] == "https://previewwg-tsst76.sycord.com"
    assert result["all_ok"] is True


def test_iframe_checklist_detects_blocking_headers() -> None:
    project = {
        "preview_domain": "previewwg-tsst76.sycord.com",
        "preview_port": 4001,
        "preview_status": "running",
    }
    csp = "frame-ancestors 'self' https://sycord.com https://*.sycord.com"
    live = {
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "frame-ancestors 'none'",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    result = build_iframe_checklist(project, frame_csp=csp, live_headers=live)
    assert result["all_ok"] is False
    failed = [i for i in result["items"] if not i["ok"]]
    assert any(i["id"] == "x_frame_options" for i in failed)
