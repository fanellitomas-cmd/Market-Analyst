"""Columna 4: centinela (cripto, commodities y arbitraje ADR/local)."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.services.sentinel import annualize, change_since, funding_reading, implied_fx
from tests.conftest import Router, yahoo_payload


def okx(data: list[Any]) -> dict[str, Any]:
    return {"code": "0", "msg": "", "data": data}


def ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


# ---------- Cálculos ----------


def test_funding_annualization_and_reading() -> None:
    assert annualize(0.0001) == pytest.approx(0.1095)  # 0,01 % cada 8 h ≈ 11 % anual
    assert funding_reading(0.35).startswith("apalancamiento comprado muy alto")
    assert funding_reading(0.05) == "neutral"
    assert funding_reading(-0.02).startswith("predominan los vendidos")


def test_implied_fx() -> None:
    # GGAL: 10 acciones locales a $ 6.000 = US$ 40 → CCL implícito $ 1.500
    assert implied_fx(6000, 10, 40) == pytest.approx(1500)


def test_change_since_requires_close_base() -> None:
    d = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    c = [100.0 + i for i in range(40)]
    assert change_since(d, c, 7) == pytest.approx(139 / 132 - 1)
    assert change_since(d, c, 365) is None  # no hay un año de historia


# ---------- API: cripto ----------


async def test_crypto_endpoint(auth_client: httpx.AsyncClient, router: Router) -> None:
    now = datetime.now(UTC)
    router.add(
        "okx.test", lambda _r: httpx.Response(200, json={"code": "50011", "msg": "rate limit"})
    )
    router.json("ticker?instId=BTC-USDT-SWAP", okx([{"last": "100000"}]))
    router.json(
        "funding-rate?instId=BTC-USDT-SWAP",
        okx([{"fundingRate": "0.0003", "fundingTime": ms(now)}]),
    )
    router.json(
        "funding-rate-history?instId=BTC-USDT-SWAP",
        okx(
            [
                {
                    "fundingRate": "0.0001",
                    "realizedRate": "0.0001",
                    "fundingTime": ms(now - timedelta(hours=8 * i)),
                }
                for i in range(30)
            ]
        ),
    )
    router.json(
        "open-interest?instType=SWAP&instId=BTC-USDT-SWAP",
        okx([{"oi": "200000", "oiCcy": "2000", "ts": ms(now)}]),
    )
    router.json(
        "open-interest-volume?ccy=BTC",
        okx(
            [
                [ms(now - timedelta(days=i)), str(1_000_000_000 * (1 + (7 - i) * 0.01)), "5"]
                for i in range(10)
            ]
        ),
    )
    router.json("instruments?instType=SWAP&instId=BTC-USDT-SWAP", okx([{"ctVal": "0.01"}]))
    router.json(
        "liquidation-orders?instType=SWAP&uly=BTC-USDT",
        okx(
            [
                {
                    "details": [
                        {
                            "posSide": "long",
                            "sz": "100",
                            "bkPx": "99000",
                            "ts": ms(now - timedelta(hours=1)),
                        },
                        {
                            "posSide": "short",
                            "sz": "50",
                            "bkPx": "101000",
                            "ts": ms(now - timedelta(hours=2)),
                        },
                        {
                            "posSide": "long",
                            "sz": "999",
                            "bkPx": "90000",
                            "ts": ms(now - timedelta(days=3)),
                        },  # viejo
                    ]
                }
            ]
        ),
    )

    r = await auth_client.get("/api/sentinel/crypto")
    assert r.status_code == 200, r.text
    assets = {a["coin"]: a for a in r.json()["assets"]}
    btc = assets["BTC"]
    assert btc["price"] == 100000
    assert btc["funding_annualized"] == pytest.approx(0.0003 * 3 * 365)
    assert btc["funding_avg_7d"] == pytest.approx(annualize(0.0001))
    assert btc["open_interest_usd"] == pytest.approx(2000 * 100000)  # sin oiUsd: monedas × precio
    assert btc["open_interest_change_7d"] == pytest.approx(1.07 / 1.0 - 1, rel=1e-6)
    assert btc["liq_long_usd"] == pytest.approx(100 * 0.01 * 99000)
    assert btc["liq_short_usd"] == pytest.approx(50 * 0.01 * 101000)
    assert btc["liq_count"] == 2
    assert btc["long_short_ratio"] is None and any("largos/cortos" in e for e in btc["errors"])
    eth = assets["ETH"]
    assert eth["price"] is None and len(eth["errors"]) == 8  # todo falla, sin romper la página


# ---------- API: commodities ----------


async def test_commodities_endpoint(auth_client: httpx.AsyncClient, router: Router) -> None:
    start = datetime(2025, 11, 1, 14, 30, tzinfo=UTC)
    router.json("/chart/HG=F", yahoo_payload([5.0] * 200 + [5.5] * 60, start))
    router.json("/chart/GC=F", yahoo_payload([2500.0] * 260, start))
    r = await auth_client.get("/api/sentinel/commodities")
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {row["symbol"]: row for row in body["rows"]}
    copper = rows["HG=F"]
    assert copper["last"] == 5.5 and len(copper["spark"]) == 60
    assert {c["label"] for c in copper["changes"]} == {
        "1 día",
        "1 semana",
        "1 mes",
        "en el año",
        "1 año",
    }
    assert rows["CL=F"]["error"] is not None  # sin ruta simulada: error, no dato inventado
    assert body["copper_gold"][-1]["v"] == pytest.approx(5.5 / 2500 * 1000)
    assert body["copper_gold_change_3m"] == pytest.approx(0.1)


# ---------- API: arbitraje ----------


def quote(price: float, ts: datetime, currency: str) -> Callable[[httpx.Request], httpx.Response]:
    body = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "regularMarketTime": int(ts.timestamp()),
                        "currency": currency,
                        "chartPreviousClose": price,
                    }
                }
            ],
            "error": None,
        }
    }
    return lambda _r: httpx.Response(200, json=body)


async def test_arbitrage_endpoint(auth_client: httpx.AsyncClient, router: Router) -> None:
    t = datetime(2026, 11, 6, 19, 0, tzinfo=UTC)
    router.add("/chart/GGAL?", quote(40.0, t, "USD"))
    router.add("/chart/GGAL.BA?", quote(6000.0, t, "ARS"))
    router.add("/chart/YPF?", quote(30.0, t, "USD"))
    router.add("/chart/YPFD.BA?", quote(4650.0, t - timedelta(hours=5), "ARS"))  # desfasado
    router.add("/chart/BMA?", quote(80.0, t, "USD"))
    router.add("/chart/BMA.BA?", quote(12400.0, t, "ARS"))
    router.add("/chart/AAPL?", quote(250.0, t, "USD"))
    router.add("/chart/AAPL.BA?", quote(19000.0, t, "ARS"))
    router.add("/chart/MSFT?", quote(500.0, t, "USD"))
    router.add("/chart/MSFT.BA?", quote(20000.0, t, "ARS"))  # ratio real 30; cargado 300
    router.json(
        "dolarapi.test/v1/dolares",
        [{"casa": "contadoconliqui", "nombre": "CCL", "compra": 1490, "venta": 1500}],
    )

    r = await auth_client.put(
        "/api/sentinel/cedears",
        json=[
            {"local": "aapl.ba", "foreign": "aapl", "ratio": 20},
            {"local": "MSFT.BA", "foreign": "MSFT", "ratio": 300},
        ],
    )
    assert r.status_code == 200, r.text
    bad = await auth_client.put(
        "/api/sentinel/cedears", json=[{"local": "AAPL", "foreign": "AAPL", "ratio": 20}]
    )
    assert bad.status_code == 422

    r = await auth_client.get("/api/sentinel/arbitrage")
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {row["foreign"]: row for row in body["rows"]}
    assert body["ccl_market"] == 1500
    ggal = rows["GGAL"]
    assert ggal["implied_fx"] == pytest.approx(1500) and ggal["comparable"]
    assert ggal["vs_ccl"] == pytest.approx(0)
    ypf = rows["YPF"]
    assert not ypf["comparable"] and "5 h" in ypf["note"]
    # La mediana usa sólo ADRs con precios comparables: GGAL 1500 y BMA 1550.
    assert body["ccl_median_implied"] == pytest.approx(1525)
    aapl = rows["AAPL"]
    assert aapl["kind"] == "CEDEAR" and aapl["implied_fx"] == pytest.approx(19000 * 20 / 250)
    msft = rows["MSFT"]
    assert not msft["comparable"] and "probablemente es incorrecto" in msft["note"]
    assert rows["PAM"]["implied_fx"] is None and rows["PAM"]["note"].startswith("sin precio")


# ---------- Cinta en vivo ----------


def test_tnx_scale() -> None:
    from app.services.live import tnx_to_pct

    assert tnx_to_pct(4.25) == 4.25
    assert tnx_to_pct(42.5) == pytest.approx(4.25)


async def test_live_strip(auth_client: httpx.AsyncClient, router: Router) -> None:
    t = datetime(2026, 11, 6, 19, 0, tzinfo=UTC)
    router.add("GSPC?range", quote(6000.0, t, "USD"))
    router.add("TNX?range", quote(4.3, t, "USD"))
    router.json(
        "dolarapi.test/v1/dolares",
        [
            {
                "casa": "contadoconliqui",
                "nombre": "CCL",
                "compra": 1490,
                "venta": 1500,
                "fechaActualizacion": "2026-11-06T18:00:00Z",
            },
        ],
    )
    router.json(
        "argdatos.test/v1/finanzas/indices/riesgo-pais",
        [{"valor": 650, "fecha": "2026-11-05"}, {"valor": 640, "fecha": "2026-11-06"}],
    )
    r = await auth_client.get("/api/live/strip")
    assert r.status_code == 200, r.text
    items = {i["key"]: i for i in r.json()["items"]}
    assert items["spx"]["value"] == 6000 and items["spx"]["change"] == 0
    assert items["us10y"]["format"] == "pct" and items["us10y"]["change_kind"] == "pp"
    assert items["ndx"]["value"] is None and items["ndx"]["error"]  # sin ruta: no se inventa
    assert items["usd_contadoconliqui"]["value"] == 1500
    assert items["riesgo_pais"]["value"] == 640
    assert items["riesgo_pais"]["change"] == pytest.approx(640 / 650 - 1)


async def test_live_strip_marks_stale_series(
    auth_client: httpx.AsyncClient, router: Router
) -> None:
    old = datetime.now(UTC) - timedelta(days=20)
    router.add("MERV?range", quote(2_800_000.0, old, "ARS"))
    r = await auth_client.get("/api/live/strip")
    merval = next(i for i in r.json()["items"] if i["key"] == "merval")
    assert merval["stale"] is True
