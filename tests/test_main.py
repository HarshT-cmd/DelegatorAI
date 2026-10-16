import asyncio

import httpx

from main import app


def test_health_check_reports_database_status(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "health.db"))

    async def request_health():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get("/health")

    response = asyncio.run(request_health())
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
