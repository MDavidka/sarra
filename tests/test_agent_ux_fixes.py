"""Tests for Vertex thought signatures, cost estimates, and project memory file."""

from __future__ import annotations

from pathlib import Path

import pytest

from syte.cloud_agent import _estimate_turn_cost
from syte.gemini_native import (
    gemini_response_to_openai_message,
    openai_messages_to_gemini,
)
from syte.project_memory_file import (
    MEMORY_REL_PATH,
    ensure_project_memory_md,
    project_memory_md_prompt_block,
    read_project_memory_md,
)


def test_gemini_preserves_thought_signature_roundtrip() -> None:
    message = gemini_response_to_openai_message({
        "candidates": [{
            "content": {
                "parts": [{
                    "functionCall": {
                        "name": "ask_question",
                        "args": {"prompt": "Remember me?", "question_type": "choice"},
                    },
                    "thoughtSignature": "sig-abc-123",
                }]
            }
        }],
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 20,
            "totalTokenCount": 130,
            "thoughtsTokenCount": 10,
        },
    })
    assert message["tool_calls"][0]["thought_signature"] == "sig-abc-123"
    assert message["_vertex_parts"][0]["thoughtSignature"] == "sig-abc-123"
    assert message["_usage"]["input_tokens"] == 100
    assert message["_usage"]["thinking_tokens"] == 10

    _, contents = openai_messages_to_gemini([
        {"role": "user", "content": "hi"},
        message,
        {
            "role": "tool",
            "tool_call_id": message["tool_calls"][0]["id"],
            "content": '{"ok": true, "answer": "yes"}',
        },
    ])
    model_parts = contents[1]["parts"]
    assert model_parts[0]["thoughtSignature"] == "sig-abc-123"
    assert model_parts[0]["functionCall"]["name"] == "ask_question"


def test_gemini_extracts_thought_text_as_reasoning() -> None:
    message = gemini_response_to_openai_message({
        "candidates": [{
            "content": {
                "parts": [
                    {"text": "I should ask first", "thought": True},
                    {"text": "What brand?"},
                ]
            }
        }]
    })
    assert message["reasoning_content"] == "I should ask first"
    assert message["content"] == "What brand?"


def test_estimate_turn_cost_uses_catalog_prices() -> None:
    cost = _estimate_turn_cost(
        {
            "profile": "syra-nano",
            "model": "gemini-3.1-flash-lite",
            "input_price_per_mtok": 0.25,
            "output_price_per_mtok": 1.50,
        },
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "thinking_tokens": 0},
    )
    assert cost["cost_usd"] == pytest.approx(1.75)
    assert "$" in cost["label"]


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from syte import config as config_mod

    monkeypatch.setattr(config_mod.settings, "workspaces_dir", tmp_path)
    return tmp_path


def test_project_memory_md_created_and_prompted(tmp_workspace: Path) -> None:
    project_id = "proj-mem-1"
    result = ensure_project_memory_md(project_id)
    assert result["created"] is True
    assert result["path"] == MEMORY_REL_PATH
    text = read_project_memory_md(project_id)
    assert "Project memory" in text
    block = project_memory_md_prompt_block(project_id)
    assert MEMORY_REL_PATH in block
    assert "Project memory" in block

    # Second ensure does not overwrite.
    again = ensure_project_memory_md(project_id)
    assert again["created"] is False
