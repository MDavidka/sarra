"""Focused tests for transactional Caddy configuration updates."""

from collections.abc import Callable
from pathlib import Path

import pytest

from syte.config import settings


@pytest.fixture
def proxy_config_stubs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    from syte import certificates

    active = tmp_path / "etc" / "Caddyfile"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "caddy_config_path", active)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(certificates.shutil, "which", lambda _name: "/usr/bin/caddy")
    monkeypatch.setattr(certificates, "ensure_caddy", lambda: (True, "active"))

    async def no_cloudflare() -> list[str]:
        return []

    async def generated_config() -> str:
        return "new config\n"

    async def no_env() -> None:
        return None

    monkeypatch.setattr(certificates, "apply_cloudflare_integration", no_cloudflare)
    monkeypatch.setattr(certificates, "async_generate_caddyfile", generated_config)
    monkeypatch.setattr(certificates, "_write_caddy_env", no_env)
    return active, data_dir


def _set_runner(
    monkeypatch: pytest.MonkeyPatch,
    runner: Callable[[list[str]], tuple[int, str]],
) -> None:
    from syte import certificates

    monkeypatch.setattr(certificates, "_run", runner)


@pytest.mark.asyncio
async def test_apply_proxy_config_validates_candidate_before_atomic_replace(
    proxy_config_stubs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import certificates

    active, _data_dir = proxy_config_stubs
    active.parent.mkdir(parents=True)
    active.write_text("old config\n")
    active_stat = active.stat()
    ownership_updates: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(
        certificates.os,
        "chown",
        lambda path, uid, gid: ownership_updates.append((Path(path), uid, gid)),
    )
    calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        if cmd[1] == "validate":
            candidate = Path(cmd[cmd.index("--config") + 1])
            assert candidate.parent == active.parent
            assert candidate != active
            assert candidate.read_text() == "new config\n"
            assert active.read_text() == "old config\n"
        return 0, "ok"

    _set_runner(monkeypatch, fake_run)

    ok, message = await certificates.apply_proxy_config()

    assert ok is True
    assert "applied" in message.lower()
    assert active.read_text() == "new config\n"
    assert len(ownership_updates) == 1
    candidate_path, uid, gid = ownership_updates[0]
    assert candidate_path.parent == active.parent
    assert (uid, gid) == (active_stat.st_uid, active_stat.st_gid)
    assert calls[0][0:2] == ["caddy", "validate"]
    assert calls[1] == [
        "caddy",
        "reload",
        "--config",
        str(active),
        "--adapter",
        "caddyfile",
    ]
    assert list(active.parent.glob(".Caddyfile.*.tmp")) == []


@pytest.mark.asyncio
async def test_validation_failure_preserves_active_config_and_cleans_candidate(
    proxy_config_stubs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import certificates

    active, _data_dir = proxy_config_stubs
    active.parent.mkdir(parents=True)
    active.write_text("known good\n")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        return 1, "bad directive"

    _set_runner(monkeypatch, fake_run)

    ok, message = await certificates.apply_proxy_config()

    assert ok is False
    assert "active configuration was preserved" in message
    assert active.read_text() == "known good\n"
    assert len(calls) == 1
    assert calls[0][0:2] == ["caddy", "validate"]
    assert list(active.parent.glob(".Caddyfile.*.tmp")) == []


@pytest.mark.asyncio
async def test_reload_is_preferred_and_restart_is_last_fallback(
    proxy_config_stubs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import certificates

    active, _data_dir = proxy_config_stubs
    calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        if cmd[0:2] == ["caddy", "validate"]:
            return 0, "valid"
        if cmd == ["systemctl", "restart", "caddy"]:
            return 0, "restarted"
        return 1, "unavailable"

    _set_runner(monkeypatch, fake_run)

    ok, _message = await certificates.apply_proxy_config()

    assert ok is True
    assert calls[1:] == [
        [
            "caddy",
            "reload",
            "--config",
            str(active),
            "--adapter",
            "caddyfile",
        ],
        ["systemctl", "reload", "caddy"],
        ["systemctl", "restart", "caddy"],
    ]


@pytest.mark.asyncio
async def test_fallback_config_is_validated_and_reloaded_by_exact_path(
    proxy_config_stubs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import certificates

    active, data_dir = proxy_config_stubs
    active.parent.parent.mkdir(parents=True, exist_ok=True)
    active.parent.write_text("blocks directory creation")
    fallback = data_dir / "Caddyfile"
    calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        return 0, "ok"

    _set_runner(monkeypatch, fake_run)

    ok, _message = await certificates.apply_proxy_config()

    assert ok is True
    assert fallback.read_text() == "new config\n"
    candidate_path = Path(calls[0][calls[0].index("--config") + 1])
    assert candidate_path.parent == data_dir
    assert calls[1] == [
        "caddy",
        "reload",
        "--config",
        str(fallback),
        "--adapter",
        "caddyfile",
    ]
    assert list(data_dir.glob(".Caddyfile.*.tmp")) == []
