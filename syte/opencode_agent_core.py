"""Compact OpenCode-inspired policy core for Syte Agent turns.

This module deliberately owns only execution policy. Provider selection, credentials,
streaming transport, persistent messages, and existing Syte tools remain in their
established modules so configured OpenAI-compatible, 9Router, and native providers
continue to work unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

AGENT_MODES = ("build", "plan")
# Conversational turns are not capped. Process, timeout, cancellation, and
# provider/resource safeguards remain enforced in their owning layers.
STEP_BUDGET_CHOICES = (4, 8, 12, 16, 32, 64, 128, 256, 512, 1000)


# This guard is deliberately narrow. It protects short greeting / translation
# requests from unnecessary repository exploration without classifying genuine
# implementation questions as chat-only work.
_SIMPLE_CONVERSATION_ACTION = re.compile(
    r"^(?:please\s+)?(?:say|write|translate|greet|reply|respond)\b"
)
_SIMPLE_CONVERSATION_TOPIC = re.compile(
    r"\b(?:hello|hi|greeting|good\s+(?:morning|afternoon|evening)|thank(?:s|\s+you))\b"
)
_WORKSPACE_ACTION_TERMS = re.compile(
    r"\b(?:app|application|website|webshop|project|repo(?:sitory)?|code|file|"
    r"build|create|change|edit|implement|fix|deploy|test|lint|command|terminal|"
    r"database|api|component|page|feature|bug)\b"
)


def is_simple_conversation(message: str | None) -> bool:
    """Identify short greeting / translation-only turns that never need tools."""
    normalized = " ".join(str(message or "").lower().split())
    if not normalized or len(normalized) > 240:
        return False
    if _WORKSPACE_ACTION_TERMS.search(normalized):
        return False
    if normalized in {"hello", "hi", "thanks", "thank you"}:
        return True
    return bool(
        _SIMPLE_CONVERSATION_ACTION.search(normalized)
        and _SIMPLE_CONVERSATION_TOPIC.search(normalized)
    )

# OpenCode keeps subagents out of the selectable default execution lane. Syte
# intentionally runs one user-selected model per session, so these legacy tools
# are never permitted even if a stale provider response mentions them.
_DELEGATION_TOOLS = frozenset({"delegate_task", "await_subagent"})

_READ_ONLY_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        "search_code",
        "semantic_search",
        "inspect_preview",
        "screenshot_preview",
        "ask_question",
        "update_plan",
        "shadcn_registry",
    }
)


@dataclass(frozen=True)
class AgentExecutionPolicy:
    """The small, durable contract for one Agent turn."""

    mode: str
    max_steps: int

    @property
    def allows_edits(self) -> bool:
        return self.mode == "build"

    @property
    def allows_shell(self) -> bool:
        return self.mode == "build"

    def allows_tool(self, tool_name: str, args: dict[str, Any] | None = None) -> bool:
        """Apply an OpenCode-style permission boundary before tool execution."""
        if tool_name in _DELEGATION_TOOLS:
            return False
        if self.mode == "build":
            return True
        if tool_name in _READ_ONLY_TOOLS:
            return True
        if tool_name == "service":
            action = str((args or {}).get("action") or "").lower()
            return action in {"status", "logs"}
        return False

    def rejection(self, tool_name: str) -> dict[str, Any]:
        if tool_name in _DELEGATION_TOOLS:
            return {
                "ok": False,
                "error": "delegation_disabled",
                "retryable": False,
                "message": "Subagent delegation is disabled. Execute the next relevant source action directly.",
            }
        return {
            "ok": False,
            "error": "plan_mode_permission_denied",
            "retryable": False,
            "message": (
                f"Plan mode is read-only and cannot run {tool_name}. "
                "Switch to Build mode to write code, run validation, or start a preview."
            ),
        }


def _nearest_step_budget(value: int | str | None) -> int:
    if value in (None, ""):
        return 16
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 16
    return min(STEP_BUDGET_CHOICES, key=lambda item: abs(item - numeric))


def normalize_agent_execution_policy(
    agent_mode: str | None = None,
    max_steps: int | str | None = None,
) -> AgentExecutionPolicy:
    mode = str(agent_mode or "build").strip().lower()
    if mode not in AGENT_MODES:
        raise ValueError("agent_mode must be one of: build, plan")
    return AgentExecutionPolicy(mode=mode, max_steps=_nearest_step_budget(max_steps))


def policy_prompt_block(policy: AgentExecutionPolicy) -> str:
    if policy.mode == "plan":
        return (
            "## Agent mode: Plan\n"
            "You are in a read-only planning session. Inspect only relevant project files, summarize the "
            "actual stack and deployability gaps, and return an executable implementation plan. Do not edit "
            "files, execute shell commands, start previews, install dependencies, or claim work is complete. "
            "Ask the user to switch to Build mode when the plan is approved.\n"
        )
    return (
        "## Agent mode: Build\n"
        "Complete the requested coding work in the actual project stack. Start with the smallest relevant "
        "file inspection, write the needed source or deployment configuration, then run one targeted declared "
        "validation command after edits. Do not perform runtime-probe commands or unrelated exploration. "
        "Continue until the requested deliverable is complete; process and cancellation safeguards still apply.\n"
    )


def policy_metadata(policy: AgentExecutionPolicy) -> dict[str, Any]:
    return {
        "agent_mode": policy.mode,
        "max_steps": policy.max_steps,
        "permissions": {
            "edit": policy.allows_edits,
            "shell": policy.allows_shell,
        },
    }


@dataclass(frozen=True)
class AgentTaskSpec:
    """A deterministic per-turn contract that reduces model-dependent tool planning."""

    kind: str
    workspace_root: str
    app_root: str
    first_action: str
    permitted_prewrite_tools: tuple[str, ...]
    completion_condition: str

    def prompt_block(self) -> str:
        permitted = ", ".join(self.permitted_prewrite_tools) or "none"
        return (
            "## Deterministic task and sandbox contract\n"
            f"Task kind: {self.kind}.\n"
            f"Sandbox: this is an isolated project workspace. All tool paths are relative to `{self.workspace_root}`; "
            f"application source belongs under `{self.app_root}`. Do not use absolute paths, host paths, or write helper "
            "scripts, memory, plans, package files, or metadata unless the user specifically requested them.\n"
            f"First action: {self.first_action}\n"
            f"Before the first source write, the only permitted tool choices are: {permitted}.\n"
            f"Completion condition: {self.completion_condition}\n"
        )


def build_agent_task_spec(
    policy: AgentExecutionPolicy,
    *,
    simple_conversation: bool = False,
    source_change_required: bool = False,
    site_plan_required: bool = False,
) -> AgentTaskSpec:
    """Return a small-model-friendly execution contract for one isolated Agent turn."""
    if simple_conversation:
        return AgentTaskSpec(
            kind="conversation",
            workspace_root="workspace/",
            app_root="app/",
            first_action="Reply directly without tools.",
            permitted_prewrite_tools=(),
            completion_condition="Answer the user directly; no workspace mutation is required.",
        )
    if policy.mode == "plan":
        return AgentTaskSpec(
            kind="plan",
            workspace_root="workspace/",
            app_root="app/",
            first_action="Inspect one directly relevant source file, then return an executable plan.",
            permitted_prewrite_tools=("list_files", "read_file", "search_code", "update_plan"),
            completion_condition="Return a grounded plan without changing the workspace.",
        )
    if source_change_required:
        return AgentTaskSpec(
            kind="follow-up source edit",
            workspace_root="workspace/",
            app_root="app/",
            first_action="Read one directly relevant application file only if needed; otherwise write the source edit now.",
            permitted_prewrite_tools=("read_file", "write_file"),
            completion_condition="Write the requested change to an actual application source file under app/.",
        )
    if site_plan_required:
        return AgentTaskSpec(
            kind="new application build",
            workspace_root="workspace/",
            app_root="app/",
            first_action="Create the primary application page or component under app/ after the required plan.",
            permitted_prewrite_tools=("update_plan", "list_files", "read_file", "write_file"),
            completion_condition="Write a deployable primary source artifact under app/ before validation or preview.",
        )
    return AgentTaskSpec(
        kind="focused coding task",
        workspace_root="workspace/",
        app_root="app/",
        first_action="Inspect one relevant file, then write the requested source change.",
        permitted_prewrite_tools=("read_file", "write_file"),
        completion_condition="Write the requested change to application source before claiming completion.",
    )


def agent_core_spec() -> dict[str, Any]:
    """Public discovery data for the compact Agent workspace and API clients."""
    return {
        "agent_modes": list(AGENT_MODES),
        "max_steps": list(STEP_BUDGET_CHOICES),
        "default_mode": "build",
        "default_max_steps": 16,
        "mode_summary": {
            "build": "Focused code changes, targeted validation, and isolated preview after edits.",
            "plan": "Read-only stack analysis and implementation planning; writes and shell actions are denied.",
        },
        "execution_contract": {
            "sandbox": "Each turn operates in an isolated workspace; tool paths are relative and application source belongs under app/.",
            "small_model_strategy": "The Agent supplies a deterministic task kind, first action, pre-write tool allowlist, and explicit completion condition.",
            "direct_runner": "One selected model executes one ordered tool lane; delegated and background subagent tools are disabled.",
            "follow_up_edit": "Completed-project changes receive a narrow source-write contract rather than a new-build planning flow.",
        },
    }
