"""Tool definitions and executors for the Syte AI Agent.

Gives the LLM complete autonomous access to the Syte platform, filesystem, terminal, deployments, logs, and infrastructure.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List

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
                "description": "Retrieve stdout/stderr logs from the current or latest deployment process.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of recent log lines to retrieve (default 60).",
                        }
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
            logs = await get_logs(project_id, limit=limit)
            return {"ok": True, "logs": logs, "lines_count": len(logs)}

        elif tool_name == "syte_get_router_logs":
            search = str(arguments.get("search") or "")
            status_code = str(arguments.get("status_code") or "")
            limit = int(arguments.get("limit") or 40)
            logs = await list_project_router_logs(project_id, search=search, status_code=status_code, limit=limit)
            return {"ok": True, "router_logs": logs, "count": len(logs)}

        elif tool_name == "syte_read_file":
            rel_path = arguments.get("path", "").lstrip("/\\")
            file_path = ws_dir / rel_path
            if not file_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if not file_path.exists():
                return {"ok": False, "error": f"File '{rel_path}' does not exist."}
            content = file_path.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "path": rel_path, "size_bytes": len(content), "content": content}

        elif tool_name == "syte_write_file":
            rel_path = arguments.get("path", "").lstrip("/\\")
            content = arguments.get("content", "")
            file_path = ws_dir / rel_path
            if not file_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {"ok": True, "path": rel_path, "written_bytes": len(content), "message": f"File '{rel_path}' written successfully."}

        elif tool_name == "syte_edit_file":
            rel_path = arguments.get("path", "").lstrip("/\\")
            old_text = arguments.get("old_text", "")
            new_text = arguments.get("new_text", "")
            file_path = ws_dir / rel_path
            if not file_path.resolve().is_relative_to(ws_dir.resolve()):
                return {"ok": False, "error": "Access denied: Path escapes project workspace."}
            if not file_path.exists():
                return {"ok": False, "error": f"File '{rel_path}' does not exist."}
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
            from syte.database import update_project_env

            key = str(arguments.get("key") or "").strip()
            value = str(arguments.get("value") or "").strip()
            if not key:
                return {"ok": False, "error": "Key is required."}
            env_vars = dict(project.get("env_vars") or {})
            env_vars[key] = value
            await update_project_env(project_id, env_vars)
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

        return {"ok": False, "error": f"Unknown tool: '{tool_name}'"}

    except Exception as exc:
        return {"ok": False, "error": f"Tool execution failed: {str(exc)}"}
