from collections.abc import Callable
from datetime import date, timedelta

import httpx
import pytest

from app.services.us_macro import INDICATORS, YIELD_CURVE, transform_series, value_on_or_before
from app.sources.errors import NotFoundError
from app.sources.fred import FredSeries, parse_csv
from tests.conftest import Router


def csv_response(
    series_id: str, rows: list[tuple[str, str]], header: str = "observation_date"
) -> Callable[[httpx.Request], httpx.Response]:
    body = f"{header},{series_id}\n" + "\n".join(f"{d},{v}" for d, v in rows) + "\n"
    return lambda _req: httpx.Response(200, text=body, headers={"content-type": "text/csv"})


def monthly(start_year: int, values: list[float]) -> list[tuple[str, str]]:
    out = []
    for i, v in enumerate(values):
        y, m = start_year + i // 12, i % 12 + 1
        out.append((f"{y}-{m:02d}-01", str(v)))
    return out


# ---------- Parser ----------


def test_parse_csv_skips_missing_days() -> None:
    s = parse_csv(
        "DGS10",
        "observation_date,DGS10\n2024-01-01,.\n2024-01-02,3.95\n2024-01-03,\n2024-01-04,4.0\n",
    )
    assert s.dates == [date(2024, 1, 2), date(2024, 1, 4)]
    assert s.values == [3.95, 4.0]
    assert s.missing_points == 2


def test_parse_csv_accepts_legacy_header() -> None:
    s = parse_csv("UNRATE", "DATE,UNRATE\n2024-01-01,3.7\n")
    assert s.values == [3.7]


def test_parse_csv_html_means_not_found() -> None:
    with pytest.raises(NotFoundError):
        parse_csv("NOPE", "<!DOCTYPE html>\n<html>,error</html>")


# ---------- Transformaciones ----------


def test_yoy_pct_uses_same_month_previous_year() -> None:
    dates = [date(2023, m, 1) for m in range(1, 13)] + [date(2024, 1, 1), date(2024, 3, 1)]
    values = [100.0 + m for m in range(12)] + [103.0, 110.0]
    out, dropped = transform_series(dates, values, "yoy_pct")
    assert [o.date for o in out] == [date(2024, 1, 1), date(2024, 3, 1)]
    assert out[0].value == pytest.approx(3.0)  # 103 / 100 − 1
    assert out[1].value == pytest.approx((110 / 102 - 1) * 100)
    assert len(dropped) == 12  # todo 2023 no tiene año base


def test_yoy_pct_does_not_invent_missing_base_month() -> None:
    dates = [date(2023, 1, 1), date(2024, 1, 1), date(2024, 2, 1)]
    out, dropped = transform_series(dates, [100.0, 105.0, 106.0], "yoy_pct")
    assert [o.date for o in out] == [date(2024, 1, 1)]
    assert date(2024, 2, 1) in dropped  # falta feb 2023: se descarta, no se estima


def test_diff_requires_previous_month() -> None:
    dates = [date(2024, 1, 1), date(2024, 2, 1), date(2024, 4, 1)]
    out, dropped = transform_series(dates, [1000.0, 1250.0, 1300.0], "diff")
    assert [(o.date, o.value) for o in out] == [(date(2024, 2, 1), 250.0)]
    assert dropped == [date(2024, 1, 1), date(2024, 4, 1)]


def test_value_on_or_before() -> None:
    s = FredSeries("X", [date(2024, 1, 2), date(2024, 1, 5)], [1.0, 2.0], 0)
    assert value_on_or_before(s, date(2024, 1, 1)) is None
    found = value_on_or_before(s, date(2024, 1, 4))
    assert found is not None and found.value == 1.0
    found = value_on_or_before(s, date(2024, 2, 1))
    assert found is not None and found.date == date(2024, 1, 5)


# ---------- API ----------


async def test_series_endpoint_cpi_yoy(auth_client: httpx.AsyncClient, router: Router) -> None:
    router.add(
        "id=CPIAUCSL", csv_response("CPIAUCSL", monthly(2022, [100.0 + i for i in range(24)]))
    )
    r = await auth_client.get(
        "/api/us/series", params={"indicator": "cpi_yoy", "start": "2023-06-01"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["indicator"]["unit"] == "% interanual"
    assert body["observations"][0]["date"] == "2023-06-01"
    assert body["observations"][0]["value"] == pytest.approx((117 / 105 - 1) * 100)
    assert body["dropped_points"] == 0
    assert body["source"]["url"].endswith("/series/CPIAUCSL")
    # Se pide el historial completo una vez; el rango se recorta en el backend.
    assert "cosd=1999-01-01" in str(router.calls[0].url)


async def test_series_validation(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/us/series", params={"indicator": "nada"})
    assert r.status_code == 422


async def test_dashboard_mixes_ok_and_failures(
    auth_client: httpx.AsyncClient, router: Router
) -> None:
    router.add("fred.test", lambda _r: httpx.Response(503, text="caído"))
    router.add("id=UNRATE", csv_response("UNRATE", [("2024-09-01", "4.1"), ("2024-10-01", "4.2")]))
    router.add(
        "id=PAYEMS", csv_response("PAYEMS", [("2024-09-01", "159000"), ("2024-10-01", "159150")])
    )
    r = await auth_client.get("/api/us/dashboard")
    assert r.status_code == 200, r.text
    items = {i["indicator"]["key"]: i for i in r.json()["items"]}
    assert len(items) == len(INDICATORS)
    assert items["unemployment"]["status"] == "OK"
    assert items["unemployment"]["latest"] == {"date": "2024-10-01", "value": 4.2}
    assert items["unemployment"]["change"] == pytest.approx(0.1)
    assert items["payrolls"]["latest"]["value"] == pytest.approx(150.0)
    assert items["payrolls"]["previous"] is None  # sólo hay un mes con base
    assert items["vix"]["status"] == "ERROR_FUENTE"
    assert items["vix"]["latest"] is None


async def test_yield_curve(auth_client: httpx.AsyncClient, router: Router) -> None:
    last = date(2026, 11, 5)
    days = [last - timedelta(days=i) for i in range(400)][::-1]
    for i, (_, sid) in enumerate(YIELD_CURVE):
        rows = [(d.isoformat(), f"{3 + i * 0.1:.2f}") for d in days]
        router.add(f"id={sid}&", csv_response(sid, rows))
    # El de 20 años dejó de publicarse hace 2 meses: no debe aparecer como "último dato".
    old = [(d.isoformat(), "4.50") for d in days if d < last - timedelta(days=60)]
    router.add("id=DGS20&", csv_response("DGS20", old))

    r = await auth_client.get("/api/us/yield-curve")
    assert r.status_code == 200, r.text
    curves = r.json()["curves"]
    assert [c["label"] for c in curves] == ["Último dato", "Hace 1 mes", "Hace 1 año"]
    today = {p["maturity"]: p for p in curves[0]["points"]}
    assert curves[0]["target_date"] == last.isoformat()
    assert today["10A"]["value"] == pytest.approx(3.8)
    assert today["20A"]["status"] == "NO_DISPONIBLE"
    assert today["20A"]["value"] is None
    year_ago = {p["maturity"]: p for p in curves[2]["points"]}
    assert year_ago["20A"]["value"] == 4.5


async def test_yield_curve_all_down(auth_client: httpx.AsyncClient, router: Router) -> None:
    router.add("fred.test", lambda _r: httpx.Response(500))
    r = await auth_client.get("/api/us/yield-curve")
    assert r.status_code == 502
