from __future__ import annotations

import pytest

from syte import litellm_manager


@pytest.mark.asyncio
async def test_litellm_migrations_use_documented_pinned_image(monkeypatch) -> None:
    calls: list[tuple[list[str], float]] = []

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        calls.append((args, timeout))
        return 0, "migrations applied"

    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    ok, message = await litellm_manager._run_litellm_migrations(
        "postgresql://litellm:secret@syte-litellm-db:5432/litellm",
        litellm_manager.LITELLM_NETWORK,
    )

    assert ok is True
    assert message == "LiteLLM PostgreSQL migrations applied."
    args, timeout = calls[0]
    assert timeout == 300.0
    assert args[:5] == ["run", "--rm", "--network", litellm_manager.LITELLM_NETWORK, "--entrypoint"]
    assert litellm_manager.LITELLM_PRISMA_BIN in args
    assert litellm_manager.LITELLM_IMAGE in args
    assert litellm_manager.LITELLM_IMAGE == "docker.litellm.ai/berriai/litellm:1.92.1"
    assert args[-4:] == [
        "migrate",
        "deploy",
        "--schema",
        litellm_manager.LITELLM_SCHEMA_PATH,
    ]


@pytest.mark.asyncio
async def test_litellm_migration_failure_blocks_startup(monkeypatch) -> None:
    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        return 1, "The column instructions does not exist"

    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    ok, message = await litellm_manager._run_litellm_migrations(
        "postgresql://litellm:secret@db:5432/litellm"
    )

    assert ok is False
    assert "migrations failed" in message
    assert "proxy was not started" in message
    assert "instructions does not exist" in message


def test_litellm_proxy_uses_documented_pinned_image() -> None:
    assert litellm_manager.LITELLM_IMAGE == (
        "docker.litellm.ai/berriai/litellm:1.92.1"
    )
