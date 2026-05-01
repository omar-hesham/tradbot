import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def client():
    from main import app
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def test_settings():
    from config.settings import get_settings
    return get_settings()