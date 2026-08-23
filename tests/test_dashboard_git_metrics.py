from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from syte.agent_metrics import get_dashboard_metrics
from syte.config import settings
from syte.main import app
from syte.system_stats import get_system_stats


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


def test_system_stats_metrics():
    stats = get_system_stats(sample_cpu=False)
    assert "cpu_percent" in stats
    assert "ram_used_mb" in stats
    assert "ram_total_mb" in stats
    assert "ram_percent" in stats
    assert "disk_used_gb" in stats
    assert "disk_total_gb" in stats
    assert "disk_percent" in stats
    assert "ping_ms" in stats


@pytest.mark.asyncio
async def test_agent_dashboard_metrics(tmp_data_dir: Path):
    metrics = await get_dashboard_metrics()
    assert "api_requests_7d" in metrics
    assert "api_requests_30d" in metrics
    assert "project_count" in metrics
    assert "cpu_percent" in metrics
    assert "ram_percent" in metrics
    assert "disk_percent" in metrics


@pytest.mark.asyncio
async def test_overview_metrics_api(tmp_data_dir: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/platform/overview/metrics")
        assert res.status_code == 200
        data = res.json()
        assert "cpu_percent" in data
        assert "memory_percent" in data
        assert "disk_percent" in data
        assert "api_requests_7d" in data
        assert "api_requests_30d" in data
        assert "project_count" in data
