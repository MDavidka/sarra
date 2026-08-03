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

    def compatible_runner(
        cmd: list[str],
        _timeout: float = 60.0,
        _env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        return runner(cmd)

    monkeypatch.setattr(certificates, "_run", compatible_runner)


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
async def test_cloudflare_token_is_in_subprocess_env_not_command_args(
    proxy_config_stubs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import certificates

    active, data_dir = proxy_config_stubs
    env_path = data_dir / "caddy.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("CLOUDFLARE_API_TOKEN=private-token\n")

    async def cloudflare_env() -> str:
        return str(env_path)

    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(
        cmd: list[str],
        _timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        calls.append((cmd, env))
        return 0, "ok"

    monkeypatch.setattr(certificates, "_write_caddy_env", cloudflare_env)
    monkeypatch.setattr(certificates, "_run", fake_run)

    ok, _message = await certificates.apply_proxy_config()

    assert ok is True
    assert active.read_text() == "new config\n"
    assert calls
    for command, environment in calls:
        assert "private-token" not in " ".join(command)
        assert environment is not None
        assert environment["CLOUDFLARE_API_TOKEN"] == "private-token"


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
    restarted = False

    def fake_run(cmd: list[str]) -> tuple[int, str]:
        nonlocal restarted
        calls.append(cmd)
        if cmd[0:2] == ["caddy", "validate"]:
            return 0, "valid"
        if cmd == ["systemctl", "restart", "caddy"]:
            restarted = True
            return 0, "restarted"
        if cmd[0:2] == ["caddy", "reload"] and restarted:
            return 0, "loaded exact config"
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
        [
            "caddy",
            "reload",
            "--config",
            str(active),
            "--adapter",
            "caddyfile",
        ],
    ]


@pytest.mark.asyncio
async def test_activation_failure_restores_previous_config(
    proxy_config_stubs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import certificates

    active, _data_dir = proxy_config_stubs
    active.parent.mkdir(parents=True)
    active.write_text("known good\n")
    active.chmod(0o640)
    original_stat = active.stat()
    monkeypatch.setattr(certificates.os, "chown", lambda *_args: None)

    def fake_run(cmd: list[str]) -> tuple[int, str]:
        if cmd[0:2] == ["caddy", "validate"]:
            return 0, "valid"
        return 1, "activation unavailable"

    _set_runner(monkeypatch, fake_run)

    ok, message = await certificates.apply_proxy_config()

    assert ok is False
    assert "previous config was restored" in message
    assert active.read_text() == "known good\n"
    assert active.stat().st_mode == original_stat.st_mode
    assert list(active.parent.glob(".Caddyfile.*.rollback")) == []


@pytest.mark.asyncio
async def test_activation_failure_removes_new_config_when_none_existed(
    proxy_config_stubs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import certificates

    active, _data_dir = proxy_config_stubs

    def fake_run(cmd: list[str]) -> tuple[int, str]:
        if cmd[0:2] == ["caddy", "validate"]:
            return 0, "valid"
        return 1, "activation unavailable"

    _set_runner(monkeypatch, fake_run)

    ok, message = await certificates.apply_proxy_config()

    assert ok is False
    assert "newly created config was removed" in message
    assert not active.exists()


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


@pytest.mark.asyncio
async def test_inactive_caddy_starts_with_exact_fallback_config(
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
        if cmd[0:2] == ["caddy", "validate"]:
            return 0, "valid"
        if cmd[0:2] == ["caddy", "start"]:
            return 0, "started"
        return 1, "not running"

    _set_runner(monkeypatch, fake_run)

    ok, _message = await certificates.apply_proxy_config()

    assert ok is True
    assert calls[1] == [
        "caddy",
        "reload",
        "--config",
        str(fallback),
        "--adapter",
        "caddyfile",
    ]
    assert calls[2] == [
        "caddy",
        "start",
        "--config",
        str(fallback),
        "--adapter",
        "caddyfile",
    ]
    assert all(call[0] != "systemctl" for call in calls)
