"""Token-efficiency helpers for agent check-fix turns.

Highest-leverage practices:
1. Diff-only / changed-file scoping instead of full-repo dumps
2. Filter noisy CLI/git/test/lint output before it enters the model context
3. Honor ``.aiignore`` / ``.copilotignore`` so build artifacts never enter listings
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Iterable

# Default ignore patterns seeded into new workspaces and always applied.
DEFAULT_AIIGNORE_PATTERNS: tuple[str, ...] = (
    "node_modules/",
    ".next/",
    "dist/",
    "build/",
    "out/",
    "coverage/",
    ".turbo/",
    ".cache/",
    "__pycache__/",
    "*.pyc",
    ".venv/",
    "venv/",
    ".git/",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.log",
    "*.tsbuildinfo",
)

_AIIGNORE_FILENAMES = (".aiignore", ".copilotignore")

# Progress / spinner noise common in npm, pytest, cargo, etc.
_PROGRESS_RE = re.compile(
    r"(?:"
    r"[\u2800-\u28FF]"  # braille spinners
    r"|[|/\-\\](?:\s+\d+%|\s+\d+/\d+)"
    r"|\r"
    r"|^\s*\d+(\.\d+)?%\s*$"
    r"|(?:Downloading|Fetched|Resolving|Building|Compiling).{0,80}\d+%"
    r")",
    re.MULTILINE,
)

_PASSING_TEST_RE = re.compile(
    r"(?im)^(?:\s*(?:✓|✔)\s.*|"
    r"\s*(?:PASS|PASSED)\b.*|"
    r"\s*\bok\b.*|"
    r"\s*\d+\s+passed(?:,\s*\d+\s+warnings?)?\s*$|"
    r"\s*Test Suites:\s*\d+\s+passed(?!,).*$|"
    r"\s*Tests:\s*\d+\s+passed(?!,).*$)$"
)

_VERBOSE_LOG_RE = re.compile(
    r"(?im)^(?:DEBUG|TRACE|verbose|npm (?:timing|http)|"
    r"\s*console\.(?:debug|trace)\b).*$"
)

_GIT_NOISE_RE = re.compile(
    r"(?im)^(?:hint:|warning: LF will be replaced|"
    r"Create a pull request for|"
    r"\s*\.+\s*$).*$"
)


def default_aiignore_contents() -> str:
    lines = [
        "# Syte agent ignore — keep build artifacts / lockfiles out of model context",
        *DEFAULT_AIIGNORE_PATTERNS,
        "",
    ]
    return "\n".join(lines)


def ensure_workspace_aiignore(app_root: Path) -> Path | None:
    """Create ``.aiignore`` under the app root when missing. Returns the path used."""
    if not app_root.exists():
        return None
    target = app_root / ".aiignore"
    if not target.exists():
        try:
            target.write_text(default_aiignore_contents(), encoding="utf-8")
        except OSError:
            return None
    return target


def load_aiignore_patterns(*roots: Path) -> list[str]:
    """Load ignore globs from ``.aiignore`` / ``.copilotignore`` under any root."""
    patterns: list[str] = list(DEFAULT_AIIGNORE_PATTERNS)
    seen = set(patterns)
    for root in roots:
        if not root:
            continue
        for name in _AIIGNORE_FILENAMES:
            path = root / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line not in seen:
                    patterns.append(line)
                    seen.add(line)
    return patterns


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def path_is_ignored(rel_path: str, patterns: Iterable[str] | None = None) -> bool:
    """Return True when ``rel_path`` matches an aiignore-style pattern."""
    rel = _normalize_rel(rel_path)
    if not rel:
        return False
    pats = list(patterns) if patterns is not None else list(DEFAULT_AIIGNORE_PATTERNS)
    parts = rel.split("/")
    for pat in pats:
        p = pat.strip()
        if not p:
            continue
        directory_only = p.endswith("/")
        bare = p.rstrip("/")
        # Directory segment match (node_modules anywhere in path).
        if directory_only or bare in {
            "node_modules", ".next", "dist", "build", "out", "coverage",
            ".turbo", ".cache", "__pycache__", ".venv", "venv", ".git",
        }:
            if bare in parts:
                return True
        # Glob against full relative path and basename.
        if fnmatch.fnmatch(rel, bare) or fnmatch.fnmatch(rel, p):
            return True
        if fnmatch.fnmatch(parts[-1], bare) or fnmatch.fnmatch(parts[-1], p):
            return True
        # Prefix directory globs like "dist/**"
        if bare.endswith("**") and rel.startswith(bare[:-2].rstrip("/")):
            return True
    return False


def filter_paths(paths: Iterable[str], patterns: Iterable[str] | None = None) -> list[str]:
    return [p for p in paths if not path_is_ignored(p, patterns)]


def filter_cli_output(command: str, output: str, *, max_chars: int = 12_000) -> str:
    """Strip progress bars, passing-test spam, and verbose logs from tool output.

    Always keeps failures, diffs, and error lines. Caps the final payload.
    """
    if not output:
        return output
    cmd = (command or "").strip().lower()
    text = output.replace("\r\n", "\n").replace("\r", "\n")

    # Prefer scoped git summaries when the agent dumped a full status/log.
    if cmd.startswith("git status") and "--short" not in cmd and "-s" not in cmd:
        text = _prefer_git_status_short(text)
    if cmd.startswith("git log") and ("--oneline" not in cmd) and ("-n" not in cmd) and ("-" not in cmd[7:].split()):
        text = _prefer_git_log_oneline(text)

    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        if _PROGRESS_RE.search(line):
            continue
        if _PASSING_TEST_RE.match(line):
            continue
        if _VERBOSE_LOG_RE.match(line):
            continue
        if cmd.startswith("git") and _GIT_NOISE_RE.match(line):
            continue
        kept.append(line)

    # Drop long runs of blank lines.
    compact: list[str] = []
    blank_run = 0
    for line in kept:
        if not line.strip():
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        compact.append(line)

    result = "\n".join(compact).strip()
    if len(result) <= max_chars:
        return result
    head = max_chars // 2
    tail = max_chars - head - 40
    return (
        result[:head]
        + "\n… [filtered + truncated for LLM context] …\n"
        + result[-max(0, tail) :]
    )


def _prefer_git_status_short(text: str) -> str:
    """Collapse verbose git status into a short path list when possible."""
    paths: list[str] = []
    for line in text.splitlines():
        m = re.match(
            r"^\s*(?:modified:|new file:|deleted:|renamed:)\s+(.+)$",
            line,
            re.I,
        )
        if m:
            paths.append(m.group(1).strip())
            continue
        m2 = re.match(r"^\s*[\sMADRCU?]{1,2}\s+(.+)$", line)
        if m2 and not line.strip().startswith("On branch"):
            paths.append(m2.group(1).strip())
    if not paths:
        return text
    unique = list(dict.fromkeys(paths))
    return "git status (filtered):\n" + "\n".join(f"  {p}" for p in unique[:200])


def _prefer_git_log_oneline(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 20:
        return text
    # Keep first 15 commit headers if they look like full log format.
    headers = [ln for ln in lines if ln.startswith("commit ")][:15]
    if not headers:
        return "\n".join(lines[:20])
    return "git log (filtered heads):\n" + "\n".join(headers)


def summarize_diff_stat(stat_text: str, *, max_files: int = 40) -> str:
    """Keep ``git diff --stat`` output bounded for model context."""
    lines = [ln for ln in (stat_text or "").splitlines() if ln.strip()]
    if len(lines) <= max_files + 2:
        return "\n".join(lines)
    return "\n".join(lines[:max_files] + ["…", lines[-1]])
