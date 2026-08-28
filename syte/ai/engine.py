"""OpenCode-inspired Autonomous AI Agent Engine for Syte.

Handles autonomous multi-turn loops, tool execution, session history, and real-time SSE streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from syte.ai.providers import UnifiedAIClient
from syte.ai.tools import execute_syte_tool, get_ai_tools_schema, _get_project_workspace_dir
from syte.database import (
    get_ai_builder_settings,
    get_project,
    list_ai_chat_messages,
    save_ai_chat_message,
)

logger = logging.getLogger("syte.ai.engine")


class AIAgentEngine:
    """Executes multi-step agent reasoning and tool calling loops."""

    def __init__(self, project_id: str, session: Optional[Any] = None):
        self.project_id = project_id
        self.session = session

    async def run_agent_turn(
        self,
        user_message: str,
        settings_override: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute a full autonomous agent turn with streaming output and tool execution."""
        # 1. Save incoming user message
        await save_ai_chat_message(self.project_id, role="user", content=user_message)
        yield {"event": "user_message_received", "content": user_message}

        # 2. Load project context
        if self.project_id == "global":
            from syte.database import list_projects
            all_projects = await list_projects()
            project = {"id": "global", "name": "Global Platform"}
            projects_summary = "\n".join([f"  • {p.get('name')} (ID: {p.get('id')}, Domain: {p.get('domain') or 'none'}, Running: {bool(p.get('running'))})" for p in all_projects[:15]])
            context_prompt = (
                f"\n\n--- ACTIVE SYTE PLATFORM CONTEXT (GLOBAL) ---\n"
                f"Active Projects on this Host:\n{projects_summary or '  • No projects registered yet.'}\n"
                f"-----------------------------------------------\n"
            )
        else:
            project = await get_project(self.project_id)
            if not project:
                yield {"event": "error", "error": f"Project '{self.project_id}' not found."}
                return

            ws_dir = _get_project_workspace_dir(project)
            github_info = "Not connected"
            try:
                from syte.database import list_operator_accounts
                from syte.github_oauth import connection_summary
                accounts = await list_operator_accounts()
                for acc in accounts:
                    summ = await connection_summary(acc["id"])
                    if summ.get("connected"):
                        github_info = f"@{summ.get('login')} (Scopes: {summ.get('scopes')})"
                        break
            except Exception:
                pass

            context_prompt = (
                f"\n\n--- ACTIVE SYTE PROJECT CONTEXT ---\n"
                f"- Project ID: {project.get('id')}\n"
                f"- Project Name: {project.get('name')}\n"
                f"- Production Domain: {project.get('domain') or 'None'}\n"
                f"- Active Branch: {project.get('branch') or 'main'}\n"
                f"- Running Status: {'Running' if project.get('running') else 'Stopped'} (Port {project.get('port') or 'unassigned'})\n"
                f"- Connected Git Repository: {project.get('git_url') or 'None'}\n"
                f"- Logged-in Git / GitHub Account: {github_info}\n"
                f"- VM Workspace Directory: {str(ws_dir)}\n"
                f"Capabilities: You have full autonomous tools to manage this project workspace on the host VM: read/write/edit/move/delete/search files, execute shell bash commands, stage and commit git changes, push/pull branches, query the logged-in GitHub account, view real-time router/deployment logs, and trigger zero-downtime deployments.\n"
                f"------------------------------------\n"
            )

        # 3. Load AI Builder settings
        ai_settings = await get_ai_builder_settings(self.project_id)
        if settings_override:
            ai_settings.update(settings_override)

        client = UnifiedAIClient(
            provider=ai_settings.get("provider", "openai"),
            model=ai_settings.get("model", "gpt-4o"),
            api_key=ai_settings.get("api_key", ""),
            base_url=ai_settings.get("base_url", ""),
            temperature=float(ai_settings.get("temperature", 0.7)),
            max_tokens=int(ai_settings.get("max_tokens", 4096)),
            thinking_level=ai_settings.get("thinking_level", "medium"),
        )

        # 4. Assemble system prompt with live project context & workflow rules
        base_prompt = ai_settings.get("system_prompt") or ""
        autonomous_instructions = (
            "\n\n--- AUTONOMOUS EXECUTION & QUALITY STANDARDS ---\n"
            "1. MANDATORY PLANNING: Before modifying or creating code files, ALWAYS create an implementation plan using `syte_create_plan` detailing your planned steps. Update step statuses using `syte_update_plan_step` as you make progress.\n"
            "2. MODULAR SKILLS: Use `syte_list_skills` and `syte_load_skill` to load rich design systems ('website-create' for shadcn/ui + Inter font + harmonious color palettes + responsive sizing), backend patterns ('integration'), LLM setups ('providers'), and DevOps ('cloud-code').\n"
            "3. CLARIFYING QUESTIONS: If requirements are ambiguous (e.g. style preferences, page count), ask the user using `syte_ask_question` with interactive choices.\n"
            "4. SECURE SECRETS: Never ask users to paste raw API keys in chat text. Use `syte_ask_env_var` to prompt for secrets, which will be safely saved to the project .env on the server.\n"
            "5. PRE-EXECUTION LINT & SECURITY: Run `syte_security_lint_scan` to verify syntax and ensure no vulnerabilities or hardcoded secrets exist before finishing.\n"
            "6. CONTINUOUS THOUGHT LOGGING: At each step, explicitly state your immediate reasoning and next goal (e.g. 'Now I have to inspect the configuration...', 'Now I have to edit App.tsx...').\n"
            "7. TERMINAL & PREVIEW: Use `syte_run_command` for terminal builds/tests and `syte_start_preview` to launch live development servers.\n"
            "---------------------------------------------------\n"
        )
        full_system_prompt = f"{base_prompt}\n{autonomous_instructions}\n{context_prompt}"

        # 5. Load message history
        history = await list_ai_chat_messages(self.project_id, limit=50)
        formatted_messages: List[Dict[str, Any]] = []
        for msg in history:
            m = {"role": msg["role"], "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                m["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                m["tool_call_id"] = msg["tool_call_id"]
            if msg.get("name"):
                m["name"] = msg["name"]
            formatted_messages.append(m)

        tools_schema = get_ai_tools_schema() if ai_settings.get("tools_enabled") != "none" else None

        # 6. Autonomous execution loop (up to 50 tool turns for longer runs)
        max_turns = 50
        current_turn = 0
        final_response_text = ""

        while current_turn < max_turns:
            current_turn += 1
            turn_tokens = ""
            turn_thoughts = ""
            turn_tool_calls: List[Dict[str, Any]] = []
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

            yield {"event": "status", "message": f"Thinking with {client.model}…", "turn": current_turn, "timestamp": now_iso}

            async for chunk in client.stream_chat(
                formatted_messages,
                tools=tools_schema,
                system_prompt=full_system_prompt,
            ):
                chunk_type = chunk.get("type")
                if chunk_type == "thought":
                    content = chunk.get("content", "")
                    turn_thoughts += content
                    yield {"event": "thought_delta", "delta": content, "timestamp": datetime.now(timezone.utc).isoformat()}
                elif chunk_type == "token":
                    content = chunk.get("content", "")
                    turn_tokens += content
                    yield {"event": "token_delta", "delta": content, "timestamp": datetime.now(timezone.utc).isoformat()}
                elif chunk_type == "tool_call":
                    turn_tool_calls.append(chunk)
                elif chunk_type == "error":
                    yield {"event": "error", "error": chunk.get("content", "LLM communication error")}
                    return

            final_response_text += turn_tokens

            # If no tool calls were requested, the model provided its complete response
            if not turn_tool_calls:
                await save_ai_chat_message(self.project_id, role="assistant", content=turn_tokens)
                yield {"event": "done", "reply": turn_tokens, "timestamp": datetime.now(timezone.utc).isoformat()}
                break

            # Save the assistant message with tool calls
            formatted_tool_calls = []
            for tc in turn_tool_calls:
                formatted_tool_calls.append(
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                )

            await save_ai_chat_message(
                self.project_id,
                role="assistant",
                content=turn_tokens,
                tool_calls=formatted_tool_calls,
            )
            formatted_messages.append(
                {
                    "role": "assistant",
                    "content": turn_tokens,
                    "tool_calls": formatted_tool_calls,
                }
            )

            # Execute requested tools
            for tc in turn_tool_calls:
                call_id = tc["id"]
                tool_name = tc["name"]
                raw_args = tc.get("arguments") or "{}"

                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                now_stamp = datetime.now(timezone.utc).isoformat()
                file_target = args.get("path") or args.get("source_path") or ""
                cmd_target = args.get("command") or ""

                yield {
                    "event": "tool_call_start",
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": args,
                    "file_path": file_target,
                    "command": cmd_target,
                    "timestamp": now_stamp,
                }

                # Execute tool
                tool_result = await execute_syte_tool(self.project_id, tool_name, args)

                # Check if tool requires interactive user response (questions / env secrets)
                if tool_result.get("requires_user_input") and self.session:
                    yield {
                        "event": "tool_call_result",
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "result": tool_result,
                        "file_path": file_target,
                        "command": cmd_target,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    user_resp = await self.session.wait_for_user_answer(tool_result)
                    tool_result = {**tool_result, "user_response": user_resp}
                    yield {
                        "event": "user_input_received",
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "user_response": user_resp,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                result_str = json.dumps(tool_result)

                if not tool_result.get("requires_user_input") or not self.session:
                    yield {
                        "event": "tool_call_result",
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "result": tool_result,
                        "file_path": file_target,
                        "command": cmd_target,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                # Save tool result in DB & conversation messages
                await save_ai_chat_message(
                    self.project_id,
                    role="tool",
                    content=result_str,
                    tool_call_id=call_id,
                    name=tool_name,
                )
                formatted_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_name,
                        "content": result_str,
                    }
                )
