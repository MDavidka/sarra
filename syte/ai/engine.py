"""OpenCode-inspired Autonomous AI Agent Engine for Syte.

Handles autonomous multi-turn loops, tool execution, session history, and real-time SSE streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
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


def extract_text_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract tool calls from model text output when native function calling chunk is omitted."""
    tool_calls = []

    # 1. Look for <tool_call> or <function_call> XML tags
    xml_matches = re.finditer(r'<(?:tool_call|function_call)>\s*(\{.*?\})\s*</(?:tool_call|function_call)>', text, re.DOTALL)
    for i, m in enumerate(xml_matches, start=1):
        try:
            data = json.loads(m.group(1))
            name = data.get("name") or data.get("tool") or data.get("function")
            args = data.get("arguments") or data.get("parameters") or {}
            if name and isinstance(name, str) and name.startswith("syte_"):
                tool_calls.append({
                    "type": "tool_call",
                    "id": f"call_xml_{i}",
                    "name": name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                })
        except Exception:
            pass

    if tool_calls:
        return tool_calls

    # 2. Look for ```json ``` blocks with {"name": "syte_...", "arguments": ...} or {"tool": "syte_..."}
    json_blocks = re.finditer(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    for i, m in enumerate(json_blocks, start=1):
        try:
            data = json.loads(m.group(1))
            name = data.get("name") or data.get("tool") or data.get("action")
            args = data.get("arguments") or data.get("parameters") or data.get("action_input") or {}
            if name and isinstance(name, str) and name.startswith("syte_"):
                tool_calls.append({
                    "type": "tool_call",
                    "id": f"call_block_{i}",
                    "name": name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                })
        except Exception:
            pass

    return tool_calls


def extract_plan_from_markdown_text(title_fallback: str, text: str) -> Optional[Dict[str, Any]]:
    """Parse a multi-step plan from markdown text headings when syte_create_plan tool was not invoked."""
    step_pattern = re.compile(r'(?:^|\n)(?:#{1,4}\s*)?(?:Step\s*(\d+)[:.]\s*|(\d+)\.\s+)(.+)', re.IGNORECASE)
    matches = step_pattern.findall(text)
    if len(matches) >= 2:
        steps = []
        for idx, m in enumerate(matches, start=1):
            step_title = m[2].strip().replace('**', '').replace('__', '')
            step_title = step_title.split('\n')[0].strip()
            if len(step_title) > 80:
                step_title = step_title[:77] + '...'
            steps.append({
                "id": str(idx),
                "title": step_title,
                "status": "pending",
                "notes": "",
            })
        return {
            "title": title_fallback or "Project Implementation Plan",
            "steps": steps,
        }
    return None


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

            # Gather existing workspace file tree
            ws_files_list = []
            if ws_dir.exists():
                for p in sorted(ws_dir.rglob("*")):
                    if any(ign in p.parts for ign in [".git", "node_modules", ".venv", "__pycache__", "dist", "build"]):
                        continue
                    if len(ws_files_list) < 40:
                        rel = p.relative_to(ws_dir)
                        ws_files_list.append(str(rel) + ("/" if p.is_dir() else ""))
            ws_files_summary = "\n".join(f"  - {f}" for f in ws_files_list) if ws_files_list else "  (Workspace directory is currently empty)"

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
                f"Existing Workspace Files:\n{ws_files_summary}\n"
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
            "\n\n--- SYTE AUTONOMOUS AGENT CORE ARCHITECTURE & EXECUTION STANDARDS ---\n"
            "You are the Syte Autonomous AI Builder & Principal Site Architect — an elite autonomous AI engineer embedded directly in the Syte platform, operating at the quality bar of v0, Google Cloud Code, and Antigravity.\n\n"
            "## 1. AUTONOMOUS END-TO-END OWNERSHIP\n"
            "- When given a request (e.g. 'redesign frontend', 'add auth', 'build landing page', 'fix bug', 'optimize build'), YOU MUST NOT STOP UNTIL THE TASK IS 100% READY.\n"
            "- Do not give partial advice or ask 'shall I proceed?'. Take action immediately by creating a plan, reading files, writing complete code, verifying syntax, and testing.\n"
            "- If a plan has uncompleted steps, you will continue automatically to the next step without pausing or waiting for human approval.\n\n"
            "## 2. PROFESSIONAL DESIGN & UI/UX STANDARDS (v0 / Antigravity Standard)\n"
            "- **Typography**: Modern font stack (Inter, Geist Sans, system UI). Strict hierarchy: Display H1 (tight tracking `-0.03em`), Section H2, Card H3, muted lead copy, and crisp caption badges.\n"
            "- **Color & Styling**: Tailwind CSS / modern CSS. Zinc/Slate neutral scale, glassmorphism (`backdrop-blur-md bg-white/80 border border-zinc-200/60`), vibrant accent colors (Indigo `#6366f1`, Sky `#0284c7`, Emerald `#10b981`).\n"
            "- **Component Library**: Use shadcn/ui style components (Cards, Pills, Action Buttons, Badges, Hero banners, Feature grids, Responsive navbar with mobile sheet) and Lucide icons.\n"
            "- **Zero-Placeholder Guarantee**: ALWAYS write complete, production-ready code. Never leave `// TODO`, `/* implement later */`, or incomplete functions.\n"
            "- **Responsive**: Mobile-first fluid layouts (`grid-cols-1 md:grid-cols-2 lg:grid-cols-3`), touch targets >= 44px, zero horizontal overflow.\n\n"
            "## 3. MANDATORY EXECUTION WORKFLOW\n"
            "1. **Plan**: For any multi-step task, start by calling `syte_create_plan` with actionable steps.\n"
            "2. **Skills**: Discover or load domain skills with `syte_load_skill` (e.g. 'website-create' for UI/Tailwind/shadcn, 'integration' for APIs/Auth/DB, 'cloud-code' for DevOps).\n"
            "3. **Inspect**: Read workspace files (`syte_read_file`, `syte_search_files`, `syte_list_workspace_files`).\n"
            "4. **Write/Edit**: Create or modify code files (`syte_write_file`, `syte_edit_file`).\n"
            "5. **Update Step Status**: Keep the user updated by marking steps `in_progress` and then `completed` using `syte_update_plan_step`.\n"
            "6. **Verify & Test**: Run `syte_security_lint_scan` to verify AST syntax and safety, run terminal builds (`syte_run_command`), and launch preview servers (`syte_start_preview`).\n"
            "7. **Deliver**: Provide a concise summary of what was accomplished only after all steps are done.\n"
            "------------------------------------------------------------------------\n"
        )
        full_system_prompt = f"{base_prompt}\n{autonomous_instructions}\n{context_prompt}"

        # 5. Load message history with context compaction for older oversized tool responses
        history = await list_ai_chat_messages(self.project_id, limit=40)
        formatted_messages: List[Dict[str, Any]] = []
        total_history = len(history)
        for idx, msg in enumerate(history):
            content = msg.get("content") or ""
            # If older tool output (> 6 messages ago) is huge (> 2500 chars), compact it to save gateway latency
            if msg.get("role") == "tool" and idx < total_history - 6 and len(content) > 2500:
                content = content[:1200] + "\n...[truncated for context efficiency]...\n" + content[-600:]
            m = {"role": msg["role"], "content": content}
            if msg.get("tool_calls"):
                m["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                m["tool_call_id"] = msg["tool_call_id"]
            if msg.get("name"):
                m["name"] = msg["name"]
            formatted_messages.append(m)

        tools_schema = get_ai_tools_schema() if ai_settings.get("tools_enabled") != "none" else None

        # 6. Autonomous execution loop (up to 60 tool turns for continuous full-task completion)
        max_turns = 60
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

            stream_error = None
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
                    err_msg = chunk.get("content", "LLM communication error")
                    if any(t in err_msg.lower() for t in ["timed out", "timeout", "504", "502", "503", "connection reset"]) and current_turn < max_turns:
                        stream_error = err_msg
                        break
                    yield {"event": "error", "error": err_msg}
                    return

            if stream_error:
                yield {
                    "event": "status",
                    "message": f"Gateway timeout, retrying turn {current_turn}…",
                    "turn": current_turn,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await asyncio.sleep(2)
                current_turn -= 1  # retry turn
                continue

            final_response_text += turn_tokens

            # Extract tool calls from text if native function calling chunks were empty
            if not turn_tool_calls:
                parsed_calls = extract_text_tool_calls(turn_tokens)
                if parsed_calls:
                    turn_tool_calls = parsed_calls

            # If no tool calls were requested in this turn
            # If no tool calls were requested in this turn
            if not turn_tool_calls:
                active_plan = getattr(self.session, "active_plan", None) if self.session else None

                # If no active plan exists yet, attempt to parse markdown step headings from turn_tokens
                if not active_plan and turn_tokens:
                    parsed_plan = extract_plan_from_markdown_text(str(user_message or "")[:60], turn_tokens)
                    if parsed_plan and self.session:
                        self.session.active_plan = parsed_plan
                        active_plan = parsed_plan
                        yield {
                            "event": "tool_call_result",
                            "tool_call_id": "auto_plan_1",
                            "tool_name": "syte_create_plan",
                            "result": {"ok": True, "plan": parsed_plan, "message": f"Created implementation plan with {len(parsed_plan.get('steps') or [])} steps."},
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }

                pending_steps = []
                if active_plan and isinstance(active_plan.get("steps"), list):
                    pending_steps = [s for s in (active_plan.get("steps") or []) if isinstance(s, dict) and s.get("status") != "completed"]

                # If there are unfinished plan steps, automatically drive the next step without stopping
                if pending_steps and current_turn < max_turns:
                    next_step = pending_steps[0]
                    step_title = next_step.get("title") or f"Step {next_step.get('id') or '1'}"
                    continuation_prompt = (
                        f"Autonomous Execution Directive: Plan step '{step_title}' is currently pending. "
                        "Do not stop, do not ask for user confirmation, and do not wait. Immediately call the necessary tools "
                        "(e.g. syte_read_file, syte_write_file, syte_edit_file, syte_run_command), execute the changes, "
                        "update the step status with syte_update_plan_step, and proceed autonomously until all plan steps are complete."
                    )
                    await save_ai_chat_message(self.project_id, role="assistant", content=turn_tokens)
                    formatted_messages.append({"role": "assistant", "content": turn_tokens})
                    formatted_messages.append({"role": "user", "content": continuation_prompt})
                    yield {
                        "event": "status",
                        "message": f"Auto-advancing plan step: {step_title}…",
                        "turn": current_turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    continue

                action_intent_phrases = ["let me", "i will", "i'll create", "i'll build", "step 1", "first,", "to start,", "i need to", "let's start", "let's build", "let's create", "let me examine", "let me inspect"]
                has_action_intent = any(phrase in str(turn_tokens or "").lower() for phrase in action_intent_phrases)
                is_complex_request = any(kw in str(user_message or "").lower() for kw in ["build", "create", "redesign", "add", "fix", "implement", "update", "make", "refactor", "setup", "style"])

                if (has_action_intent or is_complex_request) and current_turn < 4:
                    auto_start_prompt = (
                        "Autonomous Directive: Please invoke `syte_create_plan` or execute file/command tools directly now. "
                        "Do not stop or output prose descriptions without calling tools."
                    )
                    await save_ai_chat_message(self.project_id, role="assistant", content=turn_tokens)
                    formatted_messages.append({"role": "assistant", "content": turn_tokens})
                    formatted_messages.append({"role": "user", "content": auto_start_prompt})
                    yield {
                        "event": "status",
                        "message": "Initiating autonomous plan execution…",
                        "turn": current_turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    continue

                # Everything is completed: save final response and emit done
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

                # If a shell command failed, append an auto-remediation directive to guide autonomous self-healing
                if tool_name == "syte_run_command" and (not tool_result.get("ok") or tool_result.get("exit_code") not in (0, None)):
                    tool_result["remediation_directive"] = (
                        "Auto-Remediation Directive: The command failed. Analyze the stderr/stdout output above, "
                        "inspect the relevant files using syte_read_file, fix the syntax/dependency/type issue using syte_write_file or syte_edit_file, "
                        "and re-run the verification command to confirm resolution."
                    )

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

                # Sync active plan in session
                if tool_name == "syte_create_plan" and tool_result.get("plan") and self.session:
                    self.session.active_plan = tool_result.get("plan")
                elif tool_name == "syte_update_plan_step" and self.session and getattr(self.session, "active_plan", None):
                    step_id = str(args.get("step_id") or "")
                    new_status = str(args.get("status") or "completed")
                    notes = str(args.get("notes") or "")
                    plan_steps = self.session.active_plan.get("steps") if isinstance(self.session.active_plan, dict) else None
                    for stp in (plan_steps or []):
                        if isinstance(stp, dict) and str(stp.get("id")) == step_id:
                            stp["status"] = new_status
                            if notes:
                                stp["notes"] = notes

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
