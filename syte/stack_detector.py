"""Deterministic, secret-safe project stack detection for deployment preflight."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from syte.workspace import ensure_workspace

MAX_FILES = 160
ENV_NAMES = {".env", ".env.local", ".env.production", ".env.example", ".env.development"}


def _read(path: Path, limit: int = 120_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except (OSError, UnicodeError):
        return ""


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read(path))
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _env_keys(path: Path) -> list[str]:
    keys: set[str] = set()
    for line in _read(path, 80_000).splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            keys.add(match.group(1))
    return sorted(keys)


def detect_stack(project_id: str) -> dict[str, Any]:
    root = ensure_workspace(project_id) / "app"
    result: dict[str, Any] = {
        "framework": None, "language": None, "runtime": None, "package_manager": None,
        "deploy_type": "shell", "build_command": None, "start_command": None,
        "dockerfile_path": None, "monorepo": False, "env_keys": [], "warnings": [], "signals": [],
    }
    if not root.exists():
        result["warnings"] = ["Workspace is empty. Add a repository or files before deploying."]
        return result

    try:
        files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts][:MAX_FILES]
    except OSError:
        files = []
    names = {p.name for p in files}
    result["signals"] = sorted(names & {"package.json", "pyproject.toml", "requirements.txt", "Dockerfile", "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb"})

    package = _json(root / "package.json") if "package.json" in names else {}
    deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})} if package else {}
    scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
    if package:
        result["language"] = "TypeScript" if any(p.suffix in {".ts", ".tsx"} for p in files) else "JavaScript"
        result["runtime"] = "Node.js"
        if "pnpm-lock.yaml" in names: result["package_manager"] = "pnpm"
        elif "yarn.lock" in names: result["package_manager"] = "yarn"
        elif "bun.lockb" in names: result["package_manager"] = "bun"
        else: result["package_manager"] = "npm"
        frameworks = [("Next.js", "next"), ("Vite", "vite"), ("Remix", "@remix-run/react"), ("Astro", "astro"), ("Nuxt", "nuxt"), ("SvelteKit", "@sveltejs/kit")]
        for label, dep in frameworks:
            if dep in deps:
                result["framework"] = label; break
        result["build_command"] = scripts.get("build") or ("next build" if result["framework"] == "Next.js" else None)
        result["start_command"] = scripts.get("start") or ("next start" if result["framework"] == "Next.js" else None)
        if isinstance(package.get("workspaces"), (list, dict)): result["monorepo"] = True
    elif "pyproject.toml" in names or "requirements.txt" in names:
        result["language"] = "Python"; result["runtime"] = "Python"
        result["package_manager"] = "uv" if "uv.lock" in names else "pip"
        text = _read(root / "pyproject.toml")
        if "fastapi" in text.lower(): result["framework"] = "FastAPI"
        elif "django" in text.lower(): result["framework"] = "Django"
        elif "flask" in text.lower(): result["framework"] = "Flask"

    docker = next((p for p in files if p.name == "Dockerfile"), None)
    if docker:
        result["deploy_type"] = "docker"; result["dockerfile_path"] = str(docker.relative_to(root)); result["signals"].append("Dockerfile")
    elif not result["start_command"]:
        result["warnings"].append("No Dockerfile or start command was detected.")
    if not result["framework"] and package:
        result["warnings"].append("Framework could not be identified; review commands before deploying.")

    env_files = sorted(p for p in files if p.name in ENV_NAMES)
    keys = sorted({key for path in env_files for key in _env_keys(path)})
    configured = set(_env_keys(root / ".env")) if (root / ".env").exists() else set()
    result["env_keys"] = [{"name": key, "configured": key in configured, "source": "workspace"} for key in keys]
    return result


def preflight(project_id: str, project: dict[str, Any], start_command: str | None = None) -> dict[str, Any]:
    detection = detect_stack(project_id)
    if start_command:
        detection["start_command"] = start_command
        detection["deploy_type"] = "shell"
        detection["warnings"] = [w for w in detection["warnings"] if "start command" not in w.lower()]
    blocking = bool(not project.get("git_url") and not detection.get("start_command") and detection.get("deploy_type") != "docker")
    return {"ok": not blocking, "blocking": blocking, "project": {"uuid": project.get("id"), "name": project.get("name"), "branch": project.get("branch", "main"), "git_url": project.get("git_url")}, "detection": detection}
  
