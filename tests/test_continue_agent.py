"""Tests for Continue agent runtime management."""

from pathlib import Path

import pytest
from starlette.requests import Request

from syte.auth import verify_internal_service_request
from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_ensure_agent_runtime_assigns_port_and_profile(
    tmp_data_dir: Path,
) -> None:
    from syte.continue_agent import ensure_agent_runtime
    from syte.database import create_project, get_project, init_db, set_setting

    await init_db()
    await set_setting("continue_default_model_profile", "syra-havy")
    await create_project({
        "id": "proj-1",
        "name": "Agent Test",
        "port": 3000,
        "start_command": "",
    })

    project = await get_project("proj-1")
    project = await ensure_agent_runtime(project or {})

    assert project["agent_port"] == settings.continue_port_start
    assert project["agent_runtime"] == "project"
    assert project["agent_model_profile"] == "syra-havy"


@pytest.mark.asyncio
async def test_write_agent_config_uses_per_profile_providers(
    tmp_data_dir: Path,
) -> None:
    from syte.ai_providers import DEEPSEEK_API_BASE, VERTED_API_BASE
    from syte.continue_agent import agent_config_path, write_agent_config
    from syte.database import create_project, get_project, init_db, set_setting, update_project

    await init_db()
    await set_setting("continue_syra_nano_api_key", "nano-key")
    await set_setting("continue_syra_base_api_key", "base-key")
    await set_setting("continue_syra_havy_api_key", "havy-key")
    await create_project({
        "id": "proj-2",
        "name": "Bridge Test",
        "port": 3001,
        "start_command": "",
    })
    await update_project("proj-2", {"agent_model_profile": "syra-base"})

    project = await get_project("proj-2")
    path = await write_agent_config(project or {})
    text = path.read_text()

    assert path == agent_config_path("proj-2")
    assert f'apiBase: "{DEEPSEEK_API_BASE}"' in text
    assert f'apiBase: "{VERTED_API_BASE}"' in text
    assert '${{ secrets.SYRA_BASE_API_KEY }}' in text
    assert '${{ secrets.SYRA_NANO_API_KEY }}' in text
    assert '${{ secrets.SYRA_HAVY_API_KEY }}' in text
    assert 'name: "syra-base"' in text
    assert text.index('name: "syra-base"') < text.index('name: "syra-nano"')
    assert "rules:" in text
    assert "Syte website agent" in text
    assert "mcpServers:" not in text


@pytest.mark.asyncio
async def test_start_agent_reports_missing_continue_cli(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.continue_agent import start_agent
    from syte.database import create_project, get_project, init_db

    await init_db()
    await create_project({
        "id": "proj-3",
        "name": "No CLI",
        "port": 3002,
        "start_command": "",
    })
    monkeypatch.setattr("syte.continue_agent.continue_installed", lambda: False)

    ok, message, meta = await start_agent("proj-3")
    project = await get_project("proj-3")

    assert ok is False
    assert "Continue CLI not installed" in message
    assert meta == {}
    assert project["agent_status"] == "error"


@pytest.mark.asyncio
async def test_get_agent_status_exposes_proxy_and_backend_state(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.continue_agent import get_agent_status
    from syte.database import create_project, init_db, set_setting, update_project

    await init_db()
    await set_setting("continue_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-4",
        "name": "Status",
        "port": 3003,
        "start_command": "",
    })
    await update_project("proj-4", {"agent_port": 5333, "agent_status": "running"})
    monkeypatch.setattr("syte.continue_agent.is_agent_running", lambda _id: True)

    async def fake_probe(_port):
        return {"ok": True, "url": "http://127.0.0.1:5333/health", "status_code": 200}

    monkeypatch.setattr("syte.continue_agent.probe_agent_http", fake_probe)

    async def fake_backend(_project):
        return {"ok": True, "status_code": 200, "url": "https://api.deepseek.com/v1/models", "error": ""}

    monkeypatch.setattr("syte.continue_agent.backend_health", fake_backend)
    status = await get_agent_status("proj-4", request_base="https://sycord.site")

    assert status["agent_running"] is True
    assert status["agent_proxy_url"] == "https://sycord.site/api/internal/projects/proj-4/agent/proxy"
    assert status["agent_backend"]["ok"] is True


@pytest.mark.asyncio
async def test_write_agent_config_skips_profiles_without_keys(
    tmp_data_dir: Path,
) -> None:
    from syte.continue_agent import write_agent_config
    from syte.database import create_project, get_project, init_db, set_setting, update_project

    await init_db()
    await set_setting("continue_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-5",
        "name": "Partial Keys",
        "port": 3004,
        "start_command": "",
    })
    await update_project("proj-5", {"agent_model_profile": "syra-base"})

    project = await get_project("proj-5")
    path = await write_agent_config(project or {})
    text = path.read_text()

    assert 'name: "syra-base"' in text
    assert 'name: "syra-nano"' not in text
    assert 'name: "syra-havy"' not in text


@pytest.mark.asyncio
async def test_write_agent_config_requires_active_profile_key(
    tmp_data_dir: Path,
) -> None:
    from syte.continue_agent import write_agent_config
    from syte.database import create_project, get_project, init_db, set_setting, update_project

    await init_db()
    await set_setting("continue_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-6",
        "name": "Missing Active Key",
        "port": 3005,
        "start_command": "",
    })
    await update_project("proj-6", {"agent_model_profile": "syra-nano"})

    project = await get_project("proj-6")
    with pytest.raises(RuntimeError, match="syra-nano"):
        await write_agent_config(project or {})


@pytest.mark.asyncio
async def test_write_agent_config_can_include_mcp_when_enabled(
    tmp_data_dir: Path,
) -> None:
    from syte.continue_agent import write_agent_config
    from syte.database import create_project, get_project, init_db, set_setting, update_project

    await init_db()
    await set_setting("continue_syra_base_api_key", "base-key")
    await set_setting("continue_enable_mcp", "1")
    await create_project({
        "id": "proj-mcp",
        "name": "MCP Enabled",
        "port": 3006,
        "start_command": "",
    })
    await update_project("proj-mcp", {"agent_model_profile": "syra-base"})

    project = await get_project("proj-mcp")
    text = (await write_agent_config(project or {})).read_text()

    assert "mcpServers:" in text
    assert "type: stdio" in text
    assert "syte-tools" in text
    assert "syte-mcp" in text


@pytest.mark.asyncio
async def test_build_serve_command_includes_auto_flag() -> None:
    from syte.continue_agent import build_serve_command

    cmd = build_serve_command("/tmp/config.yaml", 5200)
    assert "--port 5200" in cmd
    assert "--timeout" in cmd
    assert "--auto" in cmd
    assert "--host" not in cmd


@pytest.mark.asyncio
async def test_write_agent_permissions_creates_allow_all_policy(
    tmp_data_dir: Path,
) -> None:
    from syte.continue_agent import agent_home, write_agent_permissions
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "proj-perms",
        "name": "Permissions",
        "port": 3007,
        "start_command": "",
    })

    path = write_agent_permissions("proj-perms")
    text = path.read_text()

    assert path == agent_home("proj-perms") / ".continue" / "permissions.yaml"
    assert '  - "*"' in text
    assert "allow:" in text


@pytest.mark.asyncio
async def test_communicate_with_agent_requires_api_key(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.continue_agent import communicate_with_agent
    from syte.database import create_project, init_db, update_project

    await init_db()
    await create_project({
        "id": "proj-chat",
        "name": "Chat",
        "port": 3010,
        "start_command": "",
    })
    await update_project("proj-chat", {"agent_model_profile": "syra-base"})
    monkeypatch.setattr("syte.continue_agent.continue_installed", lambda: True)

    result = await communicate_with_agent("proj-chat", "hello", source="gui")

    assert result["ok"] is False
    assert result["error"] == "api_key_missing"
    assert "API key" in result["message"]


@pytest.mark.asyncio
async def test_verify_internal_service_request_accepts_shared_secret(
    tmp_data_dir: Path,
) -> None:
    from syte.database import init_db, set_setting

    await init_db()
    await set_setting("syra_internal_secret", "top-secret")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/internal/projects/proj/agent",
        "headers": [(b"x-syra-internal-secret", b"top-secret")],
        "query_string": b"",
    }
    request = Request(scope)
    result = await verify_internal_service_request(request)
    assert result["auth"] == "internal-secret"
