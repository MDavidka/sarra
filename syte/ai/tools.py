"""Tool definitions and executors for the Syte AI Agent.

Gives the LLM complete autonomous access to the Syte platform, filesystem, terminal, deployments, logs, and infrastructure.
"""

import ast
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional

from syte.ai.skills import discover_skills_catalog, get_skill_content, list_available_skills
from syte.config import settings
from syte.database import get_project, list_project_router_logs
from syte.deployment import issue_deploy, start_service, stop_service
from syte.process_manager import get_logs, is_running, start_project, stop_project
from syte.system_stats import get_system_stats


def get_ai_tools_schema() -> List[Dict[str, Any]]:
    """Return JSON schemas for all Syte tools formatted for OpenAI / OpenCode tool calling."""
    return [
        # 1. Deployments & Observability
        {
            "type": "function",
            "function": {
                "name": "syte_create_deployment",
                "description": "Trigger a build and zero-downtime deployment for this project on the server.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "branch": {
                            "type": "string",
                            "description": "Git branch to build and deploy (defaults to project's active branch).",
                        },
                        "start_command": {
                            "type": "string",
                            "description": "Custom start command (e.g. 'npm start', 'python3 main.py').",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_get_deployment_logs",
                "description": "Retrieve and diagnose stdout/stderr logs from the current or latest deployment process.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of recent log lines to retrieve (default 60).",
                        },
                        "filter_keyword": {
                            "type": "string",
                            "description": "Optional keyword or regex to filter log lines (e.g. 'error', 'failed', 'warn').",
                        },
                        "log_level": {
                            "type": "string",
                            "enum": ["all", "errors", "warnings"],
                            "description": "Filter severity level (default 'all').",
                        },
                        "diagnose": {
                            "type": "boolean",
                            "description": "Whether to run automatic error root cause diagnosis and fix suggestions (default true).",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_get_router_logs",
                "description": "Retrieve internal HTTP router access and error logs (methods, status codes, latency, paths).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "search": {
                            "type": "string",
                            "description": "Filter logs by path, host, or keyword.",
                        },
                        "status_code": {
                            "type": "string",
                            "description": "Filter by status code (e.g. '200', '404', '500', '525').",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max entries to return (default 40).",
                        },
                    },
                },
            },
        },
        # 2. Workspace File System Operations (Read, Write, Edit, Move, Delete, List, Search, Terminal)
        {
            "type": "function",
            "function": {
                "name": "syte_read_file",
                "description": "Read the contents of a file inside the project workspace directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path to the file from the workspace root (e.g. 'package.json', 'src/App.tsx').",
                        }
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_write_file",
                "description": "Write or overwrite a file with full content inside the project workspace directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path to the file.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete text content to write into the file.",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_edit_file",
                "description": "Replace a specific substring or code block in a file inside the project workspace directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path to the file.",
                        },
                        "old_text": {
                            "type": "string",
                            "description": "Exact text substring to replace.",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "Replacement text.",
                        },
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_move_file",
                "description": "Move or rename a file or directory inside the project workspace directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "description": "Relative source path of file or directory to move.",
                        },
                        "destination_path": {
                            "type": "string",
                            "description": "Relative destination path to move to.",
                        },
                    },
                    "required": ["source_path", "destination_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_delete_file",
                "description": "Delete a file or directory inside the project workspace directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path to the file or directory to delete.",
                        }
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_list_files",
                "description": "List files and subdirectories in the project workspace (see directory tree).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Relative directory path (defaults to root '').",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum directory depth to traverse (default 3).",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_search_files",
                "description": "Search text patterns or code occurrences across all files in the project workspace (grep).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search keyword or pattern.",
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": "Optional glob filter (e.g. '*.ts', '*.js', '*.py'). Default '*'.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_run_command",
                "description": "Execute a shell / terminal command inside the project workspace directory on the host VM.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Bash command to execute (e.g. 'npm install', 'npm test', 'git status', 'ls -la').",
                        }
                    },
                    "required": ["command"],
                },
            },
        },
        # 3. Logged-in Git Account & Repository Operations
        {
            "type": "function",
            "function": {
                "name": "syte_github_account_info",
                "description": "Get details about the currently connected / logged-in GitHub account (username, permissions, and connection status).",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_github_list_repos",
                "description": "List repositories accessible by the logged-in GitHub account.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Filter repositories by name or keyword.",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_git_status",
                "description": "Get repository git status, active branch, uncommitted diffs, and recent commits in workspace.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_git_commit",
                "description": "Stage files and create a git commit in the project workspace repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Git commit message describing changes.",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of relative file paths to stage (omit or pass ['*'] to stage all).",
                        },
                    },
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_git_push",
                "description": "Push local commits to the remote Git repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "branch": {
                            "type": "string",
                            "description": "Remote branch name to push (defaults to project's active branch).",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_git_pull",
                "description": "Pull latest updates from the remote Git repository into the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "branch": {
                            "type": "string",
                            "description": "Branch to pull (defaults to project's active branch).",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_git_create_branch",
                "description": "Create and switch to a new git branch in the project repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "branch_name": {
                            "type": "string",
                            "description": "Name of the new branch to create and checkout.",
                        }
                    },
                    "required": ["branch_name"],
                },
            },
        },
        # 4. Performance, Environment & Domains
        {
            "type": "function",
            "function": {
                "name": "syte_get_performance",
                "description": "Retrieve current CPU, RAM, and Disk resource telemetry for the host node and project.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_get_environment",
                "description": "Get configured runtime environment variables for this project.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_set_environment",
                "description": "Set or update an environment variable for this project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Environment variable key name (e.g. 'PORT', 'DATABASE_URL').",
                        },
                        "value": {
                            "type": "string",
                            "description": "Variable value.",
                        },
                    },
                    "required": ["key", "value"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_manage_domains",
                "description": "Inspect or attach custom domains and subdomains for this project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "add"],
                            "description": "Action to perform: 'list' or 'add'.",
                        },
                        "domain": {
                            "type": "string",
                            "description": "Domain name to connect (required for 'add').",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        # 5. Live Preview Server Operations
        {
            "type": "function",
            "function": {
                "name": "syte_start_preview",
                "description": "Start a live hot-reloading development preview server on the VM for this workspace and return the live URL.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_stop_preview",
                "description": "Stop the running development preview server for this project.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_get_preview_status",
                "description": "Check if the live preview development server is running and get its active URL.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        # 6. Planning & Task Orchestration
        {
            "type": "function",
            "function": {
                "name": "syte_create_plan",
                "description": "Create an implementation plan before executing code modifications. Outlines steps, architecture, and validation checks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short title of the plan (e.g. 'Build Landing Page with shadcn & Inter').",
                        },
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "failed"]},
                                    "description": {"type": "string"},
                                },
                                "required": ["id", "title"],
                            },
                            "description": "Ordered list of planning steps.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "Brief architectural explanation and approach rationale.",
                        },
                    },
                    "required": ["title", "steps"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_update_plan_step",
                "description": "Update the execution status of a specific step in the current implementation plan.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "step_id": {
                            "type": "string",
                            "description": "The ID of the step to update.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "failed"],
                            "description": "New status for the step.",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Optional notes or outcome summary for this step.",
                        },
                    },
                    "required": ["step_id", "status"],
                },
            },
        },
        # 7. Modular Skills
        {
            "type": "function",
            "function": {
                "name": "syte_list_skills",
                "description": "List all available design systems, architecture guides, and domain skills in the library.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_load_skill",
                "description": "Load comprehensive instructions, best practices, and blueprints for a specific skill (e.g. 'website-create', 'shadcn-ui', 'integration', 'providers', 'cloud-code') or capability.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name or alias of the skill or capability to load (e.g. 'website-create', 'Design & Colors', 'get_color_palette', 'generate_auth_middleware').",
                        }
                    },
                    "required": ["skill_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_discover_skills",
                "description": "Search, browse, and discover modular skill capabilities across categories: 'Design & Colors', 'Components & UI', 'App & Routing', 'Login & Auth', 'Server & Backend', 'Integrations & Database', 'Optimization & Build'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Optional category filter (e.g. 'Design & Colors', 'Components & UI', 'Login & Auth', 'Server & Backend', 'Integrations & Database', 'Optimization & Build').",
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional keyword search for capability names or functionality.",
                        },
                        "detailed": {
                            "type": "boolean",
                            "description": "Whether to return full detailed tokens and specs for matched capabilities.",
                        },
                    },
                },
            },
        },
        # 8. Interactive User Clarifications & Secure Environment Vault
        {
            "type": "function",
            "function": {
                "name": "syte_ask_question",
                "description": "Ask the user a clarifying design or requirement question with multiple choice options and custom write-in.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question to ask the user (e.g. 'What color scheme do you prefer?').",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Suggested choices for the user (e.g. ['Clean Light Theme (Inter)', 'Modern Dark (Zinc)', 'Emerald Minimal']).",
                        },
                        "allow_custom": {
                            "type": "boolean",
                            "description": "Whether the user can write in a custom answer (default true).",
                        },
                    },
                    "required": ["question"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_ask_env_var",
                "description": "Request a secret or API key from the user. Securely saves the key directly to server .env without exposing the raw secret to LLM context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Environment variable key name (e.g. 'STRIPE_SECRET_KEY', 'DATABASE_URL', 'RESEND_API_KEY').",
                        },
                        "description": {
                            "type": "string",
                            "description": "Explanation of what this key is needed for.",
                        },
                        "hint": {
                            "type": "string",
                            "description": "Format hint (e.g. 'Starts with sk_test_... or sk_live_...').",
                        },
                    },
                    "required": ["key"],
                },
            },
        },
        # 9. Internal Security & Code Linting Scanner
        {
            "type": "function",
            "function": {
                "name": "syte_security_lint_scan",
                "description": "Run internal static analysis, syntax verification (Python/JS/JSON), and security vulnerability scanning on the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of relative file paths to scan. If omitted, scans all modified or critical files in workspace.",
                        }
                    },
                },
            },
        },
        # 10. Workspace Structure & Line Reading
        {
            "type": "function",
            "function": {
                "name": "syte_read_file_lines",
                "description": "Read a specific line range from a file with line numbers (efficient for large source files).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Starting line number (1-indexed).",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Ending line number (inclusive).",
                        },
                    },
                    "required": ["path", "start_line", "end_line"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "syte_get_workspace_tree",
                "description": "Get hierarchical directory structure of the workspace with file types and sizes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_depth": {
                            "type": "integer",
                            "description": "Max directory depth (default 3).",
                        }
                    },
                },
            },
        },
    ]


def _get_project_workspace_dir(project: dict[str, Any]) -> Path:
    """Resolve project directory safely on disk."""
    pid = project["id"]
    p_name = project.get("name") or ""
    candidates = [
        settings.resolved_workspaces_dir / pid / "app",
        settings.resolved_workspaces_dir / pid,
        settings.resolved_workspaces_dir / p_name / "app",
        settings.resolved_workspaces_dir / p_name,
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    p = settings.resolved_workspaces_dir / pid / "app"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _diagnose_deployment_errors(logs: List[str]) -> Dict[str, Any]:
    """Diagnose build or deployment failures from log lines and suggest actionable fixes."""
    full_text = "\n".join(logs)
    diagnoses: List[Dict[str, str]] = []

    # 1. Missing Node.js / npm package
    missing_npm = re.findall(r"(?:Cannot find module|Module not found: Can't resolve)\s+['\"]([^'\"]+)['\"]", full_text, re.IGNORECASE)
    if missing_npm:
        pkg = missing_npm[0]
        diagnoses.append({
            "category": "Missing Dependency",
            "issue": f"Module '{pkg}' is imported but not installed in package.json or node_modules.",
            "suggestion": f"Run `syte_run_command` with `npm install {pkg}` or `pnpm add {pkg}`.",
        })

    # 2. TypeScript compilation failure
    ts_errors = re.findall(r"error TS\d+:\s*(.+)", full_text)
    if ts_errors:
        diagnoses.append({
            "category": "TypeScript Compilation Error",
            "issue": ts_errors[0],
            "suggestion": "Inspect the referenced file with `syte_read_file` and fix the type definition or missing export.",
        })

    # 3. Port conflict / EADDRINUSE
    if "EADDRINUSE" in full_text or "Address already in use" in full_text:
        diagnoses.append({
            "category": "Port Conflict",
            "issue": "The target TCP port is already in use by another process.",
            "suggestion": "Ensure the app binds dynamically to `process.env.PORT` or `os.environ.get('PORT')` provided by Syte.",
        })

    # 4. Python missing package
    missing_py = re.findall(r"ModuleNotFoundError:\s+No module named\s+['\"]([^'\"]+)['\"]", full_text)
    if missing_py:
        mod = missing_py[0]
        diagnoses.append({
            "category": "Python Dependency Missing",
            "issue": f"Python module '{mod}' is not installed.",
            "suggestion": f"Add '{mod}' to requirements.txt and run `syte_run_command` with `pip install {mod}`.",
        })

    # 5. Syntax Error
    syntax_err = re.findall(r"SyntaxError:\s*(.+)", full_text)
    if syntax_err:
        diagnoses.append({
            "category": "Syntax Error",
            "issue": syntax_err[0],
            "suggestion": "Run `syte_security_lint_scan` to pinpoint the invalid syntax and apply corrections.",
        })

    has_error = bool(diagnoses) or any(k in full_text.lower() for k in ["error:", "failed", "fatal:", "exception"])
    return {
        "has_error": has_error,
        "diagnoses": diagnoses,
        "summary": diagnoses[0]["issue"] if diagnoses else ("No critical errors detected in recent logs." if not has_error else "Deployment encountered errors. Inspect recent log output for details."),
    }


def _perform_security_lint_scan(ws_dir: Path, target_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run AST syntax verification and static security scanning on workspace files."""
    syntax_errors: List[Dict[str, Any]] = []
    security_warnings: List[Dict[str, Any]] = []
    scanned_count = 0

    SECRET_PATTERNS = [
        (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PRIVATE )?KEY-----"), "Hardcoded Private Key"),
        (re.compile(r"\b(?:sk_live_[0-9a-zA-Z]{24,}|ghp_[0-9a-zA-Z]{36}|AIzaSy[0-9a-zA-Z_-]{33})\b"), "Hardcoded Live API Secret Key"),
        (re.compile(r"(?:password|secret|api_key|token)\s*=\s*['\"][0-9a-zA-Z_\-!@#$%^&*]{16,}['\"]", re.IGNORECASE), "Plaintext Secret Literal"),
    ]

    DANGEROUS_PATTERNS = [
        (re.compile(r"\brm\s+-rf\s+(?:/|~|\$HOME|\.\./\.\.)"), "Destructive Shell Command"),
        (re.compile(r"\bchild_process\.exec\s*\(\s*`[^`]*\$\{"), "Possible Command Injection via Unsanitized Template Literal"),
    ]

    files_to_scan: List[Path] = []
    if target_paths:
        for tp in target_paths:
            fp = ws_dir / tp.lstrip("/\\")
            if fp.exists() and fp.is_file():
                files_to_scan.append(fp)
    else:
        ignored_dirs = {".git", "node_modules", ".venv", "__pycache__", ".next", "dist", "build"}
        for root, dirs, files in os.walk(ws_dir):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
            for f in files:
                if f.endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".html", ".env", ".yaml", ".yml")):
                    files_to_scan.append(Path(root) / f)
                    if len(files_to_scan) >= 150:
                        break
            if len(files_to_scan) >= 150:
                break

    for file_path in files_to_scan:
        rel_str = str(file_path.relative_to(ws_dir))
        scanned_count += 1
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # 1. Python AST Syntax Check
        if file_path.suffix == ".py":
            try:
                ast.parse(content, filename=rel_str)
            except SyntaxError as syn_err:
                syntax_errors.append({
                    "file": rel_str,
                    "line": syn_err.lineno,
                    "offset": syn_err.offset,
                    "error": str(syn_err.msg),
                    "type": "Python SyntaxError",
                })

        # 2. JSON Validation
        elif file_path.suffix == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as json_err:
                syntax_errors.append({
                    "file": rel_str,
                    "line": json_err.lineno,
                    "col": json_err.colno,
                    "error": json_err.msg,
                    "type": "JSON Decode Error",
                })

        # 3. Security Checks (unless file is a mock/sample .env.example)
        if not rel_str.endswith((".example", ".sample")):
            for pattern, label in SECRET_PATTERNS:
                matches = pattern.finditer(content)
                for m in matches:
                    line_num = content[:m.start()].count("\n") + 1
                    security_warnings.append({
                        "file": rel_str,
                        "line": line_num,
                        "severity": "high",
                        "issue": label,
                        "recommendation": "Move secrets to server environment variables (.env) and access via process.env or os.environ.",
                    })

        for pattern, label in DANGEROUS_PATTERNS:
            matches = pattern.finditer(content)
            for m in matches:
                line_num = content[:m.start()].count("\n") + 1
                security_warnings.append({
                    "file": rel_str,
                    "line": line_num,
                    "severity": "critical",
                    "issue": label,
                    "recommendation": "Avoid unbounded destructive shell operations or unsanitized command interpolations.",
                })

    is_clean = len(syntax_errors) == 0 and len(security_warnings) == 0
    return {
        "ok": True,
        "clean": is_clean,
        "scanned_files_count": scanned_count,
        "syntax_errors_count": len(syntax_errors),
        "security_warnings_count": len(security_warnings),
        "syntax_errors": syntax_errors,
        "security_warnings": security_warnings,
        "summary": "Passed all syntax and security checks cleanly." if is_clean else f"Found {len(syntax_errors)} syntax error(s) and {len(security_warnings)} security warning(s).",
    }


def _build_workspace_tree(ws_dir: Path, max_depth: int = 3) -> Dict[str, Any]:
    """Generate structured directory tree representation."""
    ignored = {".git", "node_modules", ".venv", "__pycache__", ".next", "dist", "build"}

    def _walk(current: Path, depth: int) -> List[Dict[str, Any]]:
        if depth > max_depth or not current.exists():
            return []
        items = []
        try:
            for entry in sorted(current.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if entry.name in ignored:
                    continue
                rel = str(entry.relative_to(ws_dir))
                if entry.is_dir():
                    items.append({
                        "name": entry.name,
                        "path": rel,
                        "type": "directory",
                        "children": _walk(entry, depth + 1) if depth < max_depth else [],
                    })
                else:
                    items.append({
                        "name": entry.name,
                        "path": rel,
                        "type": "file",
                        "size_bytes": entry.stat().st_size if entry.exists() else 0,
                    })
        except Exception:
            pass
        return items

    return {"root": str(ws_dir.name), "tree": _walk(ws_dir, 1)}


async def execute_syte_tool(project_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool requested by the AI Builder agent against the Syte framework and VM."""
    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": f"Project '{project_id}' not found."}

    ws_dir = _get_project_workspace_dir(project)

    try:
        if tool_name == "syte_create_deployment":
            branch = arguments.get("branch") or project.get("branch") or "main"
            queued, message = await issue_deploy(project_id, trigger="ai_builder")
            return {
                "ok": bool(queued),
                "status": "queued" if queued else "failed",
                "message": message or f"Deployment triggered for branch '{branch}'.",
                "project_id": project_id,
            }

        elif tool_name == "syte_get_deployment_logs":
            limit = int(arguments.get("limit") or 60)
            keyword = str(arguments.get("filter_keyword") or "").strip().lower()
            level = str(arguments.get("log_level") or "all").lower()
            should_diagnose = bool(arguments.get("diagnose", True))

            raw_logs = await get_logs(project_id, limit=limit * 2 if (keyword or level != "all") else limit)
            filtered = []
            for line in raw_logs:
                lower_line = line.lower()
                if keyword and keyword not in lower_line:
                    continue
                if level == "errors" and not any(e in lower_line for e in ["error", "fail", "fatal", "exception", "ts2"]):
                    continue
                if level == "warnings" and not any(w in lower_line for w in ["warn", "warning", "deprecat"]):
                    continue
                filtered.append(line)

            logs = filtered[-limit:] if filtered else raw_logs[-limit:]
            diagnosis = _diagnose_deployment_errors(logs) if should_diagnose else {}
            return {
                "ok": True,
                "logs": logs,
                "lines_count": len(logs),
                "has_errors": diagnosis.get("has_error", False),
                "diagnosis": diagnosis,
            }

        elif tool_name == "syte_get_router_logs":
            search = str(arguments.get("search") or "")
            status_code = str(arguments.get("status_code") or "")
            limit = int(arguments.get("limit") or 40)
            logs = await list_project_router_logs(project_id, search=search, status_code=status_code, limit=limit)
            return {"ok": True, "router_logs": logs, "count": len(logs)}

        elif tool_name == "syte_read_file":
            rel_path = str(arguments.get("path") or "").lstrip("/\\").strip()
            if not rel_path or rel_path in (".", "/"):
                return {"ok": False, "error": "Missing 'path' parameter. Please specify the relative file path to read."}
            file_path = ws_dir / rel_path
            if not file_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if file_path.is_dir():
                return {"ok": False, "error": f"'{rel_path}' is a directory, not a file. Use syte_get_workspace_tree or syte_search_workspace."}
            if not file_path.exists():
                return {"ok": False, "error": f"File '{rel_path}' does not exist."}
            content = file_path.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "path": rel_path, "size_bytes": len(content), "content": content}

        elif tool_name == "syte_write_file":
            rel_path = str(arguments.get("path") or "").lstrip("/\\").strip()
            content = arguments.get("content", "")
            if not rel_path or rel_path in (".", "/"):
                return {"ok": False, "error": "Missing or invalid 'path' parameter for file write. Please specify a file path like 'lib/catalog.ts'."}
            file_path = ws_dir / rel_path
            if not file_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if file_path.is_dir():
                return {"ok": False, "error": f"Cannot write to '{rel_path}' because it is a directory."}
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {"ok": True, "path": rel_path, "written_bytes": len(content), "message": f"File '{rel_path}' written successfully."}

        elif tool_name == "syte_edit_file":
            rel_path = str(arguments.get("path") or "").lstrip("/\\").strip()
            old_text = arguments.get("old_text", "")
            new_text = arguments.get("new_text", "")
            if not rel_path or rel_path in (".", "/"):
                return {"ok": False, "error": "Missing or invalid 'path' parameter for file edit."}
            file_path = ws_dir / rel_path
            if not file_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if not file_path.exists() or file_path.is_dir():
                return {"ok": False, "error": f"File '{rel_path}' does not exist or is a directory."}
            existing = file_path.read_text(encoding="utf-8", errors="replace")
            if old_text not in existing:
                return {"ok": False, "error": "Target substring 'old_text' was not found in file."}
            updated = existing.replace(old_text, new_text, 1)
            file_path.write_text(updated, encoding="utf-8")
            return {"ok": True, "path": rel_path, "message": f"Updated '{rel_path}' successfully."}

        elif tool_name == "syte_move_file":
            src = arguments.get("source_path", "").lstrip("/\\")
            dest = arguments.get("destination_path", "").lstrip("/\\")
            src_path = ws_dir / src
            dest_path = ws_dir / dest
            if not src_path.resolve().is_relative_to(ws_dir.resolve()) or not dest_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if not src_path.exists():
                return {"ok": False, "error": f"Source path '{src}' does not exist."}
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dest_path))
            return {"ok": True, "source": src, "destination": dest, "message": f"Moved '{src}' to '{dest}' successfully."}

        elif tool_name == "syte_delete_file":
            rel_path = arguments.get("path", "").lstrip("/\\")
            target = ws_dir / rel_path
            if not target.resolve().is_relative_to(ws_dir.resolve()) or target.resolve() == ws_dir.resolve():
                return {"ok": False, "error": "Access denied: Cannot delete root workspace."}
            if not target.exists():
                return {"ok": False, "error": f"Path '{rel_path}' does not exist."}
            if target.is_dir():
                shutil.rmtree(str(target))
            else:
                target.unlink()
            return {"ok": True, "path": rel_path, "message": f"Deleted '{rel_path}' successfully."}

        elif tool_name == "syte_list_files":
            directory = arguments.get("directory", "").lstrip("/\\")
            target_dir = (ws_dir / directory).resolve()
            if not target_dir.is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if not target_dir.exists():
                return {"ok": False, "error": f"Directory '{directory}' does not exist."}

            max_depth = int(arguments.get("max_depth") or 3)
            files = []
            for root, dirs, filenames in os.walk(target_dir):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", ".next", "dist"}]
                rel_root = Path(root).relative_to(ws_dir)
                depth = len(rel_root.parts)
                if depth > max_depth:
                    continue
                for f in filenames:
                    rel_f = str(rel_root / f) if str(rel_root) != "." else f
                    files.append(rel_f)

            return {"ok": True, "directory": directory or ".", "count": len(files), "files": sorted(files[:300])}

        elif tool_name == "syte_search_files":
            query = str(arguments.get("query") or "").strip()
            pattern = str(arguments.get("file_pattern") or "*").strip()
            if not query:
                return {"ok": False, "error": "Search query cannot be empty."}
            matches = []
            for root, dirs, filenames in os.walk(ws_dir):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", ".next", "dist"}]
                for f in filenames:
                    if pattern != "*" and not Path(f).match(pattern):
                        continue
                    full_p = Path(root) / f
                    try:
                        text = full_p.read_text(encoding="utf-8", errors="ignore")
                        for line_no, line in enumerate(text.splitlines(), 1):
                            if query.lower() in line.lower():
                                rel_file = str(full_p.relative_to(ws_dir))
                                matches.append({
                                    "file": rel_file,
                                    "line": line_no,
                                    "content": line.strip()[:180],
                                })
                                if len(matches) >= 50:
                                    break
                    except Exception:
                        continue
                    if len(matches) >= 50:
                        break
                if len(matches) >= 50:
                    break
            return {"ok": True, "query": query, "matches_count": len(matches), "matches": matches}

        elif tool_name == "syte_run_command":
            cmd = arguments.get("command", "").strip()
            if not cmd:
                return {"ok": False, "error": "Command cannot be empty."}

            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                out_str = stdout.decode("utf-8", errors="replace")
                err_str = stderr.decode("utf-8", errors="replace")
                return {
                    "ok": proc.returncode == 0,
                    "command": cmd,
                    "exit_code": proc.returncode,
                    "stdout": out_str,
                    "stderr": err_str,
                }
            except asyncio.TimeoutError:
                proc.kill()
                return {"ok": False, "command": cmd, "error": "Command timed out after 120 seconds."}

        elif tool_name == "syte_github_account_info":
            from syte.database import list_operator_accounts
            from syte.github_oauth import connection_summary
            accounts = await list_operator_accounts()
            for acc in accounts:
                summ = await connection_summary(acc["id"])
                if summ.get("connected"):
                    return {"ok": True, "connected": True, "account": summ}
            return {"ok": True, "connected": False, "message": "No GitHub account currently connected in Syte."}

        elif tool_name == "syte_github_list_repos":
            from syte.database import list_operator_accounts
            from syte.github_oauth import list_repositories
            query = str(arguments.get("query") or "")
            accounts = await list_operator_accounts()
            for acc in accounts:
                try:
                    repos = await list_repositories(acc["id"], query=query)
                    return {"ok": True, "connected": True, "count": len(repos), "repositories": repos[:40]}
                except Exception:
                    continue
            return {"ok": False, "error": "No connected GitHub account with repository access."}

        elif tool_name == "syte_git_status":
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--short", "--branch",
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            status_out = stdout.decode("utf-8", errors="replace")

            proc_log = await asyncio.create_subprocess_exec(
                "git", "log", "-n", "5", "--oneline",
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            log_out, _ = await proc_log.communicate()

            return {
                "ok": proc.returncode == 0,
                "status": status_out.strip() or "Clean working tree",
                "recent_commits": log_out.decode("utf-8", errors="replace").strip().splitlines(),
                "branch": project.get("branch") or "main",
                "git_url": project.get("git_url") or "None",
            }

        elif tool_name == "syte_git_commit":
            msg = str(arguments.get("message") or "").strip()
            if not msg:
                return {"ok": False, "error": "Commit message is required."}
            files = arguments.get("files") or []
            if isinstance(files, str):
                files = [files]

            add_args = ["git", "add"]
            if files and files != ["*"]:
                add_args.extend(files)
            else:
                add_args.append("-A")

            p_add = await asyncio.create_subprocess_exec(
                *add_args,
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await p_add.communicate()

            p_commit = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", msg,
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await p_commit.communicate()
            return {
                "ok": p_commit.returncode == 0,
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": stderr.decode("utf-8", errors="replace").strip(),
                "message": f"Committed with message: '{msg}'",
            }

        elif tool_name == "syte_git_push":
            branch = str(arguments.get("branch") or project.get("branch") or "main").strip()
            p_push = await asyncio.create_subprocess_exec(
                "git", "push", "origin", branch,
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await p_push.communicate()
            return {
                "ok": p_push.returncode == 0,
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": stderr.decode("utf-8", errors="replace").strip(),
            }

        elif tool_name == "syte_git_pull":
            branch = str(arguments.get("branch") or project.get("branch") or "main").strip()
            p_pull = await asyncio.create_subprocess_exec(
                "git", "pull", "origin", branch,
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await p_pull.communicate()
            return {
                "ok": p_pull.returncode == 0,
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": stderr.decode("utf-8", errors="replace").strip(),
            }

        elif tool_name == "syte_git_create_branch":
            bname = str(arguments.get("branch_name") or "").strip()
            if not bname:
                return {"ok": False, "error": "Branch name is required."}
            p_b = await asyncio.create_subprocess_exec(
                "git", "checkout", "-b", bname,
                cwd=str(ws_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await p_b.communicate()
            return {
                "ok": p_b.returncode == 0,
                "stdout": stdout.decode("utf-8", errors="replace").strip(),
                "stderr": stderr.decode("utf-8", errors="replace").strip(),
                "branch": bname,
            }

        elif tool_name == "syte_get_performance":
            stats = get_system_stats(sample_cpu=False)
            return {
                "ok": True,
                "project_running": bool(project.get("running")),
                "cpu_percent": stats.get("cpu_percent", 0.0),
                "ram_used_mb": stats.get("ram_used_mb", 0),
                "ram_total_mb": stats.get("ram_total_mb", 0),
                "disk_percent": stats.get("disk_percent", 0.0),
            }

        elif tool_name == "syte_get_environment":
            env_vars = project.get("env_vars") or {}
            return {"ok": True, "environment_variables": env_vars}

        elif tool_name == "syte_set_environment":
            from syte.database import update_project

            key = str(arguments.get("key") or "").strip()
            value = str(arguments.get("value") or "").strip()
            if not key:
                return {"ok": False, "error": "Key is required."}
            env_vars = dict(project.get("env_vars") or {})
            env_vars[key] = value
            await update_project(project_id, {"env_vars": env_vars})
            return {"ok": True, "key": key, "message": f"Environment variable '{key}' saved."}

        elif tool_name == "syte_manage_domains":
            action = arguments.get("action", "list")
            if action == "list":
                domains = [project.get("domain")] if project.get("domain") else []
                if isinstance(project.get("domains"), list):
                    domains.extend(project.get("domains"))
                return {"ok": True, "domains": list(set(d for d in domains if d))}
            elif action == "add":
                from syte.database import update_project

                dom = str(arguments.get("domain") or "").strip().lower()
                if not dom:
                    return {"ok": False, "error": "Domain name is required."}
                current_extra = list(project.get("domains") or [])
                if dom not in current_extra and dom != project.get("domain"):
                    current_extra.append(dom)
                    await update_project(project_id, {"domains": current_extra})
                return {"ok": True, "domain": dom, "message": f"Domain '{dom}' attached to project."}

        elif tool_name == "syte_start_preview":
            from syte.preview_manager import start_preview
            ok, msg, meta = await start_preview(project_id)
            return {
                "ok": ok,
                "message": msg,
                "preview_url": meta.get("preview_url") or "",
                "preview_domain": meta.get("preview_domain") or "",
                "preview_port": meta.get("preview_port"),
                "status": "running" if ok else "failed",
                "stack": meta.get("stack") or "",
            }

        elif tool_name == "syte_stop_preview":
            from syte.preview_manager import stop_preview_async
            await stop_preview_async(project_id)
            return {"ok": True, "message": "Preview server stopped."}

        elif tool_name == "syte_get_preview_status":
            from syte.preview_manager import get_preview_status
            meta, running = await get_preview_status(project_id)
            return {
                "ok": True,
                "running": running,
                "preview_url": meta.get("preview_url") or "",
                "status": "running" if running else "stopped",
                "meta": meta,
            }

        elif tool_name == "syte_create_plan":
            title = str(arguments.get("title") or "Implementation Plan").strip()
            raw_steps = arguments.get("steps") or []
            rationale = str(arguments.get("rationale") or "").strip()
            steps = []
            for idx, s in enumerate(raw_steps, 1):
                if isinstance(s, dict):
                    steps.append({
                        "id": str(s.get("id") or str(idx)),
                        "title": str(s.get("title") or f"Step {idx}"),
                        "status": str(s.get("status") or ("in_progress" if idx == 1 else "pending")),
                        "description": str(s.get("description") or ""),
                    })
                elif isinstance(s, str):
                    steps.append({
                        "id": str(idx),
                        "title": s,
                        "status": "in_progress" if idx == 1 else "pending",
                        "description": "",
                    })
            plan_data = {"title": title, "steps": steps, "rationale": rationale}
            return {
                "ok": True,
                "plan": plan_data,
                "steps_count": len(steps),
                "message": f"Created implementation plan with {len(steps)} steps: '{title}'.",
            }

        elif tool_name == "syte_update_plan_step":
            step_id = str(arguments.get("step_id") or "").strip()
            status = str(arguments.get("status") or "completed").strip()
            notes = str(arguments.get("notes") or "").strip()
            return {
                "ok": True,
                "step_id": step_id,
                "status": status,
                "notes": notes,
                "message": f"Plan step '{step_id}' marked as '{status}'.",
            }

        elif tool_name == "syte_list_skills":
            skills = list_available_skills()
            return {"ok": True, "skills": skills, "count": len(skills)}

        elif tool_name == "syte_discover_skills":
            category = arguments.get("category")
            query = arguments.get("query")
            detailed = bool(arguments.get("detailed", False))
            return discover_skills_catalog(category=category, query=query, detailed=detailed)

        elif tool_name == "syte_load_skill":
            sname = str(arguments.get("skill_name") or "").strip()
            content = get_skill_content(sname)
            if not content:
                return {
                    "ok": False,
                    "error": f"Skill '{sname}' not found. Use `syte_discover_skills` or `syte_list_skills` to view available skills.",
                }
            return {
                "ok": True,
                "skill_name": sname,
                "content": content,
                "message": f"Successfully loaded skill blueprint for '{sname}'.",
            }

        elif tool_name == "syte_ask_question":
            question = str(arguments.get("question") or "").strip()
            options = arguments.get("options") or []
            allow_custom = bool(arguments.get("allow_custom", True))
            return {
                "ok": True,
                "question": question,
                "options": options,
                "allow_custom": allow_custom,
                "requires_user_input": True,
                "message": f"Awaiting user clarification: '{question}'",
            }

        elif tool_name == "syte_ask_env_var":
            key = str(arguments.get("key") or "").strip()
            description = str(arguments.get("description") or "").strip()
            hint = str(arguments.get("hint") or "").strip()
            if not key:
                return {"ok": False, "error": "Environment variable key name is required."}
            return {
                "ok": True,
                "key": key,
                "description": description,
                "hint": hint,
                "is_secret_request": True,
                "requires_user_input": True,
                "message": f"Prompting user securely on the server for '{key}'. Secret value will be stored in server .env and masked from model context.",
            }

        elif tool_name == "syte_security_lint_scan":
            paths = arguments.get("paths")
            result = _perform_security_lint_scan(ws_dir, target_paths=paths)
            return result

        elif tool_name == "syte_read_file_lines":
            rel_path = arguments.get("path", "").lstrip("/\\")
            start_line = max(1, int(arguments.get("start_line") or 1))
            end_line = int(arguments.get("end_line") or (start_line + 50))
            file_path = ws_dir / rel_path
            if not file_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if not file_path.exists():
                return {"ok": False, "error": f"File '{rel_path}' does not exist."}
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            total_lines = len(lines)
            selected_lines = lines[start_line - 1 : end_line]
            numbered = [f"{start_line + i}: {line}" for i, line in enumerate(selected_lines)]
            return {
                "ok": True,
                "path": rel_path,
                "start_line": start_line,
                "end_line": min(end_line, total_lines),
                "total_lines": total_lines,
                "content": "\n".join(numbered),
            }

        elif tool_name == "syte_get_workspace_tree":
            depth = int(arguments.get("max_depth") or 3)
            tree = _build_workspace_tree(ws_dir, max_depth=depth)
            return {"ok": True, "workspace_tree": tree}

        return {"ok": False, "error": f"Unknown tool: '{tool_name}'"}

    except Exception as exc:
        return {"ok": False, "error": f"Tool execution failed: {str(exc)}"}
