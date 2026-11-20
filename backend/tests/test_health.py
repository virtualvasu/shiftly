from app.testing import running_app


async def test_health_reports_db_ok(settings):
    async with running_app(settings=settings) as (_, client):
        r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": True}
