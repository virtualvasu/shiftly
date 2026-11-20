from app.auth import TenantContext, decode_token, hash_password, verify_password
from app.seed import DEMO_PASSWORD
from app.testing import running_app


def test_password_hashing():
    stored = hash_password("s3cret-pass")
    assert stored.startswith("scrypt$") and "s3cret-pass" not in stored
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong", stored)
    assert hash_password("s3cret-pass") != stored  # salted


async def test_login_with_email_and_password(app_kwargs, settings, clock):
    async with running_app(**app_kwargs) as (_, c):
        r = await c.post("/api/auth/login", json={"email": "Arjun@Globex.test", "password": DEMO_PASSWORD})
    assert r.status_code == 200
    assert decode_token(r.json()["token"], settings, clock.now()) == TenantContext("globex", "G-E01", "employee")


async def test_login_wrong_password_or_unknown_email_same_answer(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        bad_pw = await c.post("/api/auth/login", json={"email": "arjun@globex.test", "password": "nope"})
        unknown = await c.post("/api/auth/login", json={"email": "ghost@globex.test", "password": DEMO_PASSWORD})
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json() == {"error": "Email or password is incorrect."}


async def test_login_body_cannot_choose_company(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        r = await c.post("/api/auth/login", json={"email": "arjun@globex.test", "password": DEMO_PASSWORD, "company_id": "acme"})
    assert r.status_code == 422


async def test_login_works_without_dev_auth(app_kwargs, settings):
    app_kwargs["settings"] = settings.model_copy(update={"dev_auth": False})
    async with running_app(**app_kwargs) as (_, c):
        r = await c.post("/api/auth/login", json={"email": "kavita@acme.test", "password": DEMO_PASSWORD})
    assert r.status_code == 200
