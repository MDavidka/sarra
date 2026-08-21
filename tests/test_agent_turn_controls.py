from __future__ import annotations

import asyncio

from syte.agent_turn_controls import (
    normalize_turn_controls,
    requires_plan_before_actions,
    trim_history_to_context_budget,
)
from syte.agent_memory import memory_context_block
from syte.cloud_agent import (
    _execute_tool,
    _is_deployment_oriented_request,
    _is_exploratory_environment_command,
    _request_forbids_command_execution,
    _website_enforcement_block,
)
from syte.main import AgentChatRequest


def test_turn_controls_normalize_to_safe_provider_neutral_choices() -> None:
    controls = normalize_turn_controls(
        context_window_tokens=50_000,
        stream_max_tokens=6_000,
        memory_depth="deep",
        plan_mode="always",
        deployment_readiness=False,
    )
    assert controls["context_window_tokens"] == 64_000
    assert controls["stream_max_tokens"] == 4_096
    assert controls["memory_depth"] == "deep"
    assert controls["history_messages"] == 80
    assert controls["workspace_map_limit"] == 40
    assert controls["plan_mode"] == "always"
    assert controls["deployment_readiness"] is False


def test_auto_plan_protects_substantive_multi_step_work() -> None:
    controls = normalize_turn_controls(plan_mode="auto")
    assert requires_plan_before_actions(
        "Inspect the workspace, implement a responsive dashboard, run tests, then deploy it without errors.",
        controls,
    )
    assert not requires_plan_before_actions("What framework does this project use?", controls)


def test_context_trimming_keeps_system_and_newest_messages() -> None:
    messages = [
        {"role": "system", "content": "trusted system instruction"},
        {"role": "user", "content": "old " * 5_000},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "latest request"},
    ]
    trimmed = trim_history_to_context_budget(
        messages, context_window_tokens=8_000, reserved_output_tokens=4_096
    )
    assert trimmed[0]["role"] == "system"
    assert trimmed[-1]["content"] == "latest request"
    assert not any(message.get("content", "").startswith("old ") for message in trimmed)


def test_memory_depth_limits_are_applied_to_verified_memory() -> None:
    summary = {"summary_text": "x" * 6_000}
    files = [f"app/file-{index}.tsx" for index in range(30)]
    focused = memory_context_block(summary, files, summary_chars=1_600, active_file_limit=6)
    deep = memory_context_block(summary, files, summary_chars=6_000, active_file_limit=24)
    assert len(deep) > len(focused)
    assert "app/file-29.tsx" in deep
    assert "app/file-0.tsx" not in focused


def test_chat_request_exposes_reliability_controls() -> None:
    request = AgentChatRequest(
        message="Inspect and test this project",
        context_window_tokens=64_000,
        stream_max_tokens=8_192,
        memory_depth="deep",
        plan_mode="always",
        deployment_readiness=True,
    )
    assert request.context_window_tokens == 64_000
    assert request.stream_max_tokens == 8_192
    assert request.memory_depth == "deep"
    assert request.plan_mode == "always"


def test_explicit_no_command_request_is_detected_without_blocking_normal_read_only_work() -> None:
    assert _request_forbids_command_execution("Do not run commands or deploy; inspect files only.")
    assert _request_forbids_command_execution("Without executing shell commands, summarize the project.")
    assert not _request_forbids_command_execution("Run the smallest useful lint command after inspection.")


def test_no_command_guard_blocks_shell_and_preview_start_actions() -> None:
    ctx = {"command_execution_forbidden": True}
    shell = asyncio.run(
        _execute_tool("unused", "run_command", {"command": "node -v"}, context=ctx)
    )
    preview = asyncio.run(
        _execute_tool("unused", "service", {"action": "preview_start"}, context=ctx)
    )
    assert shell["error"] == "command_execution_forbidden"
    assert preview["error"] == "command_execution_forbidden"


def test_deployment_requests_block_runtime_probes_and_premature_previews() -> None:
    assert _is_deployment_oriented_request("Make this application deployable on Syte.")
    assert _is_exploratory_environment_command("node -v")
    assert _is_exploratory_environment_command("which npm")
    assert not _is_exploratory_environment_command("npm run build")

    context = {"deployment_oriented": True, "_workspace_changed": False}
    probe = asyncio.run(
        _execute_tool("unused", "run_command", {"command": "node -v"}, context=context)
    )
    preview = asyncio.run(
        _execute_tool("unused", "service", {"action": "preview_start"}, context=context)
    )
    assert probe["error"] == "deployment_targeted_command_required"
    assert preview["error"] == "deployment_change_required"


def test_website_prompt_preserves_existing_framework() -> None:
    prompt = _website_enforcement_block(is_website=True)
    assert "Do not force Vite, HeroUI, Next.js" in prompt
    assert "recommend shadcn/ui" in prompt
    assert "ready to deploy" in prompt
    assert "environment-probing commands" in prompt
    assert "project-declared build" in prompt
