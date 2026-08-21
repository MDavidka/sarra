from __future__ import annotations

from syte.agent_turn_controls import (
    normalize_turn_controls,
    requires_plan_before_actions,
    trim_history_to_context_budget,
)
from syte.agent_memory import memory_context_block
from syte.cloud_agent import _website_enforcement_block
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


def test_website_prompt_preserves_existing_framework() -> None:
    prompt = _website_enforcement_block(is_website=True)
    assert "Do not force Vite, HeroUI, Next.js" in prompt
    assert "recommend shadcn/ui" in prompt
    assert "ready to deploy" in prompt
