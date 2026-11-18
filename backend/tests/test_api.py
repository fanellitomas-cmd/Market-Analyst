import httpx
import pytest

from app.main import create_app
from tests.conftest import PASSWORD, Router, make_settings, synthetic_prices, yahoo_payload

# ---------- Auth ----------


async def test_endpoints_require_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/macro/indicators")).status_code == 401
    assert (await client.get("/api/markets/relative?symbol=AAPL")).status_code == 401


async def test_login_sets_httponly_cookie(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"username": "tomas", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "ma_session=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert (await client.get("/api/auth/me")).json() == {"username": "tomas"}

    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_wrong_credentials_and_lockout(client: httpx.AsyncClient) -> None:
    for _ in range(5):
        r = await client.post("/api/auth/login", json={"username": "tomas", "password": "mal"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"username": "tomas", "password": PASSWORD})
    assert r.status_code == 429


async def test_wrong_username_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"username": "otro", "password": PASSWORD})
    assert r.status_code == 401


async def test_forged_token_rejected(client: httpx.AsyncClient) -> None:
    client.cookies.set("ma_session", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0b21hcyJ9.")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_disabled_without_config() -> None:
    app = create_app(make_settings(app_password_hash="", jwt_secret=""))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        r = await c.post("/api/auth/login", json={"username": "tomas", "password": PASSWORD})
        assert r.status_code == 503
        assert (await c.get("/api/macro/indicators")).status_code == 503


# ---------- Macro ----------


def _wb(rows: list[tuple[str, str, float | None]]) -> list[object]:
    return [
        {"page": 1, "pages": 1},
        [
            {"country": {"value": c}, "countryiso3code": c, "date": y, "value": v}
            for c, y, v in rows
        ],
    ]


async def test_macro_series(auth_client: httpx.AsyncClient, router: Router) -> None:
    router.json(
        "NY.GDP.MKTP.KD.ZG",
        _wb([("USA", "2022", 1.9), ("USA", "2023", 2.5), ("ARG", "2023", None)]),
    )
    r = await auth_client.get(
        "/api/macro/series", params={"indicator": "gdp_growth", "countries": "usa,arg"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["indicator"]["unit"] == "% anual"
    usa, arg = body["series"]
    assert usa["country_name"] == "Estados Unidos"
    assert usa["status"] == "OK"
    assert [o["value"] for o in usa["observations"]] == [1.9, 2.5]
    assert arg["status"] == "NO_DISPONIBLE"
    assert arg["observations"] == []
    assert arg["missing_periods"] == 1


async def test_macro_series_validation(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/macro/series", params={"indicator": "nada"})
    assert r.status_code == 422
    r = await auth_client.get(
        "/api/macro/series", params={"indicator": "inflation", "countries": "XXX"}
    )
    assert r.status_code == 422


async def test_macro_series_source_down(auth_client: httpx.AsyncClient, router: Router) -> None:
    router.json("FP.CPI.TOTL.ZG", {}, status=503)
    r = await auth_client.get("/api/macro/series", params={"indicator": "inflation"})
    assert r.status_code == 502


async def test_macro_snapshot_partial_failure(
    auth_client: httpx.AsyncClient, router: Router
) -> None:
    router.json("/indicator/", _wb([("ARG", "2022", 5.0), ("ARG", "2023", 211.4)]))
    router.json("SL.UEM.TOTL.ZS", {}, status=500)
    r = await auth_client.get("/api/macro/snapshot", params={"countries": "ARG,USA"})
    assert r.status_code == 200, r.text
    body = r.json()
    arg = body["countries"][0]["values"]
    assert arg["inflation"] == {"value": 211.4, "period": "2023", "status": "OK"}
    assert arg["unemployment"]["status"] == "ERROR_FUENTE"
    assert body["countries"][1]["values"]["inflation"]["status"] == "NO_DISPONIBLE"
    assert "unemployment" in body["errors"]


# ---------- Mercados ----------


@pytest.fixture
def prices(router: Router) -> None:
    bench = synthetic_prices(260, 0.0004, 0.01)
    # El activo amplifica 1,5x los movimientos del benchmark.
    asset = [bench[0]]
    for i in range(1, len(bench)):
        asset.append(asset[-1] * (1 + 1.5 * (bench[i] / bench[i - 1] - 1)))
    router.json("/v8/finance/chart/SPY", yahoo_payload(list(bench)))
    router.json("/v8/finance/chart/NVDA", yahoo_payload(list(asset)))
    router.json("/v8/finance/chart/NEW", yahoo_payload(list(asset[:30])))
    router.json(
        "/v8/finance/chart/ZZZZ",
        {"chart": {"result": None, "error": {"code": "Not Found", "description": "x"}}},
        status=404,
    )


@pytest.mark.usefixtures("prices")
async def test_relative_analysis(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/markets/relative", params={"symbol": "nvda"})
    assert r.status_code == 200, r.text
    body = r.json()
    m = body["metrics"]
    assert body["symbol"] == "NVDA"
    assert body["benchmark"] == "SPY"
    assert body["observations"] == 259
    assert m["beta"]["status"] == "OK"
    assert m["beta"]["value"] == pytest.approx(1.5)
    assert m["correlation"]["value"] == pytest.approx(1.0)
    assert body["rebased_prices"][0] == {
        "date": body["start_date"],
        "asset": 100.0,
        "benchmark": 100.0,
    }
    assert len(body["rolling"]) == 259 - 63 + 1
    assert body["rolling"][-1]["date"] == body["end_date"]


@pytest.mark.usefixtures("prices")
async def test_relative_unknown_symbol(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/markets/relative", params={"symbol": "ZZZZ"})
    assert r.status_code == 404


@pytest.mark.usefixtures("prices")
async def test_compare_mixed_results(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/markets/compare", params={"symbols": "NVDA,NEW,ZZZZ,nvda"})
    assert r.status_code == 200, r.text
    rows = {row["symbol"]: row for row in r.json()["rows"]}
    assert list(rows) == ["NVDA", "NEW", "ZZZZ"]  # sin duplicados, en orden
    assert rows["NVDA"]["status"] == "OK"
    assert rows["NEW"]["status"] == "NO_DISPONIBLE"
    assert rows["NEW"]["metrics"]["beta"]["value"] is None
    assert rows["ZZZZ"]["status"] == "NO_DISPONIBLE"
    assert rows["ZZZZ"]["metrics"] is None


async def test_compare_limits(auth_client: httpx.AsyncClient) -> None:
    many = ",".join(f"S{i}" for i in range(16))
    r = await auth_client.get("/api/markets/compare", params={"symbols": many})
    assert r.status_code == 422
    r = await auth_client.get("/api/markets/relative", params={"symbol": "../etc"})
    assert r.status_code == 422


async def test_prices_are_cached(auth_client: httpx.AsyncClient, router: Router) -> None:
    router.json(
        "/v8/finance/chart/SPY",
        yahoo_payload(list[float | None](synthetic_prices(100, 0.001, 0.01))),
    )
    router.json(
        "/v8/finance/chart/KO",
        yahoo_payload(list[float | None](synthetic_prices(100, 0.0, 0.005, 1.0))),
    )
    for _ in range(3):
        r = await auth_client.get("/api/markets/relative", params={"symbol": "KO"})
        assert r.status_code == 200
    yahoo_calls = [c for c in router.calls if "yahoo.test" in str(c.url)]
    assert len(yahoo_calls) == 2  # KO + SPY una sola vez cada uno
