import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_settings_status_endpoint():
    from main import app
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/status")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_providers_endpoint():
    from main import app
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/ai")
        assert response.status_code == 200
        data = response.json()
        assert "available_providers" in data


@pytest.mark.asyncio
async def test_symbols_endpoint():
    from main import app
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/market/symbols")
        assert response.status_code == 200
        data = response.json()
        assert "symbols" in data
        assert "BTCUSDT" in data["symbols"]


@pytest.mark.asyncio
async def test_trading_status_endpoint():
    from main import app
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/trading/status")
        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "mode" in data


@pytest.mark.asyncio
async def test_trading_positions_endpoint():
    from main import app
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/trading/positions")
        assert response.status_code == 200
        data = response.json()
        assert "positions" in data


@pytest.mark.asyncio
async def test_root_endpoint():
    from main import app
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200