"""Regression: GUI question answer must not double-prefix /api."""

from pathlib import Path


def test_debug_chat_question_answer_path_uses_api_helper_prefix() -> None:
    """``api()`` already prefixes ``/api``; the call path must start at ``/projects``."""
    app_js = Path(__file__).resolve().parents[1] / "syte" / "static" / "app.js"
    text = app_js.read_text(encoding="utf-8")
    assert "async function submitDebugChatQuestionAnswer" in text
    # Bug was: `/api/projects/.../answer` → fetch(`/api` + path) = `/api/api/...` → 404 Not Found
    assert (
        "`/api/projects/${encodeURIComponent(activeServiceId)}/agent/questions/"
        not in text
    )
    assert (
        "`/projects/${encodeURIComponent(activeServiceId)}/agent/questions/"
        "${encodeURIComponent(questionId)}/answer`"
        in text
    )
