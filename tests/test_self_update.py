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
        if cmd[:2] == ["git", "show"]:
            return 0, '__version__ = "0.9.3"'
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(self_update, "_read_installed_version", lambda: "0.9.1")
    target = UpdateTarget(source_type="branch", branch="main", label="main", repo="o/r")
    ok, msg, ref = self_update._git_sync_update_target(target)
    assert ok is True
    assert ref == "origin/main"
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
        if cmd[:2] == ["git", "show"]:
            return 0, '__version__ = "0.9.3"'
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(self_update, "_read_installed_version", lambda: "0.9.1")
    target = UpdateTarget(
        source_type="pr",
        branch="cursor/update-from-latest-pr-6cbf",
        label="PR #12",
        pr_number=12,
        repo="o/r",
    )
    ok, msg, ref = self_update._git_sync_update_target(target)
    assert ok is True
    assert ref == "syte-pr-12"
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
        if cmd[:2] == ["git", "show"]:
            return 0, '__version__ = "0.9.3"'
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(self_update, "_read_installed_version", lambda: "0.9.1")
    target = UpdateTarget(
        source_type="pr",
        branch="cursor/update-from-latest-pr-6cbf",
        label="PR #12",
        pr_number=12,
        repo="o/r",
    )
    ok, msg, ref = self_update._git_sync_update_target(target)
    assert ok is True
    assert ref == "origin/cursor/update-from-latest-pr-6cbf"
    assert any("cursor/update-from-latest-pr-6cbf" in str(c) for c in calls)


def test_git_sync_update_target_refuses_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_cmd(cmd, cwd=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, ""
        if len(cmd) >= 4 and cmd[3].startswith("pull/4/head"):
            return 0, "fetched pr"
        if cmd[:2] == ["git", "show"]:
            return 0, '__version__ = "0.4.0"'
        return 0, ""

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(self_update, "_read_installed_version", lambda: "0.9.2")
    target = UpdateTarget(
        source_type="pr",
        branch="cursor/old",
        label="PR #4",
        pr_number=4,
        repo="o/r",
    )
    ok, msg, ref = self_update._git_sync_update_target(target)
    assert ok is False
    assert "Refusing downgrade" in msg
    assert ref == "syte-pr-4"


def test_git_fetch_pr_uses_fetch_head_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    pinned = {"done": False}

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:3] == ["git", "fetch", "origin"] and len(cmd) > 3 and cmd[3] == "pull/9/head:syte-pr-9":
            return 1, "refspec rejected"
        if cmd[:3] == ["git", "fetch", "origin"] and len(cmd) > 3 and cmd[3] == "pull/9/head":
            return 0, "fetched head"
        if cmd[:4] == ["git", "branch", "-f", "syte-pr-9"]:
            pinned["done"] = True
            return 0, "pinned"
        return 0, ""

    def fake_ref_exists(ref: str) -> bool:
        if ref == "FETCH_HEAD":
            return True
        if ref == "syte-pr-9":
            return pinned["done"]
        return False

    monkeypatch.setattr(self_update, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(self_update, "_git_ref_exists", fake_ref_exists)

    ok, msg, ref = self_update._git_fetch_pr(9)
    assert ok is True
    assert ref == "syte-pr-9"
    assert any(len(cmd) > 3 and cmd[3] == "pull/9/head" for cmd in calls)


def test_bootstrap_update_commands_for_pr() -> None:
    target = UpdateTarget(
        source_type="pr",
        branch="cursor/update-from-latest-pr-6cbf",
        label="PR #13",
        pr_number=13,
        repo="o/r",
    )
    cmds = self_update.bootstrap_update_commands(target)
    assert cmds[1] == "git fetch origin pull/13/head:syte-pr-13"
    assert cmds[2] == "git checkout -B syte-update syte-pr-13"


def test_get_update_info_includes_bootstrap_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    target = UpdateTarget(
        source_type="pr",
        branch="cursor/update-from-latest-pr-6cbf",
        label="PR #13",
        pr_number=13,
        repo="o/r",
    )
    monkeypatch.setattr(self_update, "_update_target", lambda: target)
    monkeypatch.setattr(self_update, "_read_installed_version", lambda: "0.9.1")
    info = self_update.get_update_info()
    assert info["work_branch"] == "syte-update"
    assert "bootstrap_commands" in info
    assert any("pull/13/head" in cmd for cmd in info["bootstrap_commands"])
