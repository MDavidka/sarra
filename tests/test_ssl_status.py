"""Tests for SSL status detection."""

from pathlib import Path

import pytest

import syte.ssl_status as ssl_status


@pytest.fixture
def cert_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "certificates"
    base.mkdir()
    monkeypatch.setattr(ssl_status, "_cert_dir", lambda: base)
    return base


def test_exact_host_cert_detected(cert_root: Path) -> None:
    host_dir = cert_root / "acme-v02.api.letsencrypt.org-directory" / "app.example.com"
    host_dir.mkdir(parents=True)
    (host_dir / "app.example.com.crt").write_text("cert")

    assert ssl_status._caddy_has_cert("app.example.com") is True


def test_wildcard_cert_covers_subdomain(cert_root: Path) -> None:
    zone_dir = (
        cert_root
        / "acme-v02.api.letsencrypt.org-directory"
        / "wildcard_.sycord.site"
    )
    zone_dir.mkdir(parents=True)
    (zone_dir / "wildcard_.sycord.site.crt").write_text("cert")

    assert ssl_status._caddy_has_cert("previewk-mysite.sycord.site") is True
    assert ssl_status._caddy_has_cert("mysite.sycord.site") is True


def test_wildcard_cert_does_not_cover_other_zone(cert_root: Path) -> None:
    zone_dir = (
        cert_root
        / "acme-v02.api.letsencrypt.org-directory"
        / "wildcard_.sycord.site"
    )
    zone_dir.mkdir(parents=True)
    (zone_dir / "wildcard_.sycord.site.crt").write_text("cert")

    assert ssl_status._caddy_has_cert("app.other.com") is False


def test_project_ssl_summary_pending_without_cert(cert_root: Path) -> None:
    project = {
        "domain": "mysite.sycord.site",
        "preview_domain": "previewk-mysite.sycord.site",
        "preview_port": 4000,
    }
    summary = ssl_status.project_ssl_summary(project)

    assert summary["production"]["configured"] is True
    assert summary["production"]["active"] is False
    assert summary["production"]["label"] == "SSL pending"
    assert summary["preview"]["label"] == "Preview SSL pending"
    assert summary["badge"] == "pending"


def test_project_ssl_summary_active_with_wildcard(cert_root: Path) -> None:
    zone_dir = (
        cert_root
        / "acme-v02.api.letsencrypt.org-directory"
        / "wildcard_.sycord.site"
    )
    zone_dir.mkdir(parents=True)
    (zone_dir / "wildcard_.sycord.site.crt").write_text("cert")

    project = {
        "domain": "mysite.sycord.site",
        "preview_domain": "previewk-mysite.sycord.site",
        "preview_port": 4000,
    }
    summary = ssl_status.project_ssl_summary(project)

    assert summary["production"]["active"] is True
    assert summary["production"]["label"] == "HTTPS"
    assert summary["badge"] == "https"
    assert summary["badge_label"] == "SSL"
