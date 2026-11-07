"""Paso 7: tesis con kill switch doble y cadencia (pre-market, alertas, cierre)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from app.container import Services
from app.core.db import Thesis
from app.services.cadence import session_open, volume_zscore
from app.services.technical import (
    Avwap,
    Regime,
    TechnicalResponse,
    VolumeStats,
)
from app.services.thesis import evaluate
from app.sources.sec import parse_current_feed
from app.sources.yahoo import Bar
from tests.conftest import Router, yahoo_payload

NY = ZoneInfo("America/New_York")

# ---------- Feed de 8-K ----------

SUMMARY = (
    "&lt;b&gt;Filed:&lt;/b&gt; 2026-11-06 &lt;b&gt;AccNo:&lt;/b&gt; 0000000009-26-000"
    "123 &lt;b&gt;Size:&lt;/b&gt; 3 MB&lt;br&gt;Item 2.02: Results of Operations and "
    "Financial Condition&lt;br&gt;Item 9.01: Financial Statements and Exhibits"
)

FEED = f"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - ACME CORP (0000000009) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/9/000000000926000123/0000000009-26-000123-index.htm"/>
<summary type="html"> {SUMMARY}</summary>
<updated>2026-11-06T16:30:41-05:00</updated>
</entry>
<entry>
<title>8-K - SIN CIK</title>
<summary type="html">nada</summary>
</entry>
</feed>"""


def test_parse_current_feed() -> None:
    [f] = parse_current_feed(FEED)
    assert f.cik == 9 and f.company == "ACME CORP" and f.form == "8-K"
    assert f.items == ("2.02", "9.01")
    assert f.accession == "0000000009-26-000123"
    assert f.updated is not None and f.updated.utcoffset() == timedelta(hours=-5)


# ---------- Volumen a la misma hora ----------


def five_min(day: date, n: int, vol: float, price: float = 100.0) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    return [
        Bar(start + timedelta(minutes=5 * i), price, price + 1, price - 1, price, vol)
        for i in range(n)
    ]


def test_volume_zscore_compares_same_time_of_day() -> None:
    bars: list[Bar] = []
    for i, d in enumerate(date(2026, 10, 26) + timedelta(days=k) for k in range(7)):
        bars += five_min(d, 78, 1000 + 50 * (i % 3))  # sesiones completas
    today = date(2026, 11, 6)
    bars += five_min(today, 12, 5000)  # primera hora con 5 veces el volumen
    now = datetime(2026, 11, 6, 10, 30, tzinfo=NY)
    z, today_vol, used = volume_zscore(bars, now)
    assert used == 7
    assert today_vol == 12 * 5000
    assert z is not None and z > 2.5
    z_none, _, _ = volume_zscore(bars[-12:], now)
    assert z_none is None  # sin historia no hay comparación


def test_session_open() -> None:
    assert session_open(datetime(2026, 11, 6, 15, 0, tzinfo=UTC))  # 10:00 NY
    assert not session_open(datetime(2026, 11, 6, 22, 0, tzinfo=UTC))  # 17:00 NY
    assert not session_open(datetime(2026, 11, 26, 15, 0, tzinfo=UTC))  # Acción de Gracias


# ---------- Tesis y kill switch ----------


def tech(close: float, avwap: float | None, rel_vol: float | None) -> TechnicalResponse:
    return TechnicalResponse(
        symbol="ACME",
        name=None,
        currency="USD",
        as_of=date(2026, 11, 6),
        close=close,
        regime=Regime(
            kind="uptrend",
            label="Tendencia alcista",
            explanation="",
            ema20=None,
            ema50=None,
            ema200=None,
            adx=None,
            bb_width=None,
            bb_width_percentile=None,
            conditions=[],
        ),
        volume=VolumeStats(last=None, avg50=None, zscore=None, relative=rel_vol),
        avwaps=[
            Avwap(
                key="earnings",
                label="Último balance",
                anchor=None,
                value=avwap,
                distance=None,
                status="OK" if avwap else "NO_DISPONIBLE",
                note=None if avwap else "sin fecha de balance",
                series=[],
            )
        ],
        profile=None,
        profile_note=None,
        candles=[],
        emas=[],
        notes=[],
        sources=[],
    )


def thesis(**kw: Any) -> Thesis:
    base: dict[str, Any] = {
        "ticker": "ACME",
        "hypothesis": "x",
        "min_operating_margin": 20.0,
        "support": 100.0,
        "use_earnings_avwap": True,
        "baseline_quarter_end": date(2026, 6, 30),
    }
    base.update(kw)
    return Thesis(**base)


def test_thesis_validation_checks() -> None:
    c = evaluate(thesis(), tech(110, 105, 1.0), 1.5, "segura", (date(2026, 6, 30), 0.25))
    assert [x.status for x in c.checks] == ["cumple"] * 5
    assert not c.kill_fundamental.triggered and not c.kill_technical.triggered


def test_fundamental_kill_switch_needs_a_new_balance() -> None:
    old = evaluate(thesis(), tech(110, 105, 1.0), 1.5, "segura", (date(2026, 6, 30), 0.15))
    assert not old.kill_fundamental.triggered  # mismo balance que al crear la tesis
    new = evaluate(thesis(), tech(110, 105, 1.0), 1.5, "segura", (date(2026, 9, 30), 0.15))
    assert new.kill_fundamental.triggered and "15.0 %" in new.kill_fundamental.detail
    ok = evaluate(thesis(), tech(110, 105, 1.0), 1.5, "segura", (date(2026, 9, 30), 0.22))
    assert not ok.kill_fundamental.triggered


def test_technical_kill_switch() -> None:
    no_volume = evaluate(thesis(use_earnings_avwap=False), tech(95, None, 1.1), None, None, None)
    assert not no_volume.kill_technical.triggered
    assert no_volume.warnings and "sin volumen" in no_volume.warnings[0]
    with_volume = evaluate(thesis(use_earnings_avwap=False), tech(95, None, 2.0), None, None, None)
    assert with_volume.kill_technical.triggered
    lost_avwap = evaluate(thesis(), tech(104, 106, 0.8), None, None, None)
    assert lost_avwap.kill_technical.triggered and "AVWAP" in lost_avwap.kill_technical.detail
    # Sin datos fundamentales: "sin dato", nunca dispara.
    assert [x.status for x in lost_avwap.checks[:3]] == ["sin dato"] * 3
    assert not lost_avwap.kill_fundamental.triggered


async def test_thesis_api_flow(
    app: FastAPI, auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services: Services = app.state.services
    state = {"tech": tech(110, 105, 1.0), "quarter": (date(2026, 6, 30), 0.25)}

    async def fake_inputs(ticker: str) -> tuple[Any, Any, Any, Any]:
        return state["tech"], 1.4, "segura", state["quarter"]

    monkeypatch.setattr(services.theses, "_inputs", fake_inputs)
    body = {
        "ticker": "acme",
        "hypothesis": "Cotiza 30 % debajo de su P/E histórico.",
        "min_operating_margin": 20,
        "support": 100,
    }
    r = await auth_client.post("/api/theses", json=body)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["ticker"] == "ACME" and t["status"] == "activa"
    assert t["baseline_quarter_end"] == "2026-06-30"
    assert (
        await auth_client.post("/api/theses", json={**body, "hypothesis": "corta"})
    ).status_code == 422

    # Nuevo balance con margen bajo: el cierre invalida la tesis.
    state["quarter"] = (date(2026, 9, 30), 0.12)
    invalidated = await services.theses.check_all_active()
    assert len(invalidated) == 1 and "12.0 %" in invalidated[0][1]
    listed = (await auth_client.get("/api/theses")).json()
    assert listed[0]["status"] == "invalidada"
    assert listed[0]["last_check"]["kill_fundamental"]["triggered"] is True

    assert (await auth_client.delete(f"/api/theses/{t['id']}")).status_code == 204
    assert (await auth_client.post(f"/api/theses/{t['id']}/check")).status_code == 404


# ---------- Alertas intradía ----------


def five_min_payload(bars: list[Bar]) -> dict[str, Any]:
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "exchangeTimezoneName": "America/New_York"},
                    "timestamp": [int(b.time.timestamp()) for b in bars],
                    "indicators": {
                        "quote": [
                            {
                                "open": [b.open for b in bars],
                                "high": [b.high for b in bars],
                                "low": [b.low for b in bars],
                                "close": [b.close for b in bars],
                                "volume": [b.volume for b in bars],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


async def test_intraday_triggers(
    app: FastAPI, auth_client: httpx.AsyncClient, router: Router
) -> None:
    services: Services = app.state.services
    await services.options.set_watchlist(["ACME"])
    bars: list[Bar] = []
    for k, d in enumerate(date(2026, 10, 27) + timedelta(days=k) for k in range(9)):
        if d.weekday() < 5:
            bars += five_min(d, 78, 1000 + 80 * (k % 3))  # volumen normal con algo de variación
    bars += five_min(date(2026, 11, 6), 12, 6000, price=110)  # rompe el máximo de ayer (101)
    router.json("/chart/ACME?range=1mo&interval=5m", five_min_payload(bars))
    router.json(
        "/chart/ACME?range=1y&interval=1d", five_min_payload(five_min(date(2026, 1, 5), 1, 1))
    )
    router.json(
        "sec.test/files/company_tickers.json",
        {"0": {"cik_str": 9, "ticker": "ACME", "title": "Acme"}},
    )
    router.add("browse-edgar?action=getcurrent", lambda _r: httpx.Response(200, text=FEED))

    now = datetime(2026, 11, 6, 15, 30, tzinfo=UTC)  # 10:30 NY
    new = await services.cadence.triggers(now=now)
    kinds = {t.kind: t for t in new}
    assert set(kinds) == {"volumen", "8-K"}
    vol = kinds["volumen"]
    assert vol.fact.startswith("rompe hacia arriba el máximo de ayer (101.00)")
    assert "resultados" in vol.cause  # el 8-K de hoy explica el movimiento
    assert "debajo de" in vol.stop
    assert "2.02 resultados" in kinds["8-K"].fact
    # La misma alerta no se repite en el día.
    assert await services.cadence.triggers(now=now + timedelta(minutes=15)) == []
    # Con la bolsa cerrada no se evalúa nada.
    assert await services.cadence.triggers(now=datetime(2026, 11, 7, 15, 0, tzinfo=UTC)) == []

    today = (await auth_client.get("/api/cadence/today")).json()
    assert {t["kind"] for t in today["triggers"]} == {"volumen", "8-K"}
    assert today["watchlist"] == ["ACME"]


# ---------- Cierre ----------


async def test_ledger(app: FastAPI, auth_client: httpx.AsyncClient, router: Router) -> None:
    services: Services = app.state.services
    start = datetime(2025, 11, 3, 14, 30, tzinfo=UTC)
    router.json("/chart/", yahoo_payload([100 + i * 0.1 for i in range(260)], start))
    router.json("/chart/XLE", yahoo_payload([100 - i * 0.1 for i in range(260)], start))
    report, message = await services.cadence.ledger()
    assert report.benchmarks[0].symbol == "SPY"
    assert report.sectors[-1].symbol == "XLE"  # el peor del día queda último
    xle = report.sectors[-1]
    assert xle.above_ema200 is False and xle.vs_spy_1d is not None and xle.vs_spy_1d < 0
    assert report.rotation is not None
    assert message.startswith("🌙 Cierre") and "Peor: Energía" in message
    today = (await auth_client.get("/api/cadence/today")).json()
    assert today["ledger"]["sectors"][-1]["symbol"] == "XLE"
