from app.testing import running_app


async def test_spa_served_with_fallback(app_kwargs, settings, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Cab desk</title>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    app_kwargs["settings"] = settings.model_copy(update={"static_dir": str(tmp_path)})
    async with running_app(**app_kwargs) as (_, c):
        root = await c.get("/")
        deep = await c.get("/manager")
        asset = await c.get("/assets/app.js")
        api_404 = await c.get("/api/nope")
    assert "Cab desk" in root.text and "Cab desk" in deep.text
    assert asset.text == "console.log(1)"
    assert api_404.status_code == 404 and api_404.json() == {"error": "Not found."}


async def test_no_static_dir_is_fine(app_kwargs, settings, tmp_path):
    app_kwargs["settings"] = settings.model_copy(update={"static_dir": str(tmp_path / "missing")})
    async with running_app(**app_kwargs) as (_, c):
        assert (await c.get("/api/health")).status_code == 200


async def test_spa_does_not_shadow_api_routes(app_kwargs, settings, tmp_path):
    from app.main import create_app

    (tmp_path / "index.html").write_text("<!doctype html><title>Cab desk</title>")
    s = settings.model_copy(update={"static_dir": str(tmp_path), "dev_auth": False})
    app = create_app(settings=s, clock=app_kwargs["clock"], seed_today=app_kwargs["seed_today"])

    @app.get("/api/added-later")
    async def later():
        return {"ok": True}

    async with running_app(app=app) as (_, c):
        assert (await c.get("/api/added-later")).json() == {"ok": True}
        post = await c.post("/api/auth/token", json={"employee_id": "A-E01"})
        assert post.status_code == 404 and post.json() == {"error": "Not found."}
        assert "Cab desk" in (await c.get("/manager")).text
