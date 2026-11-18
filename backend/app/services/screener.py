"""Screener GARP (crecimiento a precio razonable), Columna 2.

Criterios de la visión, cada uno con resultado explícito (cumple / no cumple / sin dato):
  1. P/E < 20 (capitalización / ganancia de los últimos 12 meses).
  2. Crecimiento anual compuesto de ingresos a 5 años > 20 %.
  3. Expansión del margen operativo: margen del último año fiscal mayor al del anterior.
  4. ROIC > WACC.

El universo es una lista editable guardada en la base. La tarea nocturna del agente analiza cada
empresa con el mismo motor forense de la página Empresas y avisa sólo cuando una empresa entra al
filtro (no la repite mientras siga adentro).
"""

import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import ScreenerResult, SystemState, utcnow
from app.domain import StrictModel
from app.services.forensic import ForensicResponse, ForensicService
from app.sources.errors import SourceError

logger = logging.getLogger(__name__)

UNIVERSE_KEY = "screener_universe"
DEFAULT_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "AVGO",
    "TSLA",
    "NFLX",
    "ADBE",
    "CRM",
    "ORCL",
    "AMD",
    "QCOM",
    "INTU",
    "NOW",
    "PANW",
    "ANET",
    "CRWD",
    "UBER",
    "MELI",
    "SHOP",
    "ABNB",
    "BKNG",
    "LLY",
    "NVO",
    "ISRG",
    "VRTX",
    "REGN",
    "DHR",
    "UNH",
    "COST",
    "WMT",
    "HD",
    "NKE",
    "SBUX",
    "MCD",
    "KO",
    "PEP",
    "PG",
    "JNJ",
    "XOM",
    "CVX",
    "CAT",
    "DE",
    "GE",
    "HON",
    "V",
    "MA",
    "JPM",
]
MAX_PE = 20.0
MIN_CAGR = 0.20
PAUSE_SECONDS = 0.3  # la SEC pide no superar 10 pedidos por segundo

Check = Literal["cumple", "no cumple", "sin dato"]


class CriterionOut(StrictModel):
    name: str
    status: Check
    detail: str


class ScreenerRow(StrictModel):
    ticker: str
    name: str | None
    passes: bool
    met: int  # cuántos criterios cumple
    pe: float | None
    revenue_cagr_5y: float | None
    operating_margin: float | None
    operating_margin_prev: float | None
    roic: float | None
    wacc: float | None
    criteria: list[CriterionOut]
    error: str | None


class ScreenerResponse(StrictModel):
    universe: list[str]
    run_at: datetime | None
    rows: list[ScreenerRow]  # primero los que cumplen todo, después por criterios cumplidos


def revenue_cagr(years: Sequence[tuple[date, float | None]]) -> float | None:
    """CAGR entre el último año fiscal y el de 5 años antes (con ingresos positivos)."""
    if len(years) < 6:
        return None
    (end_now, rev_now), (end_then, rev_then) = years[0], years[5]
    if rev_now is None or rev_then is None or rev_now <= 0 or rev_then <= 0:
        return None
    if not 1795 <= (end_now - end_then).days <= 1860:  # deben ser años fiscales consecutivos
        return None
    return float((rev_now / rev_then) ** (1 / 5) - 1)


def evaluate(f: ForensicResponse) -> ScreenerRow:
    years = f.years
    pe = f.pe.current
    cagr = revenue_cagr([(y.fiscal_year_end, y.revenue) for y in years])
    om = years[0].operating_margin if years else None
    om_prev = years[1].operating_margin if len(years) > 1 else None
    roic, wacc = f.wacc.roic, f.wacc.value

    def crit(name: str, ok: bool | None, detail: str) -> CriterionOut:
        status: Check = "sin dato" if ok is None else "cumple" if ok else "no cumple"
        return CriterionOut(name=name, status=status, detail=detail)

    criteria = [
        crit(
            "P/E < 20",
            None if pe is None else pe < MAX_PE,
            "sin ganancia positiva o sin precio" if pe is None else f"P/E {pe:.1f}",
        ),
        crit(
            "Ingresos +20 % anual (5 años)",
            None if cagr is None else cagr > MIN_CAGR,
            "faltan 6 años fiscales de ingresos" if cagr is None else f"{cagr * 100:.1f} % anual",
        ),
        crit(
            "Margen operativo en expansión",
            None if om is None or om_prev is None else om > om_prev,
            "faltan dos años de margen"
            if om is None or om_prev is None
            else f"{om_prev * 100:.1f} % → {om * 100:.1f} %",
        ),
        crit(
            "ROIC > WACC",
            None if roic is None or wacc is None else roic > wacc,
            "; ".join(f.wacc.missing) or "falta ROIC"
            if roic is None or wacc is None
            else f"{roic * 100:.1f} % vs {wacc * 100:.1f} %",
        ),
    ]
    met = sum(1 for c in criteria if c.status == "cumple")
    return ScreenerRow(
        ticker=f.ticker,
        name=f.name,
        passes=met == len(criteria),
        met=met,
        pe=pe,
        revenue_cagr_5y=cagr,
        operating_margin=om,
        operating_margin_prev=om_prev,
        roic=roic,
        wacc=wacc,
        criteria=criteria,
        error=None,
    )


class ScreenerService:
    def __init__(
        self, forensic: ForensicService, sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        self._forensic = forensic
        self._sessions = sessions

    async def universe(self) -> list[str]:
        async with self._sessions() as session:
            row = await session.get(SystemState, UNIVERSE_KEY)
        if row is None:
            return list(DEFAULT_UNIVERSE)
        loaded = json.loads(row.value)
        return [str(t) for t in loaded] if isinstance(loaded, list) else list(DEFAULT_UNIVERSE)

    async def set_universe(self, tickers: list[str]) -> list[str]:
        clean = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
        async with self._sessions() as session:
            row = await session.get(SystemState, UNIVERSE_KEY)
            if row is None:
                session.add(SystemState(key=UNIVERSE_KEY, value=json.dumps(clean)))
            else:
                row.value = json.dumps(clean)
                row.updated_at = utcnow()
            await session.commit()
        return clean

    async def run(self) -> tuple[list[ScreenerRow], list[str]]:
        """Analiza todo el universo. Devuelve los resultados y los tickers que ENTRARON al filtro
        respecto de la corrida anterior."""
        previous = {r.ticker for r in (await self.latest()).rows if r.passes}
        run_at = utcnow()
        rows: list[ScreenerRow] = []
        for ticker in await self.universe():
            try:
                rows.append(evaluate(await self._forensic.analyze(ticker)))
            except SourceError as exc:
                rows.append(
                    ScreenerRow(
                        ticker=ticker,
                        name=None,
                        passes=False,
                        met=0,
                        pe=None,
                        revenue_cagr_5y=None,
                        operating_margin=None,
                        operating_margin_prev=None,
                        roic=None,
                        wacc=None,
                        criteria=[],
                        error=exc.message,
                    )
                )
            await asyncio.sleep(PAUSE_SECONDS)
        async with self._sessions() as session:
            for r in rows:
                session.add(
                    ScreenerResult(
                        run_at=run_at, ticker=r.ticker, passes=r.passes, payload=r.model_dump_json()
                    )
                )
            await session.commit()
        entered = [r.ticker for r in rows if r.passes and r.ticker not in previous]
        return rows, entered

    async def latest(self) -> ScreenerResponse:
        universe = await self.universe()
        async with self._sessions() as session:
            run_at = (await session.execute(select(func.max(ScreenerResult.run_at)))).scalar()
            rows: list[ScreenerRow] = []
            if run_at is not None:
                stored = (
                    (
                        await session.execute(
                            select(ScreenerResult).where(ScreenerResult.run_at == run_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                rows = [ScreenerRow.model_validate_json(s.payload) for s in stored]
        rows.sort(key=lambda r: (not r.passes, -r.met, r.error is not None, r.ticker))
        return ScreenerResponse(
            universe=universe,
            run_at=run_at if run_at is None or run_at.tzinfo else run_at.replace(tzinfo=UTC),
            rows=rows,
        )
