"""Regression tests for Gemini/Vertex HTTP 429 quota pacing and backoff."""

from __future__ import annotations

import pytest

from syte import provider_quota
from syte.ai_providers import NANO_MODEL, PRO_MODEL
from syte.agent_errors import ProviderError
from syte.gemini_native import GoogleApiError, explain_google_api_error


@pytest.fixture(autouse=True)
def _reset_quota_state():
    provider_quota.reset_state()
    yield
    provider_quota.reset_state()


def test_retry_delay_is_truncated_exponential_with_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google guidance: wait in [0, min(max, initial * 2^attempt)] with full jitter."""
    samples = iter([0.0, 0.5, 1.0, 0.25])

    def fake_uniform(lo: float, hi: float) -> float:
        assert lo == 0.0
        return next(samples) * hi

    monkeypatch.setattr(provider_quota.random, "uniform", fake_uniform)
    assert provider_quota.retry_delay(0) == pytest.approx(0.1)  # floored from 0.0
    assert provider_quota.retry_delay(1) == pytest.approx(1.0)  # 0.5 * 2
    assert provider_quota.retry_delay(2) == pytest.approx(4.0)  # 1.0 * 4
    assert provider_quota.retry_delay(10) == pytest.approx(15.0)  # 0.25 * 60 cap


def test_retry_delay_honors_retry_after_with_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_quota.random, "uniform", lambda lo, hi: (lo + hi) / 2)
    delay = provider_quota.retry_delay(0, retry_after=37.0)
    assert delay == pytest.approx(37.0 * 0.75)


def test_parse_retry_after_from_header_and_retryinfo() -> None:
    assert provider_quota.parse_retry_after_seconds({"Retry-After": "12"}) == 12.0
    assert (
        provider_quota.parse_retry_after_seconds(
            None, '{"error":{"details":[{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"37s"}]}}'
        )
        == 37.0
    )


def test_is_quota_detail_for_429_and_resource_exhausted() -> None:
    assert provider_quota.is_quota_detail(429, "whatever")
    assert provider_quota.is_quota_detail(503, "RESOURCE_EXHAUSTED")
    assert provider_quota.is_quota_detail(400, "invalid argument") is False


def test_record_quota_exhausted_escalates_and_rotation_targets_sibling() -> None:
    first = provider_quota.record_quota_exhausted(NANO_MODEL)
    assert first == pytest.approx(20.0)
    assert provider_quota.is_available(NANO_MODEL) is False
    assert provider_quota.next_available_model(NANO_MODEL) == PRO_MODEL

    second = provider_quota.record_quota_exhausted(NANO_MODEL)
    assert second == pytest.approx(40.0)

    provider_quota.record_success(NANO_MODEL)
    assert provider_quota.is_available(NANO_MODEL) is True


def test_record_quota_exhausted_honors_retry_after() -> None:
    cooldown = provider_quota.record_quota_exhausted(PRO_MODEL, retry_after=7.5)
    assert cooldown == pytest.approx(7.5)
    remaining = provider_quota.cooldown_remaining(PRO_MODEL)
    assert 7.0 <= remaining <= 7.5


@pytest.mark.asyncio
async def test_provider_completion_rotates_on_429_without_leaking_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotation must release the acquired model, not the post-rotate active model."""
    from syte import cloud_agent
    from syte.cloud_agent import _provider_completion, close_provider_client

    calls: list[str] = []
    acquire_models: list[str] = []
    release_models: list[str] = []

    async def fake_native(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == NANO_MODEL:
            raise GoogleApiError(
                "quota",
                status_code=429,
                retry_after=1.0,
                detail='{"error":{"status":"RESOURCE_EXHAUSTED"}}',
                quota=True,
            )
        return {"role": "assistant", "content": "ok from pro"}

    async def fake_acquire(model: str) -> None:
        acquire_models.append(model)

    def fake_release(model: str) -> None:
        release_models.append(model)

    async def fake_sleep(_delay: float) -> None:
        return None

    await close_provider_client()
    monkeypatch.setattr(cloud_agent, "_get_provider_client", lambda: object())
    monkeypatch.setattr("syte.cloud_agent.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("syte.gemini_native.native_generate_content", fake_native)
    monkeypatch.setattr("syte.provider_quota.acquire", fake_acquire)
    monkeypatch.setattr("syte.provider_quota.release", fake_release)
    # Keep rotation deterministic: park nano, leave pro available.
    monkeypatch.setattr(
        "syte.provider_quota.record_quota_exhausted",
        lambda model, retry_after=None: 1.0,
    )
    monkeypatch.setattr(
        "syte.provider_quota.next_available_model",
        lambda model: PRO_MODEL if model == NANO_MODEL else None,
    )
    monkeypatch.setattr("syte.provider_quota.record_success", lambda model: None)

    message = await _provider_completion(
        {
            "profile": "syra-nano",
            "provider": "openai",
            "label": "Vertex AI",
            "model": NANO_MODEL,
            "api_key": "AQ.test-key",
            "api_base": "https://aiplatform.googleapis.com/v1",
        },
        [{"role": "user", "content": "hello"}],
    )
    assert message["content"] == "ok from pro"
    assert calls == [NANO_MODEL, PRO_MODEL]
    # Critical: each acquire is paired with a release of the *same* model.
    assert acquire_models == [NANO_MODEL, PRO_MODEL]
    assert release_models == [NANO_MODEL, PRO_MODEL]


@pytest.mark.asyncio
async def test_provider_completion_raises_rate_limited_after_exhausted_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import cloud_agent
    from syte.cloud_agent import _provider_completion, close_provider_client

    async def always_429(**kwargs):
        raise GoogleApiError(
            "quota",
            status_code=429,
            retry_after=2.0,
            detail='{"error":{"status":"RESOURCE_EXHAUSTED"}}',
            quota=True,
        )

    async def fake_acquire(model: str) -> None:
        return None

    def fake_release(model: str) -> None:
        return None

    async def fake_sleep(_delay: float) -> None:
        return None

    await close_provider_client()
    monkeypatch.setattr(cloud_agent, "_get_provider_client", lambda: object())
    monkeypatch.setattr("syte.cloud_agent.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("syte.gemini_native.native_generate_content", always_429)
    monkeypatch.setattr("syte.provider_quota.acquire", fake_acquire)
    monkeypatch.setattr("syte.provider_quota.release", fake_release)
    monkeypatch.setattr("syte.provider_quota.next_available_model", lambda model: None)
    monkeypatch.setattr(
        "syte.provider_quota.record_quota_exhausted",
        lambda model, retry_after=None: float(retry_after or 2.0),
    )
    monkeypatch.setattr(
        "syte.provider_quota.retry_delay",
        lambda attempt, retry_after=None: 0.01,
    )

    with pytest.raises(ProviderError) as exc_info:
        await _provider_completion(
            {
                "profile": "syra-nano",
                "provider": "openai",
                "label": "Vertex AI",
                "model": NANO_MODEL,
                "api_key": "AQ.test-key",
                "api_base": "https://aiplatform.googleapis.com/v1",
            },
            [{"role": "user", "content": "hello"}],
        )
    err = exc_info.value
    assert err.error_type == "rate_limited"
    assert err.retryable is True
    assert err.detail["retry_after_s"] == 2.0
    assert "gemini-enterprise-agent-platform" in err.detail["help_url"]


def test_explain_google_api_error_points_at_enterprise_429_doc() -> None:
    hint = explain_google_api_error("RESOURCE_EXHAUSTED", status_code=429)
    assert "429" in hint
    assert "gemini-enterprise-agent-platform" in hint


def test_failure_metadata_surfaces_rate_limited_fields() -> None:
    from syte.cloud_agent import _failure_metadata

    exc = ProviderError(
        "quota hit",
        error_type="rate_limited",
        retryable=True,
        detail={"retry_after_s": 12.5, "model": NANO_MODEL},
    )
    meta = _failure_metadata(exc)
    assert meta["error_type"] == "rate_limited"
    assert meta["retryable"] is True
    assert meta["title"] == "Rate limited"
    assert meta["detail"]["retry_after_s"] == 12.5


def test_model_rate_limits_are_conservative_for_express() -> None:
    from syte.ai_providers import model_rate_limit

    nano = model_rate_limit(NANO_MODEL)
    pro = model_rate_limit(PRO_MODEL)
    assert nano["requests_per_minute"] <= 55
    assert nano["max_concurrency"] <= 3
    assert pro["requests_per_minute"] <= 25
    assert pro["max_concurrency"] <= 2
