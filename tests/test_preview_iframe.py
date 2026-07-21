"""Tests for preview iframe checklist and Caddy header generation."""

import pytest

from syte.caddy_routes import preview_iframe_header_lines, reverse_proxy_lines
from syte.preview_iframe import PREVIEW_STRIP_HEADERS, build_iframe_checklist


def test_preview_iframe_header_lines_strip_blocking_headers() -> None:
    csp = "frame-ancestors 'self' https://sycord.com https://*.sycord.com"
    lines = "\n".join(preview_iframe_header_lines(csp, indent="    "))
    assert "-X-Frame-Options" in lines
    assert "-Strict-Transport-Security" in lines
    assert "-Permissions-Policy" in lines
    assert "-Cross-Origin-Opener-Policy" in lines
    assert "Cross-Origin-Resource-Policy cross-origin" in lines
    assert "Access-Control-Allow-Origin https://sycord.com" in lines
    assert "Access-Control-Allow-Origin *" not in lines
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


def test_probe_https_available_rejects_bad_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_open(*_args, **_kwargs):
        raise OSError("TLS handshake failed")

    monkeypatch.setattr("urllib.request.urlopen", fail_open)
    from syte import preview_iframe as pi

    pi._PROBE_CACHE.clear()
    assert pi.probe_https_available("https://preview.example.com") is False


def test_probe_https_available_accepts_ok_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: FakeResponse())
    from syte import preview_iframe as pi

    pi._PROBE_CACHE.clear()
    assert pi.probe_https_available("https://preview.example.com") is True


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
