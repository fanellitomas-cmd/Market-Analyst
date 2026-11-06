from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.main import create_app
from app.services.earnings import base_index, classify_session, quarterly_series
from app.sources.sec import Fact
from tests.conftest import PASSWORD, Router, make_settings, yahoo_payload


def fact(start: str, end: str, value: float, filed: str = "2024-02-01") -> Fact:
    return Fact(
        date.fromisoformat(start), date.fromisoformat(end), value, date.fromisoformat(filed), "10-Q"
    )


# ---------- Trimestres ----------


def test_q4_is_derived_from_annual_minus_three_quarters() -> None:
    facts = [
        fact("2023-01-01", "2023-03-31", 100),
        fact("2023-04-01", "2023-06-30", 110),
        fact("2023-07-01", "2023-09-30", 120),
        fact("2023-01-01", "2023-12-31", 460, filed="2024-02-20"),
        fact("2023-01-01", "2023-06-30", 210),  # semestre acumulado: se ignora
    ]
    out = quarterly_series([facts], additive=True)
    assert [(q.end.isoformat(), q.value, q.derived) for q in out] == [
        ("2023-03-31", 100, False),
        ("2023-06-30", 110, False),
        ("2023-09-30", 120, False),
        ("2023-12-31", 130, True),
    ]


def test_q4_not_derived_when_a_quarter_is_missing_or_not_additive() -> None:
    facts = [
        fact("2023-01-01", "2023-03-31", 100),
        fact("2023-07-01", "2023-09-30", 120),
        fact("2023-01-01", "2023-12-31", 460),
    ]
    assert all(not q.derived for q in quarterly_series([facts], additive=True))
    full = [*facts, fact("2023-04-01", "2023-06-30", 110)]
    assert all(not q.derived for q in quarterly_series([full], additive=False))


def test_latest_filing_wins_and_concept_priority() -> None:
    old = fact("2024-01-01", "2024-03-31", 100, filed="2024-05-01")
    restated = fact("2024-01-01", "2024-03-31", 95, filed="2025-05-01")
    fallback = fact("2024-01-01", "2024-03-31", 999)
    other_period = fact("2023-01-01", "2023-03-31", 80)
    out = quarterly_series([[old, restated], [fallback, other_period]], additive=False)
    assert [(q.end.year, q.value) for q in out] == [(2023, 80), (2024, 95)]


# ---------- Sesión y rueda base ----------


def ny(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    # 5 h de diferencia en invierno (EST)
    return datetime(y, m, d, hh + 5, mm, tzinfo=UTC).astimezone(ZoneInfo("America/New_York"))


def test_classify_session() -> None:
    assert classify_session(ny(2024, 1, 25, 16, 5)) == "después del cierre"
    assert classify_session(ny(2024, 1, 25, 7, 0)) == "antes de la apertura"
    assert classify_session(ny(2024, 1, 25, 12, 0)) == "durante la rueda"
    assert classify_session(None) == "desconocido"


def test_base_index() -> None:
    dates = [date(2024, 1, 22) + timedelta(days=i) for i in range(5)]  # lun a vie
    assert base_index(dates, date(2024, 1, 24), after_close=True) == 2
    assert base_index(dates, date(2024, 1, 24), after_close=False) == 1
    assert base_index(dates, date(2024, 1, 22), after_close=False) is None
    assert base_index(dates, date(2024, 3, 1), after_close=True) is None  # muy lejos


# ---------- API ----------


def usd(rows: list[tuple[str, str, float]]) -> list[dict[str, Any]]:
    return [
        {"start": s, "end": e, "val": v, "filed": "2024-11-01", "form": "10-Q"} for s, e, v in rows
    ]


COMPANY_FACTS = {
    "cik": 1234,
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": usd(
                        [
                            ("2023-07-01", "2023-09-30", 1000),
                            ("2024-07-01", "2024-09-30", 1200),
                        ]
                    )
                }
            },
            "GrossProfit": {"units": {"USD": usd([("2024-07-01", "2024-09-30", 480)])}},
            "NetIncomeLoss": {
                "units": {
                    "USD": usd(
                        [
                            ("2023-07-01", "2023-09-30", -50),
                            ("2024-07-01", "2024-09-30", 150),
                        ]
                    )
                }
            },
            "EarningsPerShareDiluted": {
                "units": {
                    "USD/shares": usd(
                        [
                            ("2023-07-01", "2023-09-30", -0.5),
                            ("2024-07-01", "2024-09-30", 1.5),
                        ]
                    )
                }
            },
        }
    },
}

SUBMISSIONS = {
    "cik": "1234",
    "filings": {
        "recent": {
            "form": ["10-Q", "8-K", "8-K", "8-K/A"],
            "filingDate": ["2024-11-01", "2024-01-03", "2024-01-25", "2024-01-25"],
            # 21:05 UTC = 16:05 en Nueva York: después del cierre
            "acceptanceDateTime": [
                "2024-11-01T20:00:00.000Z",
                "2024-01-03T13:00:00.000Z",
                "2024-01-25T21:05:00.000Z",
                "2024-01-25T23:00:00.000Z",
            ],
            "items": ["", "5.02", "2.02,9.01", "2.02"],
            "accessionNumber": ["a-1", "a-2", "0001234-24-000010", "a-4"],
            "primaryDocument": ["q.htm", "k.htm", "k2.htm", "k3.htm"],
        }
    },
}


@pytest.fixture
def sec_sources(router: Router) -> None:
    router.json(
        "sec.test/files/company_tickers.json",
        {
            "0": {"cik_str": 1234, "ticker": "TEST", "title": "Test Corp"},
        },
    )
    router.json("companyfacts/CIK0000001234.json", COMPANY_FACTS)
    router.json("submissions/CIK0000001234.json", SUBMISSIONS)
    # Precios diarios desde el 22/01/2024 (14:30 UTC). Rueda base = 25/01 (índice 3).
    start = datetime(2024, 1, 22, 14, 30, tzinfo=UTC)
    stock = [100.0, 100, 100, 100, 110, 111, 112, 113, 114, 115] + [120.0] * 20
    spy = [400.0, 400, 400, 400, 404, 404, 404, 404, 404, 408] + [410.0] * 20
    router.json("/v8/finance/chart/TEST", yahoo_payload(list[float | None](stock), start))
    router.json("/v8/finance/chart/SPY", yahoo_payload(list[float | None](spy), start))


@pytest.mark.usefixtures("sec_sources")
async def test_earnings_endpoint(auth_client: httpx.AsyncClient, router: Router) -> None:
    r = await auth_client.get("/api/earnings/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company"] == {"ticker": "TEST", "name": "Test Corp", "cik": 1234}

    q = body["quarters"][0]
    assert q["period_end"] == "2024-09-30"
    assert q["revenue"]["value"] == 1200
    assert q["revenue_yoy"]["value"] == pytest.approx(0.2)
    assert q["gross_margin"]["value"] == pytest.approx(0.4)
    assert q["operating_margin"]["status"] == "NO_DISPONIBLE"  # no reportado: no se inventa
    assert q["net_margin"]["value"] == pytest.approx(0.125)
    assert q["eps_diluted"]["value"] == 1.5
    assert q["eps_yoy"]["status"] == "NO_DISPONIBLE"  # base negativa

    events = body["events"]
    assert len(events) == 1  # el 8-K sin 2.02 se ignora y la enmienda del mismo día no duplica
    ev = events[0]
    assert ev["session"] == "después del cierre"
    assert ev["base_date"] == "2024-01-25"
    one = ev["reactions"][0]
    assert one["horizon"] == 1
    assert one["stock"] == pytest.approx(0.10)
    assert one["benchmark"] == pytest.approx(0.01)
    assert one["excess"] == pytest.approx(0.09)
    assert ev["reactions"][2]["stock"] == pytest.approx(0.20)  # 20 ruedas
    assert ev["filing_url"].endswith("/1234/000123424000010/")
    assert body["avg_abs_move_1d"]["value"] == pytest.approx(0.10)

    sec_calls = [c for c in router.calls if "sec" in c.url.host]
    assert all(c.headers["user-agent"] == "Test Runner test@example.com" for c in sec_calls)


@pytest.mark.usefixtures("sec_sources")
async def test_unknown_ticker(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/earnings/NOPE")
    assert r.status_code == 404
    assert "SEC" in r.json()["detail"]


async def test_requires_sec_user_agent() -> None:
    app = create_app(make_settings(sec_user_agent=""))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        await c.post("/api/auth/login", json={"username": "tomas", "password": PASSWORD})
        r = await c.get("/api/earnings/AAPL")
        assert r.status_code == 503
        assert "SEC_USER_AGENT" in r.json()["detail"]
