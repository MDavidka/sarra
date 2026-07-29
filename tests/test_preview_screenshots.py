"""Tests for preview screenshot browser discovery and capture."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_find_headless_browser_uses_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from syte import preview_access

    fake = tmp_path / "fake-chrome"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SYTE_CHROMIUM_PATH", str(fake))
    preview_access._resolved_browser = False
    assert preview_access.find_headless_browser(force_refresh=True) == str(fake)


def test_find_headless_browser_checks_known_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from syte import preview_access

    fake = tmp_path / "chromium"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.delenv("SYTE_CHROMIUM_PATH", raising=False)
    monkeypatch.setattr(preview_access.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        preview_access,
        "_BROWSER_PATH_CANDIDATES",
        (str(fake), "/nope/chromium"),
    )
    preview_access._resolved_browser = False
    assert preview_access.find_headless_browser(force_refresh=True) == str(fake)


def test_browser_install_hint_mentions_apt() -> None:
    from syte.preview_access import browser_install_hint

    hint = browser_install_hint()
    assert "chromium" in hint.lower()
    assert "SYTE_CHROMIUM_PATH" in hint


@pytest.mark.asyncio
async def test_capture_screenshot_writes_temp_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from syte import preview_access

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    fake_browser = tmp_path / "chrome"
    fake_browser.write_text("#!/bin/sh\n")
    fake_browser.chmod(0o755)

    def fake_run(cmd, capture_output=True, timeout=60, cwd=None):
        # Emulate chromium writing --screenshot=<path>
        out = None
        for arg in cmd:
            if isinstance(arg, str) and arg.startswith("--screenshot="):
                out = Path(arg.split("=", 1)[1])
                break
        assert out is not None
        out.write_bytes(png)
        return MagicMock(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(preview_access.subprocess, "run", fake_run)
    result = await preview_access._capture_screenshot(
        "https://example.com/",
        width=390,
        height=844,
        viewport="phone",
        browser=str(fake_browser),
    )
    assert result["ok"] is True
    assert result["viewport"] == "phone"
    assert result["png_bytes"].startswith(b"\x89PNG")
    assert result["bytes"] == len(png)


@pytest.mark.asyncio
async def test_capture_preview_screenshots_no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import preview_access

    monkeypatch.setattr(preview_access, "find_headless_browser", lambda **_k: None)
    results = await preview_access.capture_preview_screenshots(
        "https://example.com/", viewports=("desktop", "phone")
    )
    assert results["desktop"]["error"] == "no_browser"
    assert results["phone"]["error"] == "no_browser"
    assert "SYTE_CHROMIUM_PATH" in results["desktop"]["message"]
