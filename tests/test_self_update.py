"""Tests for self-update restart helpers."""

from pathlib import Path

import pytest

import syte.self_update as self_update
from syte.update_source import UpdateTarget


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


def test_git_sync_update_target_fetches_before_checkout_for_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, ""
        if cmd[:3] == ["git", "fetch", "origin"] and len(cmd) == 3:
            return 0, ""
        if cmd[:3] == ["git", "fetch", "origin"] and cmd[3].startswith("+refs/heads/main"):
            return 0, "fetched main"
        if cmd[:3] == ["git", "checkout", "-B"]:
            return 0, "checked out"
        if cmd[:3] == ["git", "reset", "--hard"]:
            return 0, "reset"
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    target = UpdateTarget(source_type="branch", branch="main", label="main", repo="o/r")
    ok, msg = self_update._git_sync_update_target(target)
    assert ok is True
    fetch_idx = next(i for i, c in enumerate(calls) if c[:3] == ["git", "fetch", "origin"])
    checkout_idx = next(i for i, c in enumerate(calls) if c[:3] == ["git", "checkout", "-B"])
    assert fetch_idx < checkout_idx


def test_git_sync_update_target_uses_pr_head_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, ""
        if len(cmd) >= 4 and cmd[3].startswith("pull/12/head"):
            return 0, "fetched pr"
        if cmd[:3] == ["git", "checkout", "-B"]:
            return 0, "checked out"
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    target = UpdateTarget(
        source_type="pr",
        branch="cursor/update-from-latest-pr-6cbf",
        label="PR #12",
        pr_number=12,
        repo="o/r",
    )
    ok, msg = self_update._git_sync_update_target(target)
    assert ok is True
    assert any("pull/12/head" in (c[3] if len(c) > 3 else "") for c in calls)
    assert ["git", "checkout", "-B", "syte-update", "syte-pr-12"] in calls


def test_git_sync_update_target_branch_fallback_after_pr_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, ""
        if len(cmd) >= 4 and "pull/12/head" in cmd[3]:
            return 1, "pr fetch failed"
        if cmd[:3] == ["git", "fetch", "origin"] and len(cmd) == 3:
            return 0, ""
        if cmd[:3] == ["git", "fetch", "origin"] and "cursor/" in cmd[3]:
            return 0, "fetched branch"
        if cmd[:3] == ["git", "checkout", "-B"]:
            return 0, "checked out"
        if cmd[:3] == ["git", "reset", "--hard"]:
            return 0, "reset"
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    target = UpdateTarget(
        source_type="pr",
        branch="cursor/update-from-latest-pr-6cbf",
        label="PR #12",
        pr_number=12,
        repo="o/r",
    )
    ok, msg = self_update._git_sync_update_target(target)
    assert ok is True
    assert any("cursor/update-from-latest-pr-6cbf" in str(c) for c in calls)
