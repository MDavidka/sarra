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
    assert message == "LiteLLM PostgreSQL migrations applied and MCP schema verified."
    assert len(calls) == 3
    initial_repair_args, initial_repair_timeout = calls[0]
    assert initial_repair_timeout == 60.0
    assert initial_repair_args[:5] == [
        "run", "--rm", "--network", litellm_manager.LITELLM_NETWORK, "--entrypoint"
    ]
    assert litellm_manager.LITELLM_DB_IMAGE in initial_repair_args
    assert litellm_manager.LITELLM_MCP_INSTRUCTIONS_COMPAT_SQL in " ".join(initial_repair_args)

    args, timeout = calls[1]
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

    final_repair_args, final_repair_timeout = calls[2]
    assert final_repair_timeout == 60.0
    assert final_repair_args == initial_repair_args


@pytest.mark.asyncio
async def test_litellm_schema_repair_failure_blocks_migrations(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        calls.append(args)
        return 1, "permission denied"

    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    ok, message = await litellm_manager._run_litellm_migrations(
        "postgresql://litellm:secret@db:5432/litellm"
    )

    assert ok is False
    assert len(calls) == 1
    assert "schema repair failed" in message
    assert "proxy was not started" in message


@pytest.mark.asyncio
async def test_litellm_migration_failure_blocks_startup(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        calls.append(args)
        if len(calls) == 1:
            return 0, "schema verified"
        return 1, "The column instructions does not exist"

    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    ok, message = await litellm_manager._run_litellm_migrations(
        "postgresql://litellm:secret@db:5432/litellm"
    )

    assert ok is False
    assert len(calls) == 2
    assert "migrations failed" in message
    assert "proxy was not started" in message
    assert "instructions does not exist" in message


@pytest.mark.asyncio
async def test_litellm_final_schema_repair_failure_blocks_startup(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        calls.append(args)
        if len(calls) == 3:
            return 1, "column repair denied"
        return 0, "ok"

    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    ok, message = await litellm_manager._run_litellm_migrations(
        "postgresql://litellm:secret@db:5432/litellm"
    )

    assert ok is False
    assert len(calls) == 3
    assert "verification failed after migrations" in message
    assert "proxy was not started" in message
    assert "column repair denied" in message


def test_litellm_proxy_uses_documented_pinned_image() -> None:
    assert litellm_manager.LITELLM_IMAGE == (
        "docker.litellm.ai/berriai/litellm:1.92.1"
    )
