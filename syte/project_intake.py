"""Safe source intake and Vercel-style deployment analysis for Syte projects.

The module intentionally separates source *analysis* from deployment.  An operator
can inspect the detected language, build commands and requested environment keys
before a generated Dockerfile is written or a production deploy is issued.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import zipfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from syte.platform.build_packs import (
    BuildPackError,
    detect_compose_file,
    detect_dockerfile,
    detect_language,
    detect_node_framework,
    detect_node_package_manager,
    detect_python_manager,
    resolve_build_plan,
    scan_context,
)
from syte.platform.types import BuildContext, BuildPack
from syte.workspace import ensure_workspace

MAX_ARCHIVE_BYTES = 75 * 1024 * 1024
MAX_ARCHIVE_FILES = 12_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 450 * 1024 * 1024
MAX_ENV_SCAN_FILES = 96
MAX_ENV_SCAN_BYTES = 1_800_000

_SKIP_ENV_DIRS = frozenset({
    ".git", "node_modules", ".next", ".nuxt", "dist", "build", "target", "vendor",
    "__pycache__", ".venv", "venv", ".cache", "coverage", ".terraform",
})
_TEXT_SUFFIXES = frozenset({
    ".env", ".example", ".sample", ".template", ".txt", ".md", ".json", ".toml",
    ".yaml", ".yml", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py", ".php",
    ".rb", ".go", ".rs", ".java", ".cs", ".ex", ".exs", ".sh", ".html",
})
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_ENV_PATTERNS = (
    re.compile(r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]{1,127})"),
    re.compile(r"(?:os\.getenv|os\.environ\.get|os\.environ\[|System\.getenv|ENV\.fetch|ENV\[|env)\s*\(?(?:\[)?[\"']([A-Z][A-Z0-9_]{1,127})"),
)
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,127})\s*=")
_ENV_BUILTINS = frozenset({"PORT", "HOST", "HOSTNAME", "NODE_ENV", "PYTHONPATH", "PATH"})


def _safe_relative(name: str) -> PurePosixPath:
    """Validate an archive member path before extraction."""
    if not name or "\x00" in name or "\\" in name:
        raise ValueError("Archive contains an unsafe file path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Archive contains a path outside the project")
    return path


def _archive_root_prefix(members: list[zipfile.ZipInfo]) -> str:
    """Drop one conventional top-level archive directory, if all files share it."""
    roots: set[str] = set()
    for info in members:
        if info.is_dir():
            continue
        parts = _safe_relative(info.filename).parts
        if parts:
            roots.add(parts[0])
    if len(roots) != 1:
        return ""
    root = next(iter(roots))
    for info in members:
        if info.is_dir():
            continue
        parts = _safe_relative(info.filename).parts
        if len(parts) == 1:
            return ""
    return root


def extract_zip_to_project(project_id: str, archive_path: Path) -> dict[str, int]:
    """Atomically replace a project source directory with a validated ZIP archive.

    This rejects zip-slip paths, symbolic links, zip bombs and excessive member
    counts.  Content is first expanded into a sibling staging directory and only
    moved into ``app/`` after the complete archive passes validation.
    """
    if not archive_path.is_file():
        raise ValueError("Uploaded archive was not found")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP archive exceeds the 75 MB upload limit")

    workspace = ensure_workspace(project_id)
    app_dir = workspace / "app"
    staging = workspace / ".source-import-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise ValueError("ZIP archive contains too many files")
            total_uncompressed = sum(info.file_size for info in infos)
            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("ZIP archive expands beyond the 450 MB safety limit")
            prefix = _archive_root_prefix(infos)
            extracted = 0
            written = 0
            for info in infos:
                member = _safe_relative(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ValueError("ZIP archives containing symbolic links are not supported")
                parts = member.parts
                if prefix and parts and parts[0] == prefix:
                    parts = parts[1:]
                if not parts or parts[0] == "__MACOSX":
                    continue
                target = staging.joinpath(*parts).resolve()
                if staging.resolve() not in target.parents and target != staging.resolve():
                    raise ValueError("Archive path escaped the destination")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                extracted += 1
                written += info.file_size

        if not extracted:
            raise ValueError("ZIP archive did not contain any source files")
        if app_dir.exists():
            shutil.rmtree(app_dir)
        staging.replace(app_dir)
        return {"files": extracted, "bytes": written}
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _text_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _SKIP_ENV_DIRS]
        for name in files:
            path = Path(current) / name
            if path.name.startswith(".env") or path.suffix.lower() in _TEXT_SUFFIXES:
                candidates.append(path)
                if len(candidates) >= MAX_ENV_SCAN_FILES:
                    return candidates
    return candidates


def discover_environment_keys(root: Path) -> list[dict[str, str]]:
    """Return variable names referenced by source files, never their values."""
    found: dict[str, set[str]] = {}
    remaining = MAX_ENV_SCAN_BYTES
    for path in _text_files(root):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > remaining or size > 200_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        remaining -= len(text.encode("utf-8", errors="ignore"))
        source = path.relative_to(root).as_posix()
        for line in text.splitlines():
            env_match = _ENV_LINE.match(line) if path.name.startswith(".env") else None
            if env_match:
                key = env_match.group(1)
                if key not in _ENV_BUILTINS:
                    found.setdefault(key, set()).add(source)
        for pattern in _ENV_PATTERNS:
            for match in pattern.finditer(text):
                key = match.group(1)
                if key not in _ENV_BUILTINS and _ENV_KEY.match(key):
                    found.setdefault(key, set()).add(source)
        if remaining <= 0:
            break
    return [
        {"key": key, "source": ", ".join(sorted(sources)[:3])}
        for key, sources in sorted(found.items())
    ]


def _framework_for(language: str, files: frozenset[str], package_json: dict[str, object]) -> str:
    if language == "node":
        return detect_node_framework(BuildContext(files=files, package_json=package_json))[0]
    if language == "python":
        lowered = " ".join(sorted(files)).lower()
        if "manage.py" in files:
            return "django"
        if "fastapi" in lowered:
            return "fastapi"
        if "flask" in lowered:
            return "flask"
        if "streamlit" in lowered:
            return "streamlit"
        return "python"
    markers = {
        "go": ("gin" if any("gin" in path.lower() for path in files) else "go"),
        "rust": "rust",
        "php": ("laravel" if "artisan" in files else "php"),
        "ruby": ("rails" if "config/application.rb" in files else "ruby"),
        "java": ("spring" if any("spring" in path.lower() for path in files) else "java"),
        "elixir": ("phoenix" if "mix.exs" in files else "elixir"),
        "dotnet": "dotnet",
        "bun": "bun",
        "deno": "deno",
        "static": "static",
    }
    return markers.get(language, language)


def _base_directory(root: Path, requested: str = "/") -> str:
    candidate = (requested or "/").strip().strip("/")
    if not candidate:
        return "/"
    target = (root / candidate).resolve()
    if root.resolve() not in target.parents or not target.is_dir():
        raise ValueError("Base directory must be a directory inside the imported source")
    return candidate


def analyze_project_source(project_id: str, *, source_type: str, base_directory: str = "/") -> dict[str, Any]:
    """Build a deployment-safe preview for an imported project source tree."""
    root = ensure_workspace(project_id) / "app"
    base = _base_directory(root, base_directory)
    files, package_json = scan_context(root, base_directory=base)
    if not files:
        raise ValueError("No source files were found. Import a Git repository or ZIP archive first.")

    context = BuildContext(files=files, package_json=package_json, base_directory=base)
    language = detect_language(context)
    framework = _framework_for(language, files, package_json)
    dockerfile = detect_dockerfile(root, base_directory=base)
    compose = detect_compose_file(root, base_directory=base)
    if dockerfile:
        build_pack = BuildPack.DOCKERFILE
        relative_dockerfile = dockerfile.relative_to(root / base if base != "/" else root).as_posix()
        context = BuildContext(files=files, package_json={**package_json, "dockerfile_location": relative_dockerfile}, base_directory=base)
    elif compose:
        # The current single-project runtime cannot safely orchestrate a multi-container
        # compose stack; preserve the signal and require a Dockerfile override.
        build_pack = BuildPack.DOCKERCOMPOSE
    else:
        build_pack = BuildPack.RAILPACK

    try:
        plan = resolve_build_plan(build_pack, context)
    except BuildPackError as exc:
        return {
            "source_type": source_type,
            "base_directory": base,
            "language": language,
            "framework": framework,
            "status": "needs_configuration",
            "error": str(exc),
            "files_detected": len(files),
            "environment_suggestions": discover_environment_keys(root / base if base != "/" else root),
        }

    actual_pack = plan.build_pack.value
    warnings = list(plan.notes)
    if compose:
        warnings.append("A Compose file was detected. Choose an existing Dockerfile or provide a single-container build configuration before deploying this project.")
    if dockerfile:
        warnings.append(f"Using repository Dockerfile: {relative_dockerfile}.")
    if language == "node":
        warnings.append(f"Detected package manager: {detect_node_package_manager(context)}.")
    if language == "python":
        warnings.append(f"Detected Python dependency manager: {detect_python_manager(context)}.")

    return {
        "source_type": source_type,
        "base_directory": base,
        "language": language,
        "framework": framework,
        "status": "ready" if not compose else "needs_configuration",
        "build_pack": actual_pack,
        "files_detected": len(files),
        "package_manager": detect_node_package_manager(context) if language == "node" else "",
        "install_command": plan.install_command,
        "build_command": plan.build_command,
        "start_command": plan.start_command,
        "publish_directory": context.publish_directory,
        "dockerfile_path": plan.dockerfile_path,
        "exposed_port": plan.exposed_port,
        "generated_dockerfile": plan.dockerfile if plan.generated else "",
        "environment_suggestions": discover_environment_keys(root / base if base != "/" else root),
        "warnings": warnings,
    }


def apply_detected_build_plan(project_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
    """Write only a generated Dockerfile; user-provided Dockerfiles are never replaced."""
    if analysis.get("status") != "ready":
        raise ValueError("Resolve the source configuration before deploying")
    root = ensure_workspace(project_id) / "app"
    base = str(analysis.get("base_directory") or "/").strip("/")
    app_root = root / base if base else root
    generated = str(analysis.get("generated_dockerfile") or "")
    dockerfile = app_root / "Dockerfile"
    wrote = False
    if generated and not dockerfile.exists():
        dockerfile.write_text(generated, encoding="utf-8")
        wrote = True
    return {"generated_dockerfile_written": wrote, "dockerfile_path": str(dockerfile.relative_to(root))}


def analysis_metadata(analysis: dict[str, Any]) -> dict[str, str]:
    """Project-safe fields persisted in env_vars for existing schema compatibility."""
    return {
        "SYTE_SOURCE_TYPE": str(analysis.get("source_type") or ""),
        "SYTE_DETECTED_LANGUAGE": str(analysis.get("language") or ""),
        "SYTE_DETECTED_FRAMEWORK": str(analysis.get("framework") or ""),
        "SYTE_BUILD_PACK": str(analysis.get("build_pack") or ""),
        "SYTE_BASE_DIRECTORY": str(analysis.get("base_directory") or "/"),
        "SYTE_INSTALL_COMMAND": str(analysis.get("install_command") or ""),
        "SYTE_BUILD_COMMAND": str(analysis.get("build_command") or ""),
        "SYTE_START_COMMAND": str(analysis.get("start_command") or ""),
        "SYTE_PUBLISH_DIRECTORY": str(analysis.get("publish_directory") or ""),
    }


__all__ = [
    "MAX_ARCHIVE_BYTES",
    "analysis_metadata",
    "analyze_project_source",
    "apply_detected_build_plan",
    "discover_environment_keys",
    "extract_zip_to_project",
]
