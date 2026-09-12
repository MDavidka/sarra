"""Deep Focus — High-density Project Memory and Context System for Syte AI.

Deep Focus indexes the project workspace (framework, tech stack, architecture,
key files, exports, database schemas, and custom persistent memory notes).
This eliminates high-token overhead by providing the AI agent with instant,
rich codebase understanding without needing to repeatedly list or read files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from syte.database import get_project

logger = logging.getLogger("syte.ai.deep_focus")


def scan_workspace_framework_and_stack(ws_dir: Path) -> Dict[str, Any]:
    """Analyze workspace root files to detect framework, stack, and dependencies."""
    stack_info: Dict[str, Any] = {
        "framework": "Static / Generic",
        "runtime": "Unknown",
        "language": "Unknown",
        "ui_libraries": [],
        "backend_libraries": [],
        "database": "None detected",
        "build_tool": "None",
        "package_manager": "None",
    }

    if not ws_dir.exists() or not ws_dir.is_dir():
        return stack_info

    pkg_json_path = ws_dir / "package.json"
    pyproject_path = ws_dir / "pyproject.toml"
    req_path = ws_dir / "requirements.txt"
    go_mod_path = ws_dir / "go.mod"
    cargo_path = ws_dir / "Cargo.toml"

    # Node / JS / TS ecosystem
    if pkg_json_path.exists():
        stack_info["runtime"] = "Node.js / JavaScript"
        stack_info["language"] = "TypeScript" if (ws_dir / "tsconfig.json").exists() else "JavaScript"
        try:
            pkg = json.loads(pkg_json_path.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

            if "next" in deps:
                stack_info["framework"] = f"Next.js {deps.get('next', '')} (App Router)" if (ws_dir / "app").exists() or (ws_dir / "src" / "app").exists() else f"Next.js {deps.get('next', '')}"
            elif "react" in deps and "vite" in deps:
                stack_info["framework"] = "React + Vite"
            elif "react" in deps:
                stack_info["framework"] = "React"
            elif "vue" in deps or "nuxt" in deps:
                stack_info["framework"] = "Nuxt / Vue"
            elif "svelte" in deps or "@sveltejs/kit" in deps:
                stack_info["framework"] = "SvelteKit"
            elif "astro" in deps:
                stack_info["framework"] = "Astro"
            elif "express" in deps:
                stack_info["framework"] = "Express.js (Node Backend)"
            elif "fastify" in deps:
                stack_info["framework"] = "Fastify (Node Backend)"

            # UI Libraries
            for ui_lib in ["tailwindcss", "lucide-react", "lucide-vue-next", "@radix-ui", "shadcn-ui", "@chakra-ui/react", "@mui/material", "framer-motion"]:
                if any(k.startswith(ui_lib) or k == ui_lib for k in deps):
                    stack_info["ui_libraries"].append(ui_lib)

            # DB / ORM
            for db_lib in ["prisma", "@prisma/client", "drizzle-orm", "mongoose", "pg", "better-sqlite3", "mysql2"]:
                if db_lib in deps:
                    stack_info["database"] = db_lib
        except Exception:
            pass

    # Python ecosystem
    elif pyproject_path.exists() or req_path.exists() or (ws_dir / "main.py").exists() or (ws_dir / "app.py").exists():
        stack_info["runtime"] = "Python 3"
        stack_info["language"] = "Python"
        raw_text = ""
        if pyproject_path.exists():
            try: raw_text += pyproject_path.read_text(encoding="utf-8") + "\n"
            except Exception: pass
        if req_path.exists():
            try: raw_text += req_path.read_text(encoding="utf-8") + "\n"
            except Exception: pass

        lower_req = raw_text.lower()
        if "fastapi" in lower_req:
            stack_info["framework"] = "FastAPI (Python Backend)"
        elif "flask" in lower_req:
            stack_info["framework"] = "Flask (Python Backend)"
        elif "django" in lower_req:
            stack_info["framework"] = "Django (Python Backend)"
        else:
            stack_info["framework"] = "Python App"

        if "sqlalchemy" in lower_req or "aiosqlite" in lower_req or "sqlite3" in lower_req:
            stack_info["database"] = "SQLite / SQLAlchemy"
        elif "psycopg" in lower_req or "asyncpg" in lower_req:
            stack_info["database"] = "PostgreSQL"
        elif "pymongo" in lower_req or "motor" in lower_req:
            stack_info["database"] = "MongoDB"

    elif go_mod_path.exists():
        stack_info["runtime"] = "Go Runtime"
        stack_info["language"] = "Go"
        stack_info["framework"] = "Go Service"
    elif cargo_path.exists():
        stack_info["runtime"] = "Rust Runtime"
        stack_info["language"] = "Rust"
        stack_info["framework"] = "Rust Service"
    elif (ws_dir / "index.html").exists():
        stack_info["runtime"] = "Static Web"
        stack_info["language"] = "HTML / CSS / JS"
        stack_info["framework"] = "Static HTML/JS Website"

    return stack_info


def scan_workspace_architecture(ws_dir: Path) -> Dict[str, Any]:
    """Build a compact architectural directory and file index."""
    if not ws_dir.exists():
        return {"directories": [], "key_files": [], "total_indexed_files": 0}

    key_dirs = []
    key_files = []
    ignore_set = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".turbo"}

    # Top-level directories
    for child in sorted(ws_dir.iterdir()):
        if child.name in ignore_set or child.name.startswith("."):
            continue
        if child.is_dir():
            try:
                sub_count = len(list(child.iterdir()))
            except Exception:
                sub_count = 0
            key_dirs.append(f"{child.name}/ ({sub_count} items)")

    # Key entrypoint and config files
    for p in sorted(ws_dir.rglob("*")):
        if any(ign in p.parts for ign in ignore_set):
            continue
        if p.is_file():
            rel = str(p.relative_to(ws_dir))
            try:
                size = p.stat().st_size
            except Exception:
                size = 0
            is_priority = (
                rel in ("package.json", "pyproject.toml", "requirements.txt", "Dockerfile", "README.md", "tsconfig.json", "tailwind.config.js", "vite.config.ts")
                or any(p.name in ("main.py", "app.py", "server.js", "index.html", "layout.tsx", "page.tsx", "App.tsx", "index.ts", "index.js", "router.py") for _ in [1])
                or len(p.parts) <= 3
            )
            if is_priority and len(key_files) < 40:
                key_files.append({"path": rel, "size_bytes": size})

    return {
        "directories": key_dirs[:20],
        "key_files": key_files,
        "total_indexed_files": len(key_files),
    }


async def build_deep_focus_index(project_id: str, ws_dir: Optional[Path] = None, custom_memory: str = "") -> Dict[str, Any]:
    """Assemble the complete Deep Focus (Project Memory) model for a project."""
    now_iso = datetime.now(timezone.utc).isoformat()

    if project_id == "global":
        from syte.database import list_projects
        all_projs = await list_projects()
        return {
            "project_id": "global",
            "project_name": "Global Platform Host",
            "is_global": True,
            "framework_stack": {
                "framework": "Syte Cloud Multi-Tenant Host",
                "runtime": "Python FastAPI + Uvicorn + Caddy + Docker",
                "language": "Python / TypeScript",
            },
            "active_projects": [
                {"id": p.get("id"), "name": p.get("name"), "domain": p.get("domain"), "running": bool(p.get("running")), "port": p.get("port")}
                for p in all_projs[:15]
            ],
            "custom_memory": custom_memory,
            "updated_at": now_iso,
        }

    project = await get_project(project_id)
    if not project:
        return {"project_id": project_id, "error": "Project not found", "updated_at": now_iso}

    if not ws_dir:
        from syte.ai.tools import _get_project_workspace_dir
        ws_dir = _get_project_workspace_dir(project)

    stack = scan_workspace_framework_and_stack(ws_dir)
    arch = scan_workspace_architecture(ws_dir)

    deep_focus = {
        "project_id": project_id,
        "project_name": project.get("name", project_id),
        "domain": project.get("domain") or "Unassigned",
        "branch": project.get("branch") or "main",
        "port": project.get("port"),
        "running": bool(project.get("running")),
        "git_url": project.get("git_url") or "None",
        "workspace_dir": str(ws_dir),
        "framework_stack": stack,
        "architecture": arch,
        "custom_memory": custom_memory,
        "updated_at": now_iso,
    }
    return deep_focus


def format_deep_focus_for_prompt(deep_focus: Dict[str, Any]) -> str:
    """Convert Deep Focus dictionary into a concise, token-efficient prompt context."""
    if not deep_focus or deep_focus.get("error"):
        return ""

    if deep_focus.get("is_global"):
        projs = deep_focus.get("active_projects") or []
        summary_lines = [f"  • {p['name']} (ID: {p['id']}, Domain: {p['domain']}, Port: {p['port']}, Running: {p['running']})" for p in projs]
        return (
            f"\n--- DEEP FOCUS: GLOBAL PLATFORM MEMORY ---\n"
            f"Architecture: Multi-Tenant Syte Platform\n"
            f"Hosted Services ({len(projs)} active):\n"
            + ("\n".join(summary_lines) if summary_lines else "  • No projects registered yet.")
            + (f"\nPersistent Notes: {deep_focus.get('custom_memory')}\n" if deep_focus.get("custom_memory") else "")
            + f"\n-------------------------------------------\n"
        )

    stack = deep_focus.get("framework_stack") or {}
    arch = deep_focus.get("architecture") or {}
    key_files = arch.get("key_files") or []
    dirs = arch.get("directories") or []
    custom_notes = (deep_focus.get("custom_memory") or "").strip()

    lines = [
        "--- DEEP FOCUS: PROJECT MEMORY (High-Density Context Index) ---",
        f"Project: {deep_focus.get('project_name')} (ID: {deep_focus.get('project_id')})",
        f"Framework: {stack.get('framework')} | Language: {stack.get('language')} | Runtime: {stack.get('runtime')}",
    ]
    if stack.get("ui_libraries"):
        lines.append(f"UI Libraries: {', '.join(stack.get('ui_libraries'))}")
    if stack.get("database") and stack.get("database") != "None detected":
        lines.append(f"Database / ORM: {stack.get('database')}")

    if dirs:
        lines.append(f"Key Directories: {', '.join(dirs)}")

    if key_files:
        files_summary = ", ".join(f["path"] for f in key_files[:25])
        lines.append(f"Indexed Core Files: {files_summary}")

    if custom_notes:
        lines.append(f"Persistent Project Memory Notes:\n{custom_notes}")

    lines.append("Directive: Deep Focus contains the project architecture, dependencies, and layout. Use Deep Focus directly instead of wasting tool calls or high token costs repeatedly reading boilerplate or framework files.")
    lines.append("------------------------------------------------------------------")
    return "\n" + "\n".join(lines) + "\n"
