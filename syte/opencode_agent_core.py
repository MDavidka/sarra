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
STEP_BUDGET_CHOICES = (4, 8, 12, 16)


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
        if self.mode == "build":
            return True
        if tool_name in _READ_ONLY_TOOLS:
            return True
        if tool_name == "service":
            action = str((args or {}).get("action") or "").lower()
            return action in {"status", "logs"}
        return False

    def rejection(self, tool_name: str) -> dict[str, Any]:
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
    except (TypeError, ValueError) as exc:
        raise ValueError("max_steps must be one of: 4, 8, 12, 16") from exc
    return min(STEP_BUDGET_CHOICES, key=lambda candidate: abs(candidate - numeric))


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
        f"You have at most {policy.max_steps} tool rounds; use each one toward a concrete deliverable.\n"
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
    }
