"""OpenCode-inspired Autonomous AI Agent Engine for Syte.

Handles autonomous multi-turn loops, tool execution, session history, and real-time SSE streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from syte.ai.providers import UnifiedAIClient
from syte.ai.tools import execute_syte_tool, get_ai_tools_schema, _get_project_workspace_dir
from syte.database import (
    deduct_user_credits,
    get_ai_builder_settings,
    get_omni_model,
    get_project,
    get_user_credits,
    list_ai_chat_messages,
    save_ai_chat_message,
)

logger = logging.getLogger("syte.ai.engine")

# Large payload fields are truncated for the *streamed* tool_call_result only;
# the full result still goes to the model context and the DB verbatim. This
# keeps SSE frames small so tool bursts never starve token deltas of socket
# bandwidth.
_BRIEF_TRUNCATE_FIELDS = ("content", "stdout", "stderr", "output", "tree", "diff")
_BRIEF_MAX_CHARS = 2000
_BRIEF_MAX_LIST_ITEMS = 40


def _brief_stream_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return a wire-light copy of a tool result for SSE streaming."""
    if not isinstance(result, dict):
        return result
    brief: Dict[str, Any] = {}
    for key, value in result.items():
        if isinstance(value, str) and key in _BRIEF_TRUNCATE_FIELDS and len(value) > _BRIEF_MAX_CHARS:
            brief[key] = value[:1200] + f"\n…[truncated {len(value)} chars for stream]…\n" + value[-400:]
        elif isinstance(value, list) and key in ("logs", "files", "matches", "results") and len(value) > _BRIEF_MAX_LIST_ITEMS:
            brief[key] = value[-_BRIEF_MAX_LIST_ITEMS:]
            brief[f"{key}_total"] = len(value)
        else:
            brief[key] = value
    return brief


def _brief_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Keep streamed tool arguments small (file content is never needed live)."""
    if not isinstance(arguments, dict):
        return arguments
    brief = dict(arguments)
    content = brief.get("content")
    if isinstance(content, str) and len(content) > 400:
        brief["content"] = content[:400] + f"\n…[{len(content)} chars total]…"
        brief["content_bytes"] = len(content)
    return brief


def _determine_action_mark(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Classify tool execution into high-fidelity action marks for the UI feed."""
    t_lower = (tool_name or "").lower()
    cmd = str(args.get("command") or args.get("cmd") or "").strip()
    path = str(args.get("path") or args.get("file_path") or args.get("file") or args.get("filename") or "").strip()
    url = str(args.get("url") or args.get("query") or args.get("route") or "").strip()

    # 1. Security risk check
    risk_patterns = ["rm -rf /", "chmod 777", "curl | bash", "wget | bash", "mkfs", "> /dev/sda", ":(){ :|:& };:"]
    if any(p in cmd for p in risk_patterns):
        return {
            "kind": "security_risk",
            "label": "security risk!",
            "detail": f"Dangerous command flagged: {cmd[:40]}",
            "badge": None,
            "status": "warning",
            "is_risk": True,
        }

    # 2. Connecting to github
    if "git" in t_lower or "github" in t_lower or cmd.startswith("git ") or any(k in cmd for k in ["git clone", "git push", "git commit", "git pull", "git status", "git diff", "git log"]):
        return {
            "kind": "github",
            "label": "connecting to github",
            "detail": cmd if cmd.startswith("git") else (f"branch: {args.get('branch', 'main')}" if args.get("branch") else "GitHub repository sync"),
            "badge": "github",
            "status": "running",
            "is_risk": False,
        }

    # 3. Starting server
    if "start_preview" in t_lower or "start_server" in t_lower or any(p in cmd for p in ["npm run dev", "pnpm dev", "yarn dev", "bun dev", "next dev", "npm start", "python main.py", "uvicorn"]):
        return {
            "kind": "server",
            "label": "starting server",
            "detail": cmd or "Hot-reloading local preview",
            "badge": "starting",
            "status": "running",
            "is_risk": False,
        }

    # 4. Using browser
    if "preview" in t_lower or "screenshot" in t_lower or "browser" in t_lower:
        return {
            "kind": "browser",
            "label": "using browser",
            "detail": url or path or "preview viewport",
            "badge": "browser",
            "status": "running",
            "is_risk": False,
        }

    # 5. Using cloud servers
    if "deploy" in t_lower or "cloud" in t_lower or "service" in t_lower or "mcp" in t_lower:
        return {
            "kind": "cloud",
            "label": "using cloud servers",
            "detail": str(args.get("addon") or args.get("service") or "sycord cloud cluster"),
            "badge": "cloud",
            "status": "running",
            "is_risk": False,
        }

    # 6. Scraping web
    if "search" in t_lower or "fetch" in t_lower or "scrape" in t_lower or "curl " in cmd or "wget " in cmd:
        domain = "web.app"
        if url.startswith("http://") or url.startswith("https://"):
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                if parsed.hostname:
                    domain = parsed.hostname.replace("www.", "")
            except Exception:
                pass
        elif "." in url and " " not in url:
            domain = url
        return {
            "kind": "scrape",
            "label": "scraping web",
            "detail": url or "querying web search",
            "badge": domain,
            "status": "running",
            "is_risk": False,
        }

    # 7. Type checking
    if "check_types" in t_lower or "typecheck" in t_lower or "lint" in t_lower or "tsc" in cmd or "eslint" in cmd:
        return {
            "kind": "typecheck",
            "label": "type checking",
            "detail": path or "TypeScript & AST verification",
            "badge": "tsc",
            "status": "running",
            "is_risk": False,
        }

    # 8. Opening file
    if "read" in t_lower or "list" in t_lower:
        return {
            "kind": "file",
            "label": "opening file",
            "detail": path or (f"{len(args.get('files', []))} files" if args.get("files") else "workspace files"),
            "badge": path.split("/")[-1] if path else None,
            "status": "running",
            "is_risk": False,
        }

    # 9. Running command
    if cmd or "command" in t_lower or "bash" in t_lower or "shell" in t_lower:
        return {
            "kind": "command",
            "label": "running command",
            "detail": cmd,
            "badge": None,
            "status": "running",
            "is_risk": False,
        }

    # Default file or edit action
    if path or "write" in t_lower or "edit" in t_lower or "file" in t_lower:
        return {
            "kind": "file",
            "label": "opening file" if "read" in t_lower else "editing file",
            "detail": path,
            "badge": path.split("/")[-1] if path else None,
            "status": "running",
            "is_risk": False,
        }

    return {
        "kind": "command",
        "label": "running command",
        "detail": tool_name.replace("syte_", "").replace("_", " "),
        "badge": None,
        "status": "running",
        "is_risk": False,
    }


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
        request_id: Optional[str] = None,
        credentials: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute a full autonomous agent turn with streaming output and tool execution."""
        # One request id per turn ties every event (deltas, tool calls,
        # lifecycle) to this run so clients can group/deduplicate streams.
        request_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
        turn_started = time.monotonic()

        if credentials and self.session:
            self.session.credentials = credentials

        # 1. Save incoming user message
        await save_ai_chat_message(self.project_id, role="user", content=user_message)
        yield {"event": "user_message_received", "content": user_message, "request_id": request_id}

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

            # 2. Gather Deep Focus (Project Memory)
            from syte.ai.deep_focus import build_deep_focus_index, format_deep_focus_for_prompt
            from syte.database import get_project_deep_focus
            stored_df = await get_project_deep_focus(self.project_id)
            custom_mem = stored_df.get("custom_memory", "") if stored_df else ""
            deep_focus = await build_deep_focus_index(self.project_id, ws_dir=ws_dir, custom_memory=custom_mem)
            deep_focus_prompt = format_deep_focus_for_prompt(deep_focus)

            # 3. Gather Currently Loaded / Uploaded Files
            from syte.database import list_project_uploaded_files
            ups = await list_project_uploaded_files(self.project_id)
            uploaded_files_prompt = ""
            if ups:
                up_lines = [
                    "\n--- CURRENTLY LOADED / UPLOADED PROJECT FILES ---",
                    "The user has loaded the following files into the workspace uploads directory (workspace/uploads/):",
                ]
                for u in ups[:12]:
                    up_lines.append(f"- **`{u['file_path']}`** ({u['extension'] or 'file'}, {u['file_size']} bytes): {u['summary'] or u['filename']}")
                    if u.get("parsed_content") and len(u["parsed_content"]) < 1200:
                        up_lines.append(f"  *Preview*:\n```\n{u['parsed_content']}\n```")
                up_lines.append("You have full autonomous access to inspect these files with `syte_read_file` or use their data directly.")
                up_lines.append("--------------------------------------------------\n")
                uploaded_files_prompt = "\n".join(up_lines)

            # 4. Gather Active Skills by Responsibility
            from syte.ai.skills import format_project_skills_for_prompt
            skills_prompt = await format_project_skills_for_prompt(self.project_id)

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
                f"{deep_focus_prompt}\n"
                f"{uploaded_files_prompt}\n"
                f"{skills_prompt}\n"
                f"Capabilities: You have full autonomous tools to manage this project workspace on the host VM: read/write/edit/move/delete/search files, execute shell bash commands, stage and commit git changes, push/pull branches, query the logged-in GitHub account, view real-time router/deployment logs, and trigger zero-downtime deployments.\n"
                f"------------------------------------\n"
            )

        # 3. Load AI Builder settings
        ai_settings = await get_ai_builder_settings(self.project_id)
        if settings_override:
            # If client passed model_profile or modelProfile, map to model
            if "model_profile" in settings_override and "model" not in settings_override:
                settings_override["model"] = settings_override["model_profile"]
            elif "modelProfile" in settings_override and "model" not in settings_override:
                settings_override["model"] = settings_override["modelProfile"]

            target_model = settings_override.get("model")
            if target_model and target_model != ai_settings.get("model"):
                # If target model matches a specific saved provider, switch provider config
                saved_providers = ai_settings.get("saved_providers") or []
                matched_sp = None
                for sp in saved_providers:
                    if not isinstance(sp, dict):
                        continue
                    models_sub = sp.get("models_list") or ([sp.get("model")] if sp.get("model") else [])
                    if target_model in models_sub or sp.get("model") == target_model:
                        matched_sp = sp
                        break

                # If not matched in project's saved_providers, check global saved_providers
                if not matched_sp and self.project_id != "global":
                    global_settings = await get_ai_builder_settings("global")
                    for sp in (global_settings.get("saved_providers") or []):
                        if not isinstance(sp, dict):
                            continue
                        models_sub = sp.get("models_list") or ([sp.get("model")] if sp.get("model") else [])
                        if target_model in models_sub or sp.get("model") == target_model:
                            matched_sp = sp
                            break

                if matched_sp:
                    if matched_sp.get("provider") and "provider" not in settings_override:
                        settings_override["provider"] = matched_sp["provider"]
                    if matched_sp.get("api_key") and "api_key" not in settings_override:
                        settings_override["api_key"] = matched_sp["api_key"]
                    if "base_url" not in settings_override:
                        settings_override["base_url"] = matched_sp.get("base_url", "")
                else:
                    # Infer provider from model name to prevent mismatching to custom proxies
                    from syte.ai.providers import infer_provider_for_model
                    inferred_p = infer_provider_for_model(target_model)
                    if inferred_p:
                        settings_override["provider"] = inferred_p
                        if "base_url" not in settings_override:
                            settings_override["base_url"] = ""

            ai_settings.update(settings_override)

        client = UnifiedAIClient(
            provider=ai_settings.get("provider", "openai"),
            model=ai_settings.get("model", "gpt-4o"),
            api_key=ai_settings.get("api_key", ""),
            base_url=ai_settings.get("base_url", ""),
            temperature=float(ai_settings.get("temperature", 0.7)),
            max_tokens=int(ai_settings.get("max_tokens", 4096)),
            thinking_level=ai_settings.get("thinking_level", "medium"),
            gcp_project=ai_settings.get("gcp_project", ""),
            gcp_location=ai_settings.get("gcp_location", "us-central1"),
        )

        # 4. Assemble system prompt with live project context & workflow rules
        base_prompt = ai_settings.get("system_prompt") or ""
        autonomous_instructions = (
            "\n\n--- SYTE AUTONOMOUS AGENT CORE ARCHITECTURE & EXECUTION STANDARDS ---\n"
            "You are the Syte Autonomous AI Builder & Principal Site Architect — an elite autonomous AI engineer embedded directly in the Syte platform, operating at the quality bar of v0, Google Cloud Code, and Antigravity.\n\n"
            "## 1. USER INTENT CLASSIFICATION & SCOPE DISCIPLINE (PRIMARY DIRECTIVE)\n"
            "- **CRITICAL RULE: ONLY DO WHAT THE USER ASKS YOU TO DO.**\n"
            "- **INSPECTION & CHECK REQUESTS (e.g., 'check integration', 'inspect auth', 'how does database connect?', 'status', 'test', 'review code')**:\n"
            "  - The user is asking for an evaluation, diagnosis, or explanation — NOT an unprompted rewrite.\n"
            "  - Inspect the codebase thoroughly using read and search tools (`syte_read_file`, `syte_search_files`, `syte_git_status`, `syte_mcp_list`, `syte_list_uploaded_files`).\n"
            "  - Deliver a direct, factual explanation and status report of the findings.\n"
            "  - **DO NOT** edit files, do NOT create files, do NOT scaffold new code, and do NOT install packages unless the user explicitly asked to change or build something.\n"
            "- **BUILD & CHANGE REQUESTS (e.g., 'build a new feature', 'add page', 'fix bug', 'integrate Stripe', 'refactor')**:\n"
            "  - Follow the strict PLAN -> BUILD -> VERIFY discipline.\n"
            "  - Formulate an implementation plan, follow the active skills for Designing, Integrating, and Building, and verify before delivering.\n\n"
            "## 2. ENVIRONMENT & TOOL MASTERY\n"
            "You have complete command of the workspace environment (`/var/lib/syte/workspaces/<uuid>/app`):\n"
            "- **File Inspection**: `syte_read_file` (view file contents), `syte_read_file_lines` (slice lines), `syte_search_files` (grep pattern), `syte_list_files` (directory tree).\n"
            "- **Uploaded Context**: `syte_list_uploaded_files` and `syte_read_uploaded_file` inspect user-uploaded blueprints, schemas, and documents.\n"
            "- **Code Modification**: `syte_write_file` (create/overwrite complete production code), `syte_edit_file` (surgical string replacement), `syte_delete_file`, `syte_rename_file`.\n"
            "- **Terminal & Packages**: `syte_run_command` (bash execution), `syte_install_package` (npm/pip dependency installation).\n"
            "- **Git & GitHub**: `syte_git_status`, `syte_git_diff`, `syte_git_commit`, `syte_git_log`, `syte_git_clone`. Your user's GitHub credentials, author name, and author email are injected automatically into git commands.\n"
            "- **Interactive User Alignment**: `syte_ask_question` presents interactive question cards (single-choice, multi-choice, or free text). Use this when user preferences, design trade-offs, or secret keys are needed. The agent will pause and wait for the user's answer.\n"
            "- **MCP Ecosystem**: `syte_mcp_list` and `syte_mcp_call` interact with registered MCP servers and external connections.\n"
            "- **Quality & Verification**: `syte_security_lint_scan` (AST security and syntax check), `syte_check_types` (TypeScript verification), `syte_start_preview` (dev preview server).\n\n"
            "## 3. PROFESSIONAL DESIGN & UI/UX STANDARDS (v0 / Antigravity Standard)\n"
            "- **Typography**: Modern font stack (Inter, Geist Sans, system UI). Strict hierarchy: Display H1 (tight tracking `-0.03em`), Section H2, Card H3, muted lead copy, and crisp caption badges.\n"
            "- **Color & Styling**: Tailwind CSS / modern CSS. Zinc/Slate neutral scale, glassmorphism (`backdrop-blur-md bg-white/80 border border-zinc-200/60`), vibrant accent colors (Indigo `#6366f1`, Sky `#0284c7`, Emerald `#10b981`).\n"
            "- **Component Library**: Use modern styled components (Cards, Pills, Action Buttons, Badges, Hero banners, Feature grids, Responsive navbar with mobile sheet) and clean icons.\n"
            "- **Zero-Placeholder Guarantee**: When modifying code, ALWAYS write complete, production-ready code. Never leave `// TODO`, `/* implement later */`, or truncated mock functions.\n"
            "- **Responsive**: Mobile-first fluid layouts (`grid-cols-1 md:grid-cols-2 lg:grid-cols-3`), touch targets >= 44px, zero horizontal overflow.\n\n"
            "## 4. STRICT PHASE PROGRESSION: PLAN -> BUILD -> VERIFY (FOR BUILD REQUESTS)\n"
            "1. **PLAN PHASE (Mandatory for non-trivial changes)**:\n"
            "   - Before executing code edits, formulate an explicit implementation plan using `syte_create_plan`.\n"
            "   - Consult active skills by responsibility (Designing, Integrating, Building).\n"
            "   - If requirements or choices are ambiguous, call `syte_ask_question` to align with the user.\n"
            "2. **BUILD PHASE**:\n"
            "   - Follow the plan step-by-step, updating plan step status with `syte_update_plan_step`.\n"
            "   - Incorporate any uploaded files in `uploads/` (`syte_read_file`).\n"
            "3. **VERIFY PHASE**:\n"
            "   - Run AST security/syntax check (`syte_security_lint_scan`) and verify the dev server.\n"
            "4. **DELIVER**:\n"
            "   - Return a concise, direct, professional summary of the answer or changes directly to the user.\n"
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

        # 6. Execution loop
        max_turns = 120
        current_turn = 0
        final_response_text = ""

        while current_turn < max_turns:
            current_turn += 1
            turn_tokens = ""
            turn_thoughts = ""
            turn_tool_calls: List[Dict[str, Any]] = []

            yield {"event": "status", "message": f"Thinking with {client.model}…", "turn": current_turn, "request_id": request_id}
            yield {"event": "is_working", "is_working": True, "activity": f"Thinking with {client.model}…", "turn": current_turn, "request_id": request_id}

            stream_error = None
            thinking_closed = False
            async for chunk in client.stream_chat(
                formatted_messages,
                tools=tools_schema,
                system_prompt=full_system_prompt,
            ):
                chunk_type = chunk.get("type")
                if chunk_type == "thought":
                    content = chunk.get("content", "")
                    turn_thoughts += content
                    yield {
                        "event": "thought_delta",
                        "delta": content,
                        "request_id": request_id,
                        "turn": current_turn,
                        "is_new_thinking": current_turn > 1,
                    }
                elif chunk_type == "token":
                    if turn_thoughts and not thinking_closed:
                        thinking_closed = True
                        yield {
                            "event": "thinking_finished",
                            "thought": turn_thoughts,
                            "turn": current_turn,
                            "is_new_thinking": current_turn > 1,
                            "request_id": request_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    content = chunk.get("content", "")
                    turn_tokens += content
                    yield {"event": "token_delta", "delta": content, "request_id": request_id, "turn": current_turn}
                elif chunk_type == "tool_call":
                    if turn_thoughts and not thinking_closed:
                        thinking_closed = True
                        yield {
                            "event": "thinking_finished",
                            "thought": turn_thoughts,
                            "turn": current_turn,
                            "is_new_thinking": current_turn > 1,
                            "request_id": request_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    turn_tool_calls.append(chunk)
                elif chunk_type == "error":
                    err_msg = chunk.get("content", "LLM communication error")
                    if any(t in err_msg.lower() for t in ["timed out", "timeout", "504", "502", "503", "connection reset"]) and current_turn < max_turns:
                        stream_error = err_msg
                        break
                    yield {"event": "error", "error": err_msg, "request_id": request_id}
                    yield {"event": "error_log", "level": "error", "message": err_msg, "source": "llm_stream", "turn": current_turn, "request_id": request_id}
                    yield {"event": "is_working", "is_working": False, "activity": "error", "turn": current_turn, "request_id": request_id}
                    return

            if stream_error:
                yield {
                    "event": "status",
                    "message": f"Gateway timeout, retrying turn {current_turn}…",
                    "turn": current_turn,
                    "request_id": request_id,
                }
                yield {
                    "event": "error_log",
                    "level": "warning",
                    "message": f"Gateway timeout on turn {current_turn}, retrying…",
                    "source": "llm_retry",
                    "turn": current_turn,
                    "request_id": request_id,
                }
                await asyncio.sleep(2)
                current_turn -= 1
                continue

            final_response_text += turn_tokens

            # Extract tool calls from text if native function calling chunks were empty
            if not turn_tool_calls:
                parsed_calls = extract_text_tool_calls(turn_tokens)
                if parsed_calls:
                    turn_tool_calls = parsed_calls

            # Calculate tokens and deduct from user credit balance ($5.00 starter)
            prompt_chars = sum(len(str(m.get("content") or "")) for m in formatted_messages)
            prompt_tokens_est = max(1, prompt_chars // 4)
            completion_tokens_est = max(1, len(turn_tokens) // 4)

            # Fetch model pricing from Omni catalog
            model_info = await get_omni_model(ai_settings.get("model", "gemini-2.5-flash"))
            in_cost_per_m = model_info["input_cost"] if model_info else 0.15
            out_cost_per_m = model_info["output_cost"] if model_info else 0.60
            turn_cost = (prompt_tokens_est * in_cost_per_m / 1_000_000.0) + (completion_tokens_est * out_cost_per_m / 1_000_000.0)

            updated_credits = await deduct_user_credits(
                user_id="default_user",
                project_id=self.project_id,
                provider=ai_settings.get("provider", "google"),
                model=ai_settings.get("model", "gemini-2.5-flash"),
                prompt_tokens=prompt_tokens_est,
                completion_tokens=completion_tokens_est,
                cost_usd=turn_cost,
            )

            # If no tool calls were requested in this turn: the agent has finished answering the user's message!
            if not turn_tool_calls:
                # Save final response and emit done
                await save_ai_chat_message(self.project_id, role="assistant", content=turn_tokens)
                yield {
                    "event": "credits_update",
                    "credits": updated_credits,
                    "cost_usd": turn_cost,
                    "prompt_tokens": prompt_tokens_est,
                    "completion_tokens": completion_tokens_est,
                    "total_tokens": prompt_tokens_est + completion_tokens_est,
                    "request_id": request_id,
                }
                yield {
                    "event": "done",
                    "reply": turn_tokens,
                    "request_id": request_id,
                    "turn": current_turn,
                    "turn_duration_ms": int((time.monotonic() - turn_started) * 1000),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield {"event": "is_working", "is_working": False, "activity": "idle", "turn": current_turn, "request_id": request_id}
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

                # Generate precise human-readable status marker
                status_msg = f"Executing {tool_name}…"
                if tool_name == "syte_write_file" and file_target:
                    status_msg = f"Creating file: {file_target}…"
                elif tool_name == "syte_edit_file" and file_target:
                    status_msg = f"Editing file: {file_target}…"
                elif tool_name == "syte_read_file" and file_target:
                    status_msg = f"Inspecting file: {file_target}…"
                elif tool_name == "syte_read_file_lines" and file_target:
                    status_msg = f"Reading lines from: {file_target}…"
                elif tool_name == "syte_search_files":
                    status_msg = f"Searching files for '{args.get('query', '')}'…"
                elif tool_name in ("syte_list_files", "syte_list_workspace_files"):
                    status_msg = "Listing workspace directory tree…"
                elif tool_name == "syte_run_command" and cmd_target:
                    status_msg = f"Running terminal: {cmd_target}…"
                elif tool_name == "syte_start_preview":
                    status_msg = "Starting hot-reloading preview dev server…"
                elif tool_name == "syte_security_lint_scan":
                    status_msg = "Scanning AST security and syntax across workspace…"
                elif tool_name == "syte_discover_skills":
                    status_msg = f"Browsing skill capabilities in {args.get('category') or 'all categories'}…"
                elif tool_name == "syte_load_skill":
                    status_msg = f"Loading domain skill blueprint: {args.get('skill_name', '')}…"
                elif tool_name == "syte_create_plan":
                    status_msg = f"Creating implementation plan: '{args.get('title', '')}'…"
                elif tool_name == "syte_update_plan_step":
                    status_msg = f"Updating plan step {args.get('step_id', '')} -> {args.get('status', '')}…"

                mark = _determine_action_mark(tool_name, args)
                yield {
                    "event": "action_mark",
                    "action_mark": mark,
                    "tool_name": tool_name,
                    "arguments": _brief_arguments(args),
                    "file_path": file_target,
                    "command": cmd_target,
                    "request_id": request_id,
                    "turn": current_turn,
                    "timestamp": now_stamp,
                }

                yield {
                    "event": "status",
                    "message": status_msg,
                    "tool_name": tool_name,
                    "file_path": file_target,
                    "command": cmd_target,
                    "request_id": request_id,
                    "turn": current_turn,
                    "timestamp": now_stamp,
                }
                yield {
                    "event": "is_working",
                    "is_working": True,
                    "activity": status_msg,
                    "tool_name": tool_name,
                    "file_path": file_target,
                    "command": cmd_target,
                    "turn": current_turn,
                    "request_id": request_id,
                }

                yield {
                    "event": "tool_call_start",
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": _brief_arguments(args),
                    "file_path": file_target,
                    "command": cmd_target,
                    "message": status_msg,
                    "request_id": request_id,
                    "turn": current_turn,
                    "timestamp": now_stamp,
                }

                if tool_name == "syte_run_command" and cmd_target:
                    yield {
                        "event": "command_start",
                        "command": cmd_target,
                        "cwd": str(args.get("cwd") or "app"),
                        "turn": current_turn,
                        "request_id": request_id,
                        "timestamp": now_stamp,
                    }

                # Execute tool
                tool_exec_started = time.monotonic()
                tool_result = await execute_syte_tool(self.project_id, tool_name, args, session=self.session, credentials=credentials)
                tool_duration_ms = int((time.monotonic() - tool_exec_started) * 1000)

                # Check if tool flagged a security risk
                if tool_result.get("security_risk") or (not mark.get("is_risk") and "security risk" in str(tool_result.get("error", "")).lower()):
                    risk_mark = {
                        "kind": "security_risk",
                        "label": "security risk!",
                        "detail": str(tool_result.get("security_risk") or tool_result.get("error") or "Security violation flagged"),
                        "badge": None,
                        "status": "warning",
                        "is_risk": True,
                    }
                    yield {
                        "event": "action_mark",
                        "action_mark": risk_mark,
                        "tool_name": tool_name,
                        "turn": current_turn,
                        "request_id": request_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                # Check if tool requires interactive user response (questions / env secrets)
                if tool_result.get("requires_user_input") and self.session:
                    yield {
                        "event": "tool_call_result",
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "result": tool_result,
                        "file_path": file_target,
                        "command": cmd_target,
                        "duration_ms": tool_duration_ms,
                        "request_id": request_id,
                        "turn": current_turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    q_id = str(tool_result.get("question_id") or tool_result.get("id") or f"q_{uuid.uuid4().hex[:8]}")
                    q_prompt = tool_result.get("question") or tool_result.get("prompt") or status_msg
                    q_options = tool_result.get("options") or []
                    q_type = "choice" if q_options else tool_result.get("type", "input")
                    q_obj = {
                        "id": q_id,
                        "question_id": q_id,
                        "prompt": q_prompt,
                        "question": q_prompt,
                        "options": q_options,
                        "question_type": q_type,
                        "allow_custom": tool_result.get("allow_custom", True),
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                    }
                    yield {
                        "event": "question",
                        "event_type": "question",
                        "question": q_obj,
                        "question_id": q_id,
                        "prompt": q_prompt,
                        "options": q_options,
                        "question_type": q_type,
                        "allow_custom": tool_result.get("allow_custom", True),
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                        "turn": current_turn,
                        "request_id": request_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    yield {
                        "event": "ask_question",
                        "question": q_obj,
                        "question_id": q_id,
                        "prompt": q_prompt,
                        "options": q_options,
                        "question_type": q_type,
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                        "turn": current_turn,
                        "request_id": request_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    yield {
                        "event": "waiting_for_user_input",
                        "question_id": q_id,
                        "question": q_obj,
                        "turn": current_turn,
                        "request_id": request_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    user_resp = await self.session.wait_for_user_answer(q_obj)
                    tool_result = {**tool_result, "user_response": user_resp, "status": "answered"}
                    user_ans_val = user_resp.get("answer") if isinstance(user_resp, dict) else user_resp
                    yield {
                        "event": "question_answered",
                        "event_type": "question_answered",
                        "question": {**q_obj, "answer": user_ans_val, "status": "answered"},
                        "question_id": q_id,
                        "answer": user_ans_val,
                        "tool_call_id": call_id,
                        "turn": current_turn,
                        "request_id": request_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    yield {
                        "event": "user_input_received",
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "user_response": user_resp,
                        "request_id": request_id,
                        "turn": current_turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                # If a shell command ran, emit command_end
                if tool_name == "syte_run_command" and cmd_target:
                    yield {
                        "event": "command_end",
                        "command": cmd_target,
                        "exit_code": tool_result.get("exit_code", 0 if tool_result.get("ok") else 1),
                        "duration_ms": tool_duration_ms,
                        "output": str(tool_result.get("stdout") or tool_result.get("output") or "")[:2000],
                        "turn": current_turn,
                        "request_id": request_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                # If tool failed or produced error, emit error_log event
                if not tool_result.get("ok") or tool_result.get("error"):
                    err_text = str(tool_result.get("error") or tool_result.get("stderr") or "Tool failed")
                    yield {
                        "event": "error_log",
                        "level": "error" if not tool_result.get("ok") else "warning",
                        "message": err_text,
                        "source": tool_name,
                        "context": {"tool_name": tool_name, "file_path": file_target, "command": cmd_target},
                        "turn": current_turn,
                        "request_id": request_id,
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
                        "result": _brief_stream_result(tool_result),
                        "file_path": file_target,
                        "command": cmd_target,
                        "duration_ms": tool_duration_ms,
                        "ok": bool(tool_result.get("ok", True)),
                        "request_id": request_id,
                        "turn": current_turn,
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
