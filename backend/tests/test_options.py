"""Paso 6: opciones (GEX, movimiento implícito, interés abierto)."""

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.container import Services
from app.core.db import OptionSnapshot
from app.services.options import (
    analyze_chain,
    bs_gamma,
    implied_moves,
    oi_reading,
    regime_flip_message,
    zero_gamma,
)
from app.sources.cboe import OptionChain, OptionQuote, cboe_symbol, parse_occ
from tests.conftest import Router

NOW = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # viernes 10:00 en Nueva York
EXP = date(2026, 12, 18)


def opt(
    kind: str,
    strike: float,
    oi: float,
    iv: float | None = 0.2,
    bid: float | None = None,
    ask: float | None = None,
    expiry: date = EXP,
) -> OptionQuote:
    return OptionQuote(
        expiry=expiry,
        kind=kind,
        strike=strike,
        bid=bid,
        ask=ask,
        iv=iv,
        open_interest=oi,
        volume=0,
        delta=None,
        gamma=None,
    )


def test_parse_occ_and_symbols() -> None:
    assert parse_occ("SPY261218C00600000") == (date(2026, 12, 18), "C", 600.0)
    assert parse_occ("SPXW261106P05987500") == (date(2026, 11, 6), "P", 5987.5)
    assert parse_occ("basura") is None
    assert cboe_symbol("^SPX") == "_SPX" and cboe_symbol("spx") == "_SPX"
    assert cboe_symbol("AAPL") == "AAPL"


def test_black_scholes_gamma_by_hand() -> None:
    # S = K = 100, T = 1, σ = 0,2 → d1 = 0,1; φ(0,1) = 0,396953; gamma = φ / (S σ √T)
    assert bs_gamma(100, 100, 1, 0.2) == pytest.approx(0.396953 / 20, rel=1e-5)
    assert bs_gamma(100, 100, 0, 0.2) == 0.0


def test_gex_signs_walls_and_flip() -> None:
    calls_only = analyze_chain(OptionChain("X", 100, None, [opt("C", 100, 1000)]), NOW)
    assert calls_only.total > 0
    puts_only = analyze_chain(OptionChain("X", 100, None, [opt("P", 100, 1000)]), NOW)
    assert puts_only.total < 0
    assert calls_only.total == pytest.approx(-puts_only.total)

    # Puts concentrados abajo, calls arriba: el GEX cambia de signo entre los dos.
    mixed = [opt("P", 92, 5000), opt("C", 108, 5000), opt("C", 104, 100, iv=None)]
    a = analyze_chain(OptionChain("X", 100, None, mixed), NOW)
    assert a.call_wall == 108 and a.put_wall == 92
    assert a.excluded == 1  # el contrato sin volatilidad implícita
    flip = zero_gamma(mixed, 100, NOW)
    assert flip is not None and 92 < flip < 108


def test_expired_contracts_are_ignored() -> None:
    old = opt("C", 100, 1000, expiry=date(2026, 11, 5))
    a = analyze_chain(OptionChain("X", 100, None, [old, opt("P", 100, 10)]), NOW)
    assert a.total < 0  # sólo cuenta el put vigente


def test_implied_move_uses_atm_straddle_mid() -> None:
    chain = OptionChain(
        "X",
        101,
        None,
        [
            opt("C", 100, 1, bid=5.8, ask=6.2),
            opt("P", 100, 1, bid=4.8, ask=5.2),
            opt("C", 105, 1, bid=3, ask=3.2),  # sin put: no sirve
            opt("C", 100, 1, bid=0, ask=1, expiry=date(2026, 11, 13)),  # sin bid: se descarta
            opt("P", 100, 1, bid=1, ask=1.2, expiry=date(2026, 11, 13)),
        ],
    )
    moves = implied_moves(chain, NOW)
    assert [m.expiry for m in moves] == [EXP]
    assert moves[0].straddle == pytest.approx(11.0)
    assert moves[0].move == pytest.approx(11 / 101)


def snap(day: int, spot: float, call_oi: float, put_oi: float, gex: float) -> OptionSnapshot:
    return OptionSnapshot(
        symbol="X",
        date=date(2026, 11, day),
        spot=spot,
        call_oi=call_oi,
        put_oi=put_oi,
        total_gex=gex,
        zero_gamma=99.0,
    )


def test_oi_reading_quadrants_and_flip() -> None:
    base = snap(5, 100, 1000, 1000, 5e6)
    assert oi_reading(base, snap(6, 102, 1200, 1000, 5e6)).reading.startswith(
        "suba con posiciones nuevas"
    )
    assert "gamma squeeze" in oi_reading(base, snap(6, 102, 1200, 1000, 5e6)).reading
    assert oi_reading(base, snap(6, 102, 900, 900, 5e6)).reading.startswith("suba con cierre")
    assert oi_reading(base, snap(6, 98, 1100, 1100, 5e6)).reading.startswith(
        "baja con posiciones nuevas"
    )
    assert oi_reading(base, snap(6, 98, 900, 900, 5e6)).reading.startswith("baja con cierre")
    assert regime_flip_message("X", snap(6, 98, 1, 1, -1e6), base) is not None
    assert regime_flip_message("X", snap(6, 98, 1, 1, 1e6), base) is None
    assert regime_flip_message("X", snap(6, 98, 1, 1, -1e6), None) is None


def cboe_payload(spot: float, rows: list[tuple[str, float, float]]) -> dict[str, Any]:
    return {
        "timestamp": "2026-11-06 14:45:00",
        "data": {
            "current_price": spot,
            "options": [
                {
                    "option": code,
                    "open_interest": oi,
                    "iv": iv,
                    "bid": 1.0,
                    "ask": 1.2,
                    "volume": 10,
                }
                for code, oi, iv in rows
            ],
        },
    }


async def test_options_endpoint_and_snapshots(
    app: FastAPI, auth_client: httpx.AsyncClient, router: Router
) -> None:
    services: Services = app.state.services
    router.json(
        "/options/SPY.json",
        cboe_payload(
            600,
            [
                ("SPY261218C00620000", 8000, 0.15),
                ("SPY261218P00580000", 2000, 0.2),
                ("SPY261218C00600000", 100, 0.16),
                ("SPY261218P00600000", 100, 0.16),
            ],
        ),
    )
    first, prev = await services.options.snapshot(
        "SPY", now=datetime(2026, 11, 5, 23, 0, tzinfo=UTC)
    )
    assert prev is None and first.total_gex > 0

    router.json(
        "/options/SPY.json",
        cboe_payload(
            590,
            [
                ("SPY261218C00620000", 1000, 0.15),
                ("SPY261218P00580000", 9500, 0.2),
            ],
        ),
    )
    second, prev = await services.options.snapshot(
        "SPY", now=datetime(2026, 11, 6, 23, 0, tzinfo=UTC)
    )
    assert prev is not None and prev.date == date(2026, 11, 5)
    assert second.total_gex < 0
    msg = regime_flip_message("SPY", second, prev)
    assert msg is not None and "gamma negativa" in msg

    r = await auth_client.get("/api/options/spy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "SPY" and body["spot"] == 590
    assert body["regime"].startswith("gamma negativa")
    assert [h["date"] for h in body["history"]] == ["2026-11-05", "2026-11-06"]
    assert body["oi_reading"]["reading"].startswith("baja con posiciones nuevas")
    assert body["put_call_oi"] == pytest.approx(9.5)

    router.add("/options/ZZZZ.json", lambda _r: httpx.Response(403))
    assert (await auth_client.get("/api/options/ZZZZ")).status_code == 404


async def test_watchlist(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/options/watchlist")
    assert "SPY" in r.json()
    r = await auth_client.put("/api/options/watchlist", json={"symbols": ["qqq", "^spx"]})
    assert r.json() == ["QQQ", "^SPX"]
    assert (
        await auth_client.put("/api/options/watchlist", json={"symbols": []})
    ).status_code == 422
