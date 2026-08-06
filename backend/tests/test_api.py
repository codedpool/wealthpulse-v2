import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.asyncio
async def test_root():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "WealthPulse v2 running"

@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] in ("ok", "down")
    assert body["redis"] in ("ok", "down")

@pytest.mark.asyncio
async def test_analytics_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/analytics/portfolio")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_analytics_fake_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            "/api/analytics/portfolio",
            headers={"Authorization": "Bearer faketoken"}
        )
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_market_mutualfunds():
    # Route exists and query param validation works
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/market/mutualfunds")  # no ?q — should 422
    assert response.status_code == 422  # Unprocessable — q is required

@pytest.mark.asyncio
async def test_debug_endpoints_require_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in [
            ("post", "/api/portfolio/test-nav-refresh"),
            ("post", "/api/portfolio/backfill-nav/119551"),
            ("post", "/api/analytics/test-snapshot"),
        ]:
            response = await getattr(ac, method)(path)
            assert response.status_code in (401, 403), f"{path} is unauthenticated"

@pytest.mark.asyncio
async def test_ai_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/ai/dost")
    assert response.status_code == 401
