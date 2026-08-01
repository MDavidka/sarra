"""System-level agent for the 'new feature?' settings tab.

The agent uses the selected AI model, has access to system files,
and can trigger an auto-update after completing its work.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from syte import __version__
from syte.ai_providers import (
    DEFAULT_PROFILE,
    PROFILE_ORDER,
    profile_provider,
)
from syte.cloud_agent import bridge_settings, resolve_profile_api_key
from syte.config import settings
from syte.database import get_setting
from syte.self_update import update_syte
from syte.workspace import run_cmd

INSTALL_DIR = Path(__file__).resolve().parent.parent


def get_current_version() -> str:
    """Return the currently installed Syte version."""
    return __version__


def get_update_target_info() -> dict[str, Any]:
    """Return information about the current update target."""
    from syte.update_source import resolve_update_target

    target = resolve_update_target(INSTALL_DIR)
    return {
        "source_type": target.source_type,
        "branch": target.branch,
        "label": target.label,
        "pr_number": target.pr_number,
        "pr_url": target.pr_url,
        "repo": target.repo,
    }


async def run_new_feature_agent(
    message: str,
    model_profile: str | None = None,
) -> dict[str, Any]:
    """Run the new-feature agent with system file access.

    The agent uses the selected model profile (or the default) and
    has tools to read/write system files and execute commands.
    After the agent finishes, an auto-update is triggered.
    """
    bridge = await bridge_settings()
    profile = (model_profile or bridge["default_profile"] or DEFAULT_PROFILE).strip()
    if profile not in PROFILE_ORDER:
        profile = DEFAULT_PROFILE

    resolved = await resolve_profile_api_key(profile)
    api_key = resolved["api_key"]
    if not api_key:
        return {
            "ok": False,
            "error": "api_key_missing",
            "message": (
                f"No API key configured for profile '{profile}'. "
                "Save it in Settings → AI provider settings first."
            ),
            "profile": profile,
        }

    spec = profile_provider(profile)
    model = spec.get("model", "")
    api_base = spec.get("api_base", "")
    provider_label = spec.get("label", profile)

    system_prompt = (
        "You are a system administration agent for Syte. "
        "You have access to system files and can read, write, and execute commands. "
        "Your task is to help the user implement new features, fix issues, or update the system. "
        "When you are done with your work, trigger an auto-update so the system stays current. "
        "Always report the current version before and after any update. "
        "Be careful with file operations — only modify files that are necessary. "
        "If you need to install dependencies, use the project's requirements.txt. "
        "After making changes, run the update process to apply them."
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the system.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to the file to read.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of bytes to read (default 512000).",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file on the system.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to the file to write.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write to the file.",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Execute a shell command on the system.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds (default 30, max 300).",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_version",
                "description": "Get the current installed Syte version.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "trigger_update",
                "description": "Pull the newest Syte version from git, refresh dependencies, and restart.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in a directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path to list files from.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
    ]

    user_message = message
    if not user_message.strip():
        return {
            "ok": False,
            "error": "empty_message",
            "message": "Please provide a message for the system agent.",
        }

    try:
        import httpx

        api_base_url = api_base.rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "temperature": 0.3,
        }

        response = httpx.post(
            f"{api_base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()

        assistant_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        tool_calls = data.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])

        if tool_calls:
            for tool_call in tool_calls:
                function_name = tool_call["function"]["name"]
                try:
                    function_args = json.loads(tool_call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    function_args = {}

                result = await _execute_tool(function_name, function_args)
                messages.append(
                    {
                        "role": "tool",
                        "content": json.dumps(result, default=str),
                        "tool_call_id": tool_call["id"],
                    }
                )

            payload["messages"] = messages
            response2 = httpx.post(
                f"{api_base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120.0,
            )
            response2.raise_for_status()
            data2 = response2.json()
            assistant_content = data2.get("choices", [{}])[0].get("message", {}).get("content", "")

        current_version = get_current_version()
        update_info = get_update_target_info()

        return {
            "ok": True,
            "profile": profile,
            "provider": provider_label,
            "model": model,
            "response": assistant_content,
            "current_version": current_version,
            "update_target": update_info,
            "triggered_update": False,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": "agent_failed",
            "message": f"Agent execution failed: {exc}",
            "profile": profile,
        }


async def _execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call from the AI agent."""
    if name == "read_file":
        path = args.get("path", "")
        limit = args.get("limit", 512000)
        try:
            f = Path(path)
            if not f.exists():
                return {"ok": False, "error": "not_found", "message": f"File not found: {path}"}
            content = f.read_text(encoding="utf-8", errors="replace")[:limit]
            return {"ok": True, "path": path, "content": content, "size": len(content)}
        except Exception as exc:
            return {"ok": False, "error": "read_failed", "message": str(exc)}

    if name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        try:
            f = Path(path)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
            return {"ok": True, "path": path, "message": f"Written {len(content)} bytes to {path}"}
        except Exception as exc:
            return {"ok": False, "error": "write_failed", "message": str(exc)}

    if name == "execute_command":
        command = args.get("command", "")
        try:
            cmd_list = shlex.split(command)
            code, output = run_cmd(cmd_list, cwd=INSTALL_DIR)
            return {"ok": code == 0, "exit_code": code, "output": output, "command": command}
        except Exception as exc:
            return {"ok": False, "error": "exec_failed", "message": str(exc)}

    if name == "get_version":
        return {"ok": True, "version": get_current_version()}

    if name == "trigger_update":
        try:
            ok, message = update_syte()
            return {"ok": ok, "message": message, "version": get_current_version()}
        except Exception as exc:
            return {"ok": False, "error": "update_failed", "message": str(exc)}

    if name == "list_files":
        path = args.get("path", "")
        try:
            d = Path(path)
            if not d.exists() or not d.is_dir():
                return {"ok": False, "error": "not_found", "message": f"Directory not found: {path}"}
            files = [
                {
                    "name": f.name,
                    "path": str(f),
                    "is_dir": f.is_dir(),
                    "size": f.stat().st_size if f.is_file() else 0,
                }
                for f in sorted(d.iterdir())
            ]
            return {"ok": True, "path": path, "files": files}
        except Exception as exc:
            return {"ok": False, "error": "list_failed", "message": str(exc)}

    return {"ok": False, "error": "unknown_tool", "message": f"Unknown tool: {name}"}