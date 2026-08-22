"""Tests for external-agent provider error classification and retry gating."""

from __future__ import annotations

from syte.agent_errors import classify_provider_error, is_retryable_provider_detail


def test_classify_invalid_model() -> None:
    result = classify_provider_error("invalid model provided")
    assert result["matched"] is True
    assert result["error_type"] == "malformed_request"
    assert "model" in result["message"].lower()


def test_classify_rate_limited() -> None:
    result = classify_provider_error("You are being rate limited, try again later")
    assert result["matched"] is True
    assert result["error_type"] == "rate_limited"
    assert "rate limited" in result["message"].lower()


def test_classify_rate_limited_via_429() -> None:
    result = classify_provider_error("RESOURCE_EXHAUSTED", status_code=429)
    assert result["matched"] is True
    assert result["error_type"] == "rate_limited"


def test_classify_invalid_project() -> None:
    result = classify_provider_error("invalid project")
    assert result["matched"] is True
    assert result["error_type"] == "malformed_request"
    assert "project" in result["message"].lower()


def test_classify_model_on_capacity() -> None:
    result = classify_provider_error("model is on capacity for this region")
    assert result["matched"] is True
    assert result["error_type"] == "provider_error"
    assert "capacity" in result["message"].lower()


def test_classify_unmatched_defaults_to_provider_error() -> None:
    result = classify_provider_error("some unrelated upstream error")
    assert result["matched"] is False
    assert result["error_type"] == "provider_error"
    assert result["message"] == ""


def test_retryable_detail_rejects_invalid_model() -> None:
    assert is_retryable_provider_detail(500, "invalid model provided") is False
    assert is_retryable_provider_detail(500, "invalid project") is False


def test_retryable_detail_allows_transient_errors() -> None:
    assert is_retryable_provider_detail(503, "service busy") is True
    assert is_retryable_provider_detail(500, "internal error, retry") is True


def test_retryable_detail_rejects_bad_status() -> None:
    assert is_retryable_provider_detail(400, "bad request") is False


def test_failure_metadata_preserves_offending_value_for_malformed() -> None:
    """A raw provider 'invalid model' error must stream the offending value, not just the code."""
    from syte.cloud_agent import _failure_metadata

    exc = ValueError("invalid model id: xy")
    meta = _failure_metadata(exc)
    assert meta["error_type"] == "malformed_request"
    assert meta["detail"]["raw_error"] == "invalid model id: xy"
    # The user-facing message must still name the value that was rejected.
    assert "xy" in meta["message"]
    assert meta["message"] != "malformed_request"


def test_failure_metadata_preserves_raw_error_for_unmatched() -> None:
    """Unmatched raw exceptions keep their original text in detail + message."""
    from syte.cloud_agent import _failure_metadata

    exc = RuntimeError("runtime crashed")
    meta = _failure_metadata(exc)
    assert meta["error_type"] == "cloud_agent_failed"
    assert meta["detail"]["raw_error"] == "runtime crashed"
    assert "runtime crashed" in meta["message"]


def test_failure_metadata_malformed_agent_error_keeps_specific_message() -> None:
    """Structured MalformedRequestError surfaces its specific message (not a generic hint)."""
    from syte.agent_errors import MalformedRequestError
    from syte.cloud_agent import _failure_metadata

    exc = MalformedRequestError(
        "Invalid model id: xy",
        detail={"profile": "syra-nano"},
    )
    meta = _failure_metadata(exc)
    assert meta["error_type"] == "malformed_request"
    assert "xy" in meta["message"]
    assert meta["detail"]["profile"] == "syra-nano"


def test_incomplete_delivery_is_retryable_and_not_a_success_state() -> None:
    from syte.agent_errors import ToolExecutionError
    from syte.cloud_agent import _failure_metadata

    meta = _failure_metadata(
        ToolExecutionError(
            "Required source was not delivered.",
            error_type="delivery_incomplete",
            retryable=True,
        )
    )

    assert meta["error_type"] == "delivery_incomplete"
    assert meta["retryable"] is True
    assert meta["title"] == "Request failed"
