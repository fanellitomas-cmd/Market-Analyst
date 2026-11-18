"""Columna 1: liquidez, condiciones financieras y calendario con sorpresas."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.agent.jobs import JOBS_BY_ID, run_job
from app.container import Services
from app.services.econ_calendar import (
    SPEC_BY_KEY,
    compute_actual,
    reference_month,
)
from app.services.liquidity import basket_ratio, changes, net_liquidity, on_or_before
from app.sources.forexfactory import parse_value
from app.sources.fred import FredSeries
from tests.conftest import Router, yahoo_payload

# ---------- Cálculos de liquidez ----------


def test_net_liquidity_aligns_without_interpolating() -> None:
    wed1, wed2, wed3 = date(2026, 10, 7), date(2026, 10, 14), date(2026, 10, 21)
    walcl = [(wed1, 6_600_000.0), (wed2, 6_590_000.0), (wed3, 6_580_000.0)]  # millones
    tga = [(wed1, 800_000.0), (wed2, 750_000.0)]  # millones; falta la 3.ª semana... casi
    rrp = [(wed1 - timedelta(days=1), 150.0), (wed2, 120.0), (wed3, 100.0)]  # miles de millones
    points, skipped = net_liquidity(walcl, tga, rrp)
    # Semana 1: RRP del día anterior (permitido). Semana 3: TGA de hace 7 días (permitido).
    assert points == [
        (wed1, 6600 - 800 - 150),
        (wed2, 6590 - 750 - 120),
        (wed3, 6580 - 750 - 100),
    ]
    assert skipped == 0
    points, skipped = net_liquidity([(date(2026, 11, 4), 1.0)], tga, rrp)
    assert points == [] and skipped == 1  # TGA de hace 3 semanas: no representa la fecha


def test_changes_respect_max_base_lag() -> None:
    pts = [(date(2025, 11, 1), 100.0), (date(2026, 9, 1), 110.0), (date(2026, 11, 1), 121.0)]
    out = {c.label: c for c in changes(pts, "pct")}
    assert out["1 año"].value == pytest.approx(0.21)
    assert out["3 meses"].value is None  # la base más cercana (1/9) no representa el 1/8
    assert out["1 mes"].value is None  # la base más cercana es de 2 meses atrás
    monthly = [(date(2026, 1, 1), 100.0), (date(2026, 2, 1), 102.0), (date(2026, 3, 1), 103.02)]
    one = {c.label: c for c in changes(monthly, "pct")}["1 mes"]
    assert one.base is not None and one.base.date == date(2026, 2, 1)  # mes calendario
    assert one.value == pytest.approx(0.01)


def test_months_back_clamps_day() -> None:
    from app.services.liquidity import months_back

    assert months_back(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert months_back(date(2026, 1, 15), 12) == date(2025, 1, 15)


def test_basket_ratio() -> None:
    d = [date(2026, 1, i) for i in (2, 3, 4)]
    cyc = [[(d[0], 10.0), (d[1], 11.0), (d[2], 12.0)], [(d[0], 20.0), (d[1], 22.0), (d[2], 24.0)]]
    dfn = [[(d[0], 5.0), (d[1], 5.0), (d[2], 5.0)]]
    out = basket_ratio(cyc, dfn)
    assert out == [(d[0], 100.0), (d[1], pytest.approx(110.0)), (d[2], pytest.approx(120.0))]
    assert on_or_before(out, date(2026, 1, 1)) is None


def fred_csv(
    series_id: str, rows: list[tuple[str, float]]
) -> Callable[[httpx.Request], httpx.Response]:
    body = f"observation_date,{series_id}\n" + "".join(f"{d},{v}\n" for d, v in rows)
    return lambda _r: httpx.Response(200, text=body)


async def test_liquidity_dashboard(auth_client: httpx.AsyncClient, router: Router) -> None:
    router.add("fred.test", lambda _r: httpx.Response(500))  # todo lo demás falla
    weeks = [date(2025, 10, 1) + timedelta(days=7 * i) for i in range(60)]
    router.add(
        "id=WALCL&",
        fred_csv("WALCL", [(w.isoformat(), 6_700_000 - i * 1000) for i, w in enumerate(weeks)]),
    )
    router.add("id=WTREGEN&", fred_csv("WTREGEN", [(w.isoformat(), 800_000) for w in weeks]))
    router.add("id=RRPONTSYD&", fred_csv("RRPONTSYD", [(w.isoformat(), 100) for w in weeks]))
    router.json("/v8/finance/chart/DX-Y.NYB", yahoo_payload([100.0, 101.0, 102.0]))

    r = await auth_client.get("/api/liquidity/dashboard")
    assert r.status_code == 200, r.text
    items = {i["key"]: i for i in r.json()["indicators"]}
    net = items["net_liquidity"]
    assert net["status"] == "OK"
    assert net["latest"]["value"] == pytest.approx(6700 - 59 - 800 - 100)
    assert net["unit"] == "miles de millones de USD"
    one_year = next(c for c in net["changes"] if c["label"] == "1 año")
    assert one_year["value"] == pytest.approx(5741 / 5794 - 1)  # base: 12/11/2025
    assert items["dxy"]["latest"]["value"] == 102.0
    assert items["hy_spread"]["status"] == "ERROR_FUENTE"  # una falla no tumba el tablero

    r = await auth_client.get(
        "/api/liquidity/series", params={"key": "net_liquidity", "start": "2026-11-01"}
    )
    assert r.status_code == 200
    assert all(p["date"] >= "2026-11-01" for p in r.json()["points"])
    assert (await auth_client.get("/api/liquidity/series", params={"key": "x"})).status_code == 422


# ---------- Calendario y sorpresas ----------


def test_parse_value() -> None:
    assert parse_value("0.3%") == (0.3, "%")
    assert parse_value("-12.5K") == (-12.5, "K")
    assert parse_value("180K") == (180.0, "K")
    assert parse_value("<0.1%") is None  # no es un número exacto
    assert parse_value("") is None and parse_value(None) is None


def test_reference_month_and_actual() -> None:
    assert reference_month(datetime(2026, 11, 12, 13, 30, tzinfo=UTC)) == date(2026, 10, 1)
    assert reference_month(datetime(2026, 1, 9, 13, 30, tzinfo=UTC)) == date(2025, 12, 1)

    months = [date(2025, m, 1) for m in range(1, 13)] + [date(2026, m, 1) for m in range(1, 11)]
    cpi = FredSeries("CPIAUCSL", months, [300 + i * 0.6 for i in range(len(months))], 0)
    ref = date(2026, 10, 1)
    mom = compute_actual(cpi, SPEC_BY_KEY["cpi_mom"], ref)
    assert mom == round((312.6 / 312.0 - 1) * 100, 1)
    yoy = compute_actual(cpi, SPEC_BY_KEY["cpi_yoy"], ref)
    assert yoy == round((312.6 / 305.4 - 1) * 100, 1)
    assert compute_actual(cpi, SPEC_BY_KEY["cpi_mom"], date(2026, 11, 1)) is None  # no salió

    nfp = FredSeries("PAYEMS", [date(2026, 9, 1), date(2026, 10, 1)], [159_000.0, 159_180.0], 0)
    assert compute_actual(nfp, SPEC_BY_KEY["nfp"], ref) == 180


def ff_event(
    title: str, when: str, forecast: str, impact: str = "High", country: str = "USD"
) -> dict[str, str]:
    return {
        "title": title,
        "country": country,
        "date": when,
        "impact": impact,
        "forecast": forecast,
        "previous": "0.2%",
    }


@pytest.fixture
def services(app: FastAPI) -> Services:
    s: Services = app.state.services
    return s


async def test_calendar_flow_with_surprise_alert(
    auth_client: httpx.AsyncClient, router: Router, services: Services
) -> None:
    router.json(
        "ff.test",
        [
            ff_event("CPI m/m", "2026-11-12T08:30:00-05:00", "0.2%"),
            ff_event("Core CPI m/m", "2026-11-12T08:30:00-05:00", "0.3%"),
            ff_event("ECB Press Conference", "2026-11-12T08:45:00-05:00", "", country="EUR"),
            ff_event("Low thing", "2026-11-12T10:00:00-05:00", "1", impact="Low"),
        ],
    )
    run = await run_job(JOBS_BY_ID["calendario_economico"], services)
    assert run.status == "ok"
    assert run.detail is not None and run.detail.startswith("3 eventos nuevos")
    # Correr de nuevo no duplica.
    assert (await services.calendar.refresh()) == (0, 0)

    months = [date(2025, m, 1) for m in range(1, 13)] + [date(2026, m, 1) for m in range(1, 11)]
    router.add(
        "id=CPIAUCSL&",
        fred_csv("CPIAUCSL", [(m.isoformat(), 300 + i * 0.6) for i, m in enumerate(months)]),
    )
    router.add(
        "id=CPILFESL&",
        fred_csv("CPILFESL", [(m.isoformat(), 310 + i * 0.93) for i, m in enumerate(months)]),
    )

    after = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
    surprises = await services.calendar.capture_actuals(now=after)
    # IPC: real 0,2 vs 0,2 → sin sorpresa. Núcleo: real 0,3 vs 0,3 → tampoco.
    assert surprises == []

    cal = await services.calendar.calendar(now=after)
    by_title = {e.title: e for e in cal.recent}
    cpi = by_title["CPI m/m"]
    assert cpi.actual == 0.2 and cpi.surprise == 0.0
    assert cpi.surprise_note == "en línea con lo esperado"
    assert all(e.tracked for e in cal.recent)  # sólo eventos con seguimiento


async def test_surprise_triggers_whatsapp_once(
    auth_client: httpx.AsyncClient, router: Router, services: Services
) -> None:
    router.json(
        "ff.test", [ff_event("Non-Farm Employment Change", "2026-11-06T08:30:00-05:00", "100K")]
    )
    await services.calendar.refresh()
    router.add("id=PAYEMS&", fred_csv("PAYEMS", [("2026-09-01", 159_000), ("2026-10-01", 159_230)]))
    after = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)
    surprises = await services.calendar.capture_actuals(now=after)
    assert len(surprises) == 1
    msg = surprises[0].message()
    assert "Empleos no agrícolas" in msg and "230 mil vs 100 mil esperado" in msg
    assert "+130 mil" in msg and "más fuerte" in msg
    # Una segunda pasada no vuelve a avisar.
    assert await services.calendar.capture_actuals(now=after + timedelta(hours=1)) == []


async def test_actual_not_captured_before_release(
    auth_client: httpx.AsyncClient, router: Router, services: Services
) -> None:
    router.json("ff.test", [ff_event("Unemployment Rate", "2026-12-04T08:30:00-05:00", "4.3%")])
    await services.calendar.refresh()
    surprises = await services.calendar.capture_actuals(
        now=datetime(2026, 12, 4, 13, 0, tzinfo=UTC)
    )
    assert surprises == []  # 08:00 de NY: todavía no se publicó, ni se consulta FRED
    assert not any("UNRATE" in str(c.url) for c in router.calls)


async def test_calendar_endpoint(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/calendar")
    assert r.status_code == 200
    assert r.json() == {"upcoming": [], "recent": [], "last_refresh": None}
