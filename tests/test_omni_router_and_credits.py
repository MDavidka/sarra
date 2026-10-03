import pytest
from httpx import AsyncClient, ASGITransport
from syte.main import app
from syte import database

@pytest.mark.asyncio
async def test_omni_models_catalog():
    await database.init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/ai/omni/models?project_id=global")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "models" in data
        assert "top_swe_models" in data
        assert "active_model" in data
        assert "credits" in data
        assert data["credits"]["balance"] >= 0.0
        assert len(data["top_swe_models"]) >= 3

@pytest.mark.asyncio
async def test_omni_model_selection():
    await database.init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/omni/select-model", json={
            "model_id": "claude-3-7-sonnet",
            "project_id": "global"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["active_model"] == "claude-3-7-sonnet"

@pytest.mark.asyncio
async def test_user_credits_metering():
    await database.init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Fetch credits
        response = await client.get("/api/ai/user/credits")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "credits" in data
        assert data["credits"]["total_granted"] == 5.00
        initial_balance = data["credits"]["balance"]

        # Deduct credit
        await database.deduct_user_credits(
            user_id="default_user",
            cost_usd=0.05,
            model="claude-3-7-sonnet",
            prompt_tokens=1000,
            completion_tokens=2000
        )
        
        # Check updated balance
        response = await client.get("/api/ai/user/credits")
        assert response.status_code == 200
        data = response.json()
        assert data["credits"]["balance"] == pytest.approx(initial_balance - 0.05, 0.0001)
        assert data["credits"]["total_used"] >= 0.05
        assert len(data["records"]) > 0

@pytest.mark.asyncio
async def test_handshake_sync_and_status():
    await database.init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sync_resp = await client.post("/api/ai/handshake/sync-providers", json={
            "project_id": "global"
        })
        assert sync_resp.status_code == 200
        sync_data = sync_resp.json()
        assert sync_data["ok"] is True
        assert sync_data["synced_models_count"] >= 10

        status_resp = await client.get("/api/ai/handshake/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["ok"] is True
        assert status_data["vm_status"] == "connected"
