"""Columna 2: screener GARP."""

from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.container import Services
from app.services.forensic import ForensicResponse, PeStats, ScoreResult, Wacc, YearScores
from app.services.screener import evaluate, revenue_cagr
from app.sources.errors import NotFoundError


def year(end: date, revenue: float | None, margin: float | None) -> YearScores:
    empty = ScoreResult(value=None, zone=None, components={}, missing=[])
    return YearScores(
        fiscal_year_end=end,
        beneish=empty,
        altman=empty,
        quick_ratio=None,
        roic=None,
        fcf=None,
        sbc=None,
        fcf_minus_sbc=None,
        sbc_to_revenue=None,
        sbc_to_fcf=None,
        revenue=revenue,
        operating_margin=margin,
        notes=[],
    )


def forensic(
    ticker: str,
    pe: float | None,
    growth: float,
    margins: tuple[float, float],
    roic: float | None,
    wacc: float | None,
) -> ForensicResponse:
    years = [
        year(
            date(2025 - i, 12, 31),
            100 * (1 + growth) ** (5 - i),
            margins[0] if i == 0 else margins[1],
        )
        for i in range(6)
    ]
    return ForensicResponse(
        ticker=ticker,
        name=f"{ticker} Inc",
        sic=None,
        sic_description=None,
        altman_variant="Z''",
        years=years,
        pe=PeStats(
            current=pe,
            percentile=None,
            median=None,
            low=None,
            high=None,
            observations=0,
            excluded_days=0,
            series=[],
            note=None,
        ),
        wacc=Wacc(value=wacc, roic=roic, spread=None, inputs={}, assumptions=[], missing=[]),
        sources=[],
    )


def test_revenue_cagr() -> None:
    ys = [(date(2025 - i, 12, 31), 100 * 1.25 ** (5 - i)) for i in range(6)]
    assert revenue_cagr(ys) == pytest.approx(0.25)
    assert revenue_cagr(ys[:5]) is None  # faltan años
    gap = [*ys[:5], (date(2018, 12, 31), 100.0)]
    assert revenue_cagr(gap) is None  # los años no son consecutivos
    assert revenue_cagr([*ys[:5], (date(2020, 12, 31), -5.0)]) is None


def test_evaluate_all_criteria() -> None:
    row = evaluate(forensic("GOOD", 18, 0.25, (0.30, 0.28), 0.25, 0.09))
    assert row.passes and row.met == 4
    assert [c.status for c in row.criteria] == ["cumple"] * 4

    expensive = evaluate(forensic("EXP", 45, 0.25, (0.30, 0.28), 0.25, 0.09))
    assert not expensive.passes and expensive.met == 3
    assert expensive.criteria[0].status == "no cumple"

    unknown = evaluate(forensic("UNK", None, 0.25, (0.30, 0.28), None, 0.09))
    assert unknown.criteria[0].status == "sin dato"
    assert unknown.criteria[3].status == "sin dato"
    assert not unknown.passes


async def test_run_notifies_only_new_entries(
    app: FastAPI, auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services: Services = app.state.services
    await services.screener.set_universe(["GOOD", "BAD", "GONE"])
    data = {
        "GOOD": forensic("GOOD", 18, 0.25, (0.30, 0.28), 0.25, 0.09),
        "BAD": forensic("BAD", 40, 0.05, (0.10, 0.12), 0.05, 0.09),
    }

    async def fake_analyze(ticker: str, erp: float = 5.0) -> ForensicResponse:
        if ticker not in data:
            raise NotFoundError("SEC EDGAR", f"{ticker} no figura en la SEC")
        return data[ticker]

    monkeypatch.setattr(services.forensic, "analyze", fake_analyze)
    monkeypatch.setattr("app.services.screener.PAUSE_SECONDS", 0)
    rows, entered = await services.screener.run()
    assert entered == ["GOOD"]
    assert {r.ticker: r.error for r in rows}["GONE"] == "GONE no figura en la SEC"
    _, entered_again = await services.screener.run()
    assert entered_again == []  # sigue adentro: no se vuelve a avisar

    r = await auth_client.get("/api/screener")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    assert body["universe"] == ["GOOD", "BAD", "GONE"]
    assert [row["ticker"] for row in body["rows"]] == ["GOOD", "BAD", "GONE"]
    assert body["run_at"] is not None


async def test_universe_endpoint(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/screener")
    assert r.json()["rows"] == [] and "AAPL" in r.json()["universe"]
    r = await auth_client.put(
        "/api/screener/universe", json={"tickers": ["msft", " aapl ", "MSFT"]}
    )
    assert r.status_code == 200 and r.json() == ["MSFT", "AAPL"]
    assert (
        await auth_client.put("/api/screener/universe", json={"tickers": []})
    ).status_code == 422
    bad = await auth_client.put("/api/screener/universe", json={"tickers": ["../x"]})
    assert bad.status_code == 422
