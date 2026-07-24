"""Tests for token-efficiency helpers (CLI filters + aiignore)."""

from pathlib import Path

from syte.token_efficiency import (
    DEFAULT_AIIGNORE_PATTERNS,
    ensure_workspace_aiignore,
    filter_cli_output,
    load_aiignore_patterns,
    path_is_ignored,
)


def test_path_is_ignored_defaults() -> None:
    assert path_is_ignored("app/node_modules/lodash/index.js")
    assert path_is_ignored("package-lock.json")
    assert path_is_ignored("app/.next/server.js")
    assert not path_is_ignored("app/app/page.tsx")


def test_load_aiignore_merges_file(tmp_path: Path) -> None:
    (tmp_path / ".aiignore").write_text("secrets/\n*.env.local\n", encoding="utf-8")
    patterns = load_aiignore_patterns(tmp_path)
    assert "node_modules/" in patterns
    assert "secrets/" in patterns
    assert path_is_ignored("secrets/token.txt", patterns)
    assert path_is_ignored("app/.env.local", patterns)


def test_ensure_workspace_aiignore_creates_file(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    path = ensure_workspace_aiignore(app)
    assert path is not None
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "node_modules/" in text
    # Second call is idempotent.
    assert ensure_workspace_aiignore(app) == path


def test_filter_cli_output_strips_passing_and_progress() -> None:
    raw = "\n".join([
        "Running tests…",
        "✓ should render button",
        "PASS src/Button.test.tsx",
        "FAIL src/Form.test.tsx",
        "  Expected true",
        "npm timing npm:load Completed",
        "Tests: 12 passed, 1 failed",
        "Test Suites: 3 passed, 1 failed",
    ])
    filtered = filter_cli_output("npm test", raw)
    assert "FAIL src/Form.test.tsx" in filtered
    assert "Expected true" in filtered
    assert "✓ should render button" not in filtered
    assert "PASS src/Button.test.tsx" not in filtered
    assert "npm timing" not in filtered


def test_filter_git_status_collapses_verbose() -> None:
    raw = "\n".join([
        "On branch main",
        "Changes not staged for commit:",
        "  (use \"git add <file>...\" to update what will be committed)",
        "\tmodified:   app/app/page.tsx",
        "\tmodified:   app/components/Hero.tsx",
        "hint: use git status -sb",
    ])
    filtered = filter_cli_output("git status", raw)
    assert "app/app/page.tsx" in filtered
    assert "Hero.tsx" in filtered
    assert "hint:" not in filtered


def test_default_patterns_cover_lockfiles() -> None:
    assert "yarn.lock" in DEFAULT_AIIGNORE_PATTERNS
    assert "pnpm-lock.yaml" in DEFAULT_AIIGNORE_PATTERNS
