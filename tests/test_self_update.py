"""Tests for self-update restart helpers."""

from pathlib import Path

import pytest

import syte.self_update as self_update


def test_port_listener_pid_parses_ss_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_cmd(cmd, cwd=None):
        if cmd[0] == "ss":
            return 0, 'LISTEN 0 128 0.0.0.0:8787 0.0.0.0:* users:(("uvicorn",pid=4242,fd=3))'
        return 1, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    assert self_update._port_listener_pid(8787) == 4242


def test_restart_via_systemd_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:2] == ["systemctl", "restart"]:
            return 0, "restarted"
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    ok, msg = self_update._restart_via_systemd()
    assert ok is True
    assert "restarted" in msg
    assert ["systemctl", "restart", "syte"] in calls


def test_schedule_restart_uses_python_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    class FakeFile:
        def write(self, _text: str) -> None:
            pass

        def flush(self) -> None:
            pass

    monkeypatch.setattr(self_update.subprocess, "Popen", FakePopen)
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: FakeFile())
    self_update._schedule_restart()
    assert captured["args"][-2:] == ["syte.self_update", "--apply-and-restart"]


def test_git_pull_latest_checks_out_main(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, ""
        if cmd[:3] == ["git", "checkout", "main"]:
            return 0, "Switched to branch 'main'"
        if cmd[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if cmd[:3] == ["git", "pull", "--ff-only"]:
            return 0, "Already up to date."
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(self_update, "_current_branch", lambda: "cursor/old-branch")

    ok, msg = self_update._git_pull_latest("main")
    assert ok is True
    assert "main" in msg
    assert ["git", "checkout", "main"] in calls
    assert ["git", "pull", "--ff-only", "origin", "main"] in calls


def test_git_pull_latest_resets_when_ff_only_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, ""
        if cmd[:3] == ["git", "checkout", "main"]:
            return 0, ""
        if cmd[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if cmd[:3] == ["git", "pull", "--ff-only"]:
            return 1, "fatal: Not possible to fast-forward"
        if cmd[:2] == ["git", "reset"]:
            return 0, "HEAD is now at abc123"
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(self_update, "_current_branch", lambda: "main")

    ok, msg = self_update._git_pull_latest("main")
    assert ok is True
    assert ["git", "reset", "--hard", "origin/main"] in calls
    assert "abc123" in msg
