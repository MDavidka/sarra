#!/usr/bin/env python3
"""Validate source-level invariants that prevent avoidable merge and edit conflicts."""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (ROOT / "syte", ROOT / "scripts", ROOT / "tests", ROOT / "systemd", ROOT / "docs")
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".py", ".service", ".sh", ".toml", ".webmanifest"}
ROOT_TEXT_FILES = (ROOT / ".gitignore", ROOT / "README.md", ROOT / "pyproject.toml", ROOT / "requirements.txt")
MERGE_MARKER = re.compile(r"^(?:<<<<<<<|=======|>>>>>>>)", re.MULTILINE)


def text_sources() -> list[Path]:
    paths = [
        path
        for source_root in SOURCE_ROOTS
        if source_root.exists()
        for path in source_root.rglob("*")
        if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts and "vendor" not in path.parts
    ]
    paths.extend(path for path in ROOT_TEXT_FILES if path.is_file())
    return sorted(set(paths))


def python_sources() -> list[Path]:
    return [path for path in text_sources() if path.suffix == ".py"]


def duplicate_definitions(tree: ast.AST, source: Path) -> list[str]:
    """Return duplicate names declared in a single lexical scope.

    Redefining a function or class in the same scope silently replaces the
    earlier declaration in Python. Treating that as a build failure keeps
    future edits and conflict resolutions from shipping an accidental shadow.
    """

    duplicates: list[str] = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        names = [
            item.name
            for item in body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        for name, count in Counter(names).items():
            if count > 1:
                duplicates.append(f"{source.relative_to(ROOT)}: duplicate definition '{name}'")
    return duplicates


def main() -> int:
    errors: list[str] = []
    for source in text_sources():
        content = source.read_text(encoding="utf-8")
        if MERGE_MARKER.search(content):
            errors.append(f"{source.relative_to(ROOT)}: unresolved merge marker")

    for source in python_sources():
        content = source.read_text(encoding="utf-8")
        try:
            tree = ast.parse(content, filename=str(source))
        except SyntaxError as exc:
            errors.append(f"{source.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
            continue
        errors.extend(duplicate_definitions(tree, source))

    if errors:
        print("Code-health validation failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1

    print(f"Code-health validation succeeded for {len(text_sources())} text source files and {len(python_sources())} Python source files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
