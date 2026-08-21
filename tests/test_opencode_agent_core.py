from __future__ import annotations

import asyncio

from syte.agent_turn_controls import normalize_turn_controls
from syte.cloud_agent import _execute_tool
from syte.opencode_agent_core import (
    agent_core_spec,
    is_simple_conversation,
    normalize_agent_execution_policy,
    policy_prompt_block,
)


def test_build_and_plan_modes_have_explicit_permissions_and_bounded_steps() -> None:
    build = normalize_agent_execution_policy("build", 11)
    plan = normalize_agent_execution_policy("plan", 15)

    assert build.mode == "build"
    assert build.max_steps == 12
    assert build.allows_tool("write_file", {"path": "app/page.tsx"})
    assert plan.mode == "plan"
    assert plan.max_steps == 16
    assert plan.allows_tool("read_file", {"path": "app/package.json"})
    assert plan.allows_tool("service", {"action": "status"})
    assert not plan.allows_tool("write_file", {"path": "app/page.tsx"})
    assert not plan.allows_tool("run_command", {"command": "npm run build"})
    assert "read-only" in policy_prompt_block(plan).lower()


def test_plan_mode_blocks_legacy_mutating_dispatch_before_workspace_access() -> None:
    policy = normalize_agent_execution_policy("plan", 8)
    result = asyncio.run(
        _execute_tool(
            "unused",
            "write_file",
            {"path": "app/page.tsx", "content": "unsafe"},
            context={"agent_policy": policy},
        )
    )
    assert result["error"] == "plan_mode_permission_denied"
    assert result["retryable"] is False


def test_short_greeting_requests_are_classified_as_tool_free_conversation() -> None:
    assert is_simple_conversation("Say hello in Romanian, English, and Hungarian.")
    assert is_simple_conversation("Please translate hello into Hungarian.")
    assert not is_simple_conversation("Create a webshop with a product page.")
    assert not is_simple_conversation("Fix the hello message component in my app.")


def test_turn_controls_persist_core_mode_without_provider_changes() -> None:
    controls = normalize_turn_controls(agent_mode="plan", max_steps=5)
    assert controls["agent_mode"] == "plan"
    assert controls["max_steps"] == 4
    spec = agent_core_spec()
    assert spec["agent_modes"] == ["build", "plan"]
    assert 8 in spec["max_steps"]
