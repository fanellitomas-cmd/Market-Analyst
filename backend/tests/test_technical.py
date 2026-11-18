"""Columna 3: motor técnico."""

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.services.technical import (
    adx,
    anchor_start,
    bollinger_width,
    classify,
    ema,
    percentile_rank,
    volume_profile,
    vwap,
)
from app.sources.yahoo import Bar
from tests.conftest import Router

NY = ZoneInfo("America/New_York")


def bar(t: datetime, o: float, h: float, lo: float, c: float, v: float | None = 1000.0) -> Bar:
    return Bar(time=t, open=o, high=h, low=lo, close=c, volume=v)


def day(i: int) -> datetime:
    return datetime(2025, 1, 2, 9, 30, tzinfo=NY) + timedelta(days=i)


# ---------- Indicadores ----------


def test_ema_on_a_ramp_lags_by_half_the_window() -> None:
    values = [float(i + 1) for i in range(30)]
    out = ema(values, 10)
    assert out[8] is None
    assert out[9] == pytest.approx(5.5)  # media simple de 1..10
    # En una rampa lineal, la EMA queda exactamente (n − 1) / 2 por debajo.
    assert all(out[i] == pytest.approx(values[i] - 4.5) for i in range(9, 30))


def test_adx_strong_trend_and_flat_market() -> None:
    trend = [bar(day(i), 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(60)]
    assert adx(trend)[-1] == pytest.approx(100.0)
    flat = [bar(day(i), 100, 101 + (i % 2), 99 - (i % 2), 100) for i in range(60)]
    value = adx(flat)[-1]
    assert value is not None and value < 1
    assert adx(trend[:20])[-1] is None  # historia insuficiente


def test_bollinger_width_and_percentile() -> None:
    assert bollinger_width([10.0] * 25)[-1] == 0.0
    w = bollinger_width([10.0, 12.0] * 10)[-1]
    # media 11, desvío poblacional 1 → (4 × 1) / 11
    assert w == pytest.approx(4 / 11)
    assert percentile_rank([1, 2, 3, 4], 2) == 0.5


def test_classify_regimes() -> None:
    assert classify(30, 20, 10, 30, 0.5) == "uptrend"
    assert classify(10, 20, 30, 30, 0.5) == "downtrend"
    assert classify(30, 20, 10, 20, 0.1) == "squeeze"  # medias ordenadas pero sin fuerza
    assert classify(30, 20, 10, 20, 0.6) == "range"
    assert classify(20, 30, 10, 30, 0.5) == "unclear"


def test_vwap_excludes_bars_without_volume() -> None:
    bars = [
        bar(day(0), 10, 12, 8, 10, 100),  # típico 10
        bar(day(1), 20, 22, 18, 20, None),  # sin volumen: no cuenta
        bar(day(2), 30, 32, 28, 30, 300),  # típico 30
    ]
    out, skipped = vwap(bars)
    assert skipped == 1
    assert out[0] == pytest.approx(10)
    assert out[1] == pytest.approx(10)  # se mantiene el último valor válido
    assert out[2] == pytest.approx((10 * 100 + 30 * 300) / 400)


def test_volume_profile_poc_and_value_area() -> None:
    bars = [bar(day(i), 100, 100.5, 99.5, 100, 10_000) for i in range(20)]
    bars += [bar(day(20 + i), 110, 111, 109, 110, 100) for i in range(5)]
    bars += [bar(day(30), 90, 91, 89, 90, 100), bar(day(31), 95, 96, 94, 95, None)]
    prof = volume_profile(bars)
    assert prof is not None
    assert 99.5 <= prof.poc <= 100.5
    # Con 48 niveles, el área de valor cubre el rango de la vela con precisión de un nivel.
    assert prof.val < 100 < prof.vah
    assert prof.vah - prof.val < 2.5
    assert prof.vah < 109  # el 70 % del volumen está alrededor de 100
    assert prof.bars_skipped == 1
    assert math.isclose(sum(v for _, _, v in prof.bins), 20 * 10_000 + 6 * 100)


def test_anchor_start_after_close_and_pre_market() -> None:
    hours = [datetime(2026, 1, d, h, 30, tzinfo=NY) for d in (28, 29) for h in range(9, 16)]
    bars = [bar(t, 1, 1, 1, 1) for t in hours]
    after_close = datetime(2026, 1, 28, 21, 5, tzinfo=UTC)  # 16:05 NY
    assert bars[anchor_start(after_close, bars) or 0].time == datetime(
        2026, 1, 29, 9, 30, tzinfo=NY
    )
    pre_market = datetime(2026, 1, 29, 11, 0, tzinfo=UTC)  # 06:00 NY
    assert bars[anchor_start(pre_market, bars) or 0].time == datetime(2026, 1, 29, 9, 30, tzinfo=NY)
    intraday = datetime(2026, 1, 28, 17, 0, tzinfo=UTC)  # 12:00 NY
    assert bars[anchor_start(intraday, bars) or 0].time == datetime(2026, 1, 28, 12, 30, tzinfo=NY)


# ---------- API ----------


def chart(
    times: list[datetime], closes: list[float], vol: float | None = 1_000_000
) -> dict[str, Any]:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                        "longName": "Trend Inc",
                    },
                    "timestamp": [int(t.timestamp()) for t in times],
                    "indicators": {
                        "quote": [
                            {
                                "open": closes,
                                "high": [c * 1.01 for c in closes],
                                "low": [c * 0.99 for c in closes],
                                "close": closes,
                                "volume": [vol] * len(closes),
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


async def test_technical_endpoint(auth_client: httpx.AsyncClient, router: Router) -> None:
    days = [datetime(2024, 11, 4, 9, 30, tzinfo=NY) + timedelta(days=i) for i in range(500)]
    trend = [100 * 1.002**i for i in range(500)]
    router.json("/chart/TRND?range=2y&interval=1d", chart(days, trend))
    hours = [d.replace(hour=h) for d in days[-400:] for h in (10, 12, 14)]
    router.json(
        "/chart/TRND?range=2y&interval=1h",
        chart(hours, [100 * 1.002 ** (i / 3) for i in range(len(hours))]),
    )
    halfs = [days[-60] + timedelta(minutes=30 * i) for i in range(400)]
    router.json(
        "/chart/TRND?range=60d&interval=30m", chart(halfs, [150 + (i % 7) for i in range(400)])
    )

    r = await auth_client.get(
        "/api/technical/trnd", params={"anchor": days[-30].date().isoformat()}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "TRND"
    assert body["regime"]["kind"] == "uptrend"
    assert body["regime"]["ema20"] > body["regime"]["ema50"] > body["regime"]["ema200"]
    avwaps = {a["key"]: a for a in body["avwaps"]}
    assert set(avwaps) == {"earnings", "high_52w", "low_52w", "ytd", "custom"}
    assert avwaps["earnings"]["status"] == "NO_DISPONIBLE"  # la SEC no responde en el test
    assert avwaps["custom"]["status"] == "OK"
    assert avwaps["custom"]["distance"] > 0  # en tendencia alcista el precio está arriba
    assert avwaps["low_52w"]["status"] == "OK"
    assert body["profile"]["poc"] > 0 and body["profile"]["val"] <= body["profile"]["vah"]
    assert len(body["candles"]) == 260 and len(body["emas"]) == 260
    assert body["volume"]["relative"] == pytest.approx(1.0)


async def test_technical_insufficient_history(
    auth_client: httpx.AsyncClient, router: Router
) -> None:
    days = [datetime(2026, 9, 1, 9, 30, tzinfo=NY) + timedelta(days=i) for i in range(60)]
    router.json(
        "/chart/NEW?range=2y&interval=1d", chart(days, [10.0 + i * 0.1 for i in range(60)], None)
    )
    r = await auth_client.get("/api/technical/NEW")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["regime"]["kind"] == "insufficient"
    assert body["volume"]["last"] is None
    assert any("volumen" in n for n in body["notes"])
    assert body["profile"] is None
