from datetime import timedelta

from app.auth import TenantContext, decode_token, issue_token
from app.main import create_app
from app.testing import running_app
from tests.conftest import bearer, get_token


async def test_token_roundtrip_carries_company_from_db(app_kwargs, settings, clock):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "G-E01")
    assert decode_token(token, settings, clock.now()) == TenantContext("globex", "G-E01", "employee")


async def test_manager_role_comes_from_db(app_kwargs, settings, clock):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-M01")
    assert decode_token(token, settings, clock.now()).role == "manager"


async def test_token_expires_by_app_clock(app_kwargs, settings, clock):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-E01")
        clock.advance(settings.jwt_ttl_min + 1)
        r = await c.get("/api/me", headers=bearer(token))
    assert r.status_code == 401


async def test_token_body_cannot_set_company(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        r = await c.post("/api/auth/token", json={"employee_id": "A-E01", "company_id": "globex"})
    assert r.status_code == 422


async def test_unknown_person_404(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        r = await c.post("/api/auth/token", json={"employee_id": "X-999"})
    assert r.status_code == 404
    assert r.json() == {"error": "Unknown person."}


async def test_me_returns_profile(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-E01")
        r = await c.get("/api/me", headers=bearer(token))
    assert r.json() == {
        "employee_id": "A-E01",
        "name": "Asha",
        "role": "employee",
        "company_id": "acme",
        "company_name": "Acme Tech",
    }


async def test_missing_token_401(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        r = await c.get("/api/me")
    assert r.status_code == 401


async def test_tampered_token_401(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-E01")
        sig_char = token[-2]
        tampered = token[:-2] + ("A" if sig_char != "A" else "B") + token[-1]
        r = await c.get("/api/me", headers=bearer(tampered))
    assert r.status_code == 401
    assert r.json() == {"error": "Your session has expired. Please sign in again."}


async def test_expired_token_401(app_kwargs, settings, clock):
    token = issue_token(
        TenantContext("acme", "A-E01", "employee"), settings, clock.now() - timedelta(days=1)
    )
    async with running_app(**app_kwargs) as (_, c):
        r = await c.get("/api/me", headers=bearer(token))
    assert r.status_code == 401


async def test_dev_auth_off_hides_issuer(app_kwargs, settings):
    app_kwargs["settings"] = settings.model_copy(update={"dev_auth": False})
    async with running_app(**app_kwargs) as (_, c):
        r1 = await c.post("/api/auth/token", json={"employee_id": "A-E01"})
        r2 = await c.get("/api/auth/demo-users")
    assert r1.status_code == 404
    assert r2.status_code == 404


async def test_demo_users_lists_only_demo_people(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        r = await c.get("/api/auth/demo-users")
    groups = {g["company_id"]: g for g in r.json()}
    assert set(groups) == {"acme", "globex"}
    ids = [p["employee_id"] for p in groups["acme"]["people"]]
    assert ids == ["A-E01", "A-E02", "A-E03", "A-M01"]


async def test_unhandled_error_is_plain_json(app_kwargs):
    app = create_app(**app_kwargs)

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("secret internals")

    async with running_app(app=app) as (_, c):
        r = await c.get("/api/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["error"].startswith("Something went wrong on our side. Nothing was changed.")
    assert len(body["ref"]) == 8
    assert "Traceback" not in r.text and "secret internals" not in r.text
