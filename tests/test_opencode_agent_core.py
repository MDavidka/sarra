from __future__ import annotations

import asyncio

from syte.agent_turn_controls import normalize_turn_controls
from syte.cloud_agent import (
    _execute_tool,
    _is_deliverable_web_source_path,
    _is_webshop_feature_complete,
    _parse_text_patch_protocol,
)
from syte.site_planner import (
    is_source_change_request,
    is_substantive_site_request,
    is_website_request,
)
from syte.opencode_agent_core import (
    agent_core_spec,
    build_agent_task_spec,
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


def test_webshop_prompts_are_substantive_website_work() -> None:
    prompt = "Create a responsive webshop with a product listing, cart, and checkout."
    assert is_website_request(prompt)
    assert is_substantive_site_request(prompt)


def test_follow_up_source_change_requests_are_classified() -> None:
    assert is_source_change_request("Add a dark mode toggle to the webshop.")
    assert is_source_change_request("Fix the checkout total formatting.")
    assert not is_source_change_request("What is dark mode?")


def test_webshop_delivery_requires_core_feature_flows() -> None:
    assert not _is_webshop_feature_complete("<main><h1>Welcome</h1></main>")
    assert _is_webshop_feature_complete("product listing, cart drawer, checkout form")


def test_webshop_delivery_requires_an_actual_ui_source_path() -> None:
    assert not _is_deliverable_web_source_path("syra/memory.md")
    assert not _is_deliverable_web_source_path("app/package.json")
    assert _is_deliverable_web_source_path("app/app/page.tsx")
    assert _is_deliverable_web_source_path("app/src/App.tsx")
    assert _is_deliverable_web_source_path("app/index.html")


def test_follow_up_change_blocks_metadata_before_application_source() -> None:
    result = asyncio.run(
        _execute_tool(
            "unused",
            "write_file",
            {"path": "syra/memory.md", "content": "note"},
            context={
                "completion_write_required": True,
                "source_change_required": True,
                "_delivery_requirements_complete": False,
            },
        )
    )
    assert result["error"] == "webshop_ui_source_required"
    assert result["retryable"] is True


def test_follow_up_change_requires_write_after_one_inspection() -> None:
    result = asyncio.run(
        _execute_tool(
            "unused",
            "search_code",
            {"query": "dark mode"},
            context={
                "completion_write_required": True,
                "source_change_required": True,
                "_delivery_requirements_complete": False,
                "_prewrite_inspections": 1,
            },
        )
    )
    assert result["error"] == "source_write_required"
    assert result["retryable"] is True


def test_fresh_webshop_blocks_setup_files_before_ui_source() -> None:
    result = asyncio.run(
        _execute_tool(
            "unused",
            "write_file",
            {"path": "app/package.json", "content": "{}"},
            context={
                "build_artifact_required": True,
                "webshop_requirements": True,
                "_delivery_requirements_complete": False,
            },
        )
    )
    assert result["error"] == "webshop_ui_source_required"
    assert result["retryable"] is True


def test_build_request_requires_write_after_two_inspections() -> None:
    result = asyncio.run(
        _execute_tool(
            "unused",
            "list_mcp_addons",
            {},
            context={"build_artifact_required": True, "_prewrite_inspections": 2},
        )
    )
    assert result["error"] == "source_write_required"
    assert result["retryable"] is True


def test_build_request_blocks_shell_before_first_source_change() -> None:
    result = asyncio.run(
        _execute_tool(
            "unused",
            "run_command",
            {"command": "ls -la"},
            context={"build_artifact_required": True},
        )
    )
    assert result["error"] == "prewrite_tool_not_allowed"
    assert result["retryable"] is True


def test_short_greeting_requests_are_classified_as_tool_free_conversation() -> None:
    assert is_simple_conversation("Say hello in Romanian, English, and Hungarian.")
    assert is_simple_conversation("Please translate hello into Hungarian.")
    assert not is_simple_conversation("Create a webshop with a product page.")
    assert not is_simple_conversation("Fix the hello message component in my app.")


def test_text_patch_protocol_only_accepts_bounded_application_source_replacements() -> None:
    payload = '''{"patches":[{"path":"app/index.html","find":"</body>","replace":"<script>ok()</script></body>"}]}'''
    assert _parse_text_patch_protocol(payload) == [
        {"path": "app/index.html", "find": "</body>", "replace": "<script>ok()</script></body>"}
    ]
    assert not _parse_text_patch_protocol('{"patches":[{"path":"syra/memory.md","find":"x","replace":"y"}]}')
    assert not _parse_text_patch_protocol("write a patch please")


def test_follow_up_task_contract_is_sandbox_aware_and_write_oriented() -> None:
    contract = build_agent_task_spec(
        normalize_agent_execution_policy("build", 8),
        source_change_required=True,
    )

    assert contract.kind == "follow-up source edit"
    assert contract.app_root == "app/"
    assert contract.permitted_prewrite_tools == ("read_file", "write_file")
    assert "actual application source file under app/" in contract.completion_condition
    prompt = contract.prompt_block()
    assert "isolated project workspace" in prompt
    assert "Do not use absolute paths" in prompt
    assert "memory, plans, package files" in prompt


def test_turn_controls_persist_core_mode_without_provider_changes() -> None:
    controls = normalize_turn_controls(agent_mode="plan", max_steps=5)
    assert controls["agent_mode"] == "plan"
    assert controls["max_steps"] == 4
    spec = agent_core_spec()
    assert spec["agent_modes"] == ["build", "plan"]
    assert 8 in spec["max_steps"]
    assert "small_model_strategy" in spec["execution_contract"]
