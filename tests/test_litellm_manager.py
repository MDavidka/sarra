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
    assert litellm_manager.LITELLM_SCHEMA_PATH == (
        "/app/.venv/lib/python3.13/site-packages/"
        "litellm_proxy_extras/schema.prisma"
    )


@pytest.mark.asyncio
async def test_litellm_schema_repair_is_additive_and_uses_prisma(monkeypatch) -> None:
    calls: list[tuple[list[str], float]] = []

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        calls.append((args, timeout))
        return 0, "Script executed successfully."

    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    ok, message = await litellm_manager._repair_litellm_schema(
        "postgresql://litellm:secret@syte-litellm-db:5432/litellm",
        litellm_manager.LITELLM_NETWORK,
    )

    assert ok is True
    assert message == "LiteLLM MCP instructions schema verified."
    args, timeout = calls[0]
    assert timeout == 300.0
    assert args[:6] == [
        "run",
        "--rm",
        "--network",
        litellm_manager.LITELLM_NETWORK,
        "--entrypoint",
        "sh",
    ]
    assert litellm_manager.LITELLM_IMAGE in args
    assert any(
        value == (
            "LITELLM_SCHEMA_REPAIR_SQL="
            + litellm_manager.LITELLM_MCP_INSTRUCTIONS_REPAIR_SQL
        )
        for value in args
    )
    assert "ADD COLUMN IF NOT EXISTS \"instructions\" TEXT" in " ".join(args)
    assert litellm_manager.LITELLM_PRISMA_BIN in args[-1]
    assert "db execute --stdin --schema" in args[-1]


@pytest.mark.asyncio
async def test_litellm_schema_repair_failure_is_actionable(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        calls.append(args)
        return 1, 'permission denied for table LiteLLM_MCPServerTable'

    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    ok, message = await litellm_manager._repair_litellm_schema(
        "postgresql://litellm:secret@db.example:5432/litellm"
    )

    assert ok is False
    assert "LiteLLM_MCPServerTable.instructions" in message
    assert "proxy was not started" in message
    assert "permission denied" in message
    assert "--network" not in calls[0]


@pytest.mark.asyncio
async def test_schema_repair_failure_blocks_proxy_start(monkeypatch) -> None:
    from syte import preview_manager

    docker_calls: list[list[str]] = []

    async def fake_status():
        return {"running": False}

    async def fake_preview_migration():
        return {"ok": True, "message": "ready"}

    async def fake_database():
        return True, "postgresql://litellm:secret@db:5432/litellm", "", ""

    async def fake_migrations(database_url: str, database_network: str = ""):
        return True, "LiteLLM PostgreSQL migrations applied."

    async def fake_repair(database_url: str, database_network: str = ""):
        return False, "LiteLLM_MCPServerTable.instructions repair failed"

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        docker_calls.append(args)
        return 0, ""

    monkeypatch.setattr(litellm_manager, "litellm_status", fake_status)
    monkeypatch.setattr(
        preview_manager,
        "relocate_litellm_preview_conflicts",
        fake_preview_migration,
    )
    monkeypatch.setattr(litellm_manager, "_ensure_litellm_database", fake_database)
    monkeypatch.setattr(litellm_manager, "_run_litellm_migrations", fake_migrations)
    monkeypatch.setattr(litellm_manager, "_repair_litellm_schema", fake_repair)
    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)

    result = await litellm_manager.start_litellm(
        master_key="sk-master",
        salt_key="salt",
    )

    assert result["ok"] is False
    assert result["running"] is False
    assert "LiteLLM_MCPServerTable.instructions" in result["schema_message"]
    assert docker_calls == []


@pytest.mark.asyncio
async def test_start_repairs_schema_before_proxy_and_disables_auto_update(
    monkeypatch,
) -> None:
    from syte import preview_manager

    events: list[str] = []
    docker_calls: list[list[str]] = []
    status_results = iter([
        {"running": False},
        {"running": True, "healthy": True, "message": "running"},
    ])

    async def fake_status():
        return next(status_results)

    async def fake_preview_migration():
        return {"ok": True, "message": "ready"}

    async def fake_database():
        return (
            True,
            "postgresql://litellm:secret@syte-litellm-db:5432/litellm",
            "",
            litellm_manager.LITELLM_NETWORK,
        )

    async def fake_migrations(database_url: str, database_network: str = ""):
        events.append("migrations")
        return True, "LiteLLM PostgreSQL migrations applied."

    async def fake_repair(database_url: str, database_network: str = ""):
        events.append("repair")
        return True, "LiteLLM MCP instructions schema verified."

    async def fake_run_docker(args: list[str], timeout: float = 30.0):
        docker_calls.append(args)
        if args[:2] == ["run", "-d"]:
            events.append("proxy")
        return 0, "container-id"

    async def fake_health():
        return {"ok": True, "healthy": True, "message": "ready"}

    async def fake_sleep(seconds: float):
        return None

    monkeypatch.setattr(litellm_manager, "litellm_status", fake_status)
    monkeypatch.setattr(
        preview_manager,
        "relocate_litellm_preview_conflicts",
        fake_preview_migration,
    )
    monkeypatch.setattr(litellm_manager, "_ensure_litellm_database", fake_database)
    monkeypatch.setattr(litellm_manager, "_run_litellm_migrations", fake_migrations)
    monkeypatch.setattr(litellm_manager, "_repair_litellm_schema", fake_repair)
    monkeypatch.setattr(litellm_manager, "_run_docker", fake_run_docker)
    monkeypatch.setattr(litellm_manager, "_wait_for_litellm_health", fake_health)
    monkeypatch.setattr(litellm_manager.os, "makedirs", lambda *args, **kwargs: None)
    monkeypatch.setattr(litellm_manager.asyncio, "sleep", fake_sleep)

    result = await litellm_manager.start_litellm(
        master_key="sk-master",
        salt_key="salt",
    )

    assert result["ok"] is True
    assert events == ["migrations", "repair", "proxy"]
    proxy_args = next(args for args in docker_calls if args[:2] == ["run", "-d"])
    assert "DISABLE_SCHEMA_UPDATE=true" in proxy_args
    assert result["schema_message"] == "LiteLLM MCP instructions schema verified."



@pytest.mark.asyncio
async def test_readiness_wait_respects_total_deadline(monkeypatch) -> None:
    clock = {"now": 0.0}
    probe_count = 0

    def fake_monotonic() -> float:
        return clock["now"]

    async def fake_health():
        nonlocal probe_count
        probe_count += 1
        clock["now"] += 5.0
        return {"ok": False, "healthy": False, "message": "still starting"}

    async def fake_sleep(seconds: float):
        clock["now"] += seconds

    monkeypatch.setattr(litellm_manager, "monotonic", fake_monotonic)
    monkeypatch.setattr(litellm_manager, "litellm_health", fake_health)
    monkeypatch.setattr(litellm_manager.asyncio, "sleep", fake_sleep)

    result = await litellm_manager._wait_for_litellm_health(timeout_seconds=12.0)

    assert result["healthy"] is False
    assert "readiness deadline of 12 seconds reached" in result["message"]
    assert probe_count == 2
    assert clock["now"] == 12.0


@pytest.mark.asyncio
async def test_readiness_wait_accepts_healthy_response_at_deadline(monkeypatch) -> None:
    clock = {"now": 0.0}
    responses = iter([False, True])

    def fake_monotonic() -> float:
        return clock["now"]

    async def fake_health():
        clock["now"] += 5.0
        healthy = next(responses)
        return {"ok": healthy, "healthy": healthy, "message": "ready" if healthy else "starting"}

    async def fake_sleep(seconds: float):
        clock["now"] += seconds

    monkeypatch.setattr(litellm_manager, "monotonic", fake_monotonic)
    monkeypatch.setattr(litellm_manager, "litellm_health", fake_health)
    monkeypatch.setattr(litellm_manager.asyncio, "sleep", fake_sleep)

    result = await litellm_manager._wait_for_litellm_health(timeout_seconds=12.0)

    assert result == {"ok": True, "healthy": True, "message": "ready"}
    assert clock["now"] == 12.0
