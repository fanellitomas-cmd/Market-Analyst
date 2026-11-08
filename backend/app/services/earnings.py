"""Resultados trimestrales (SEC EDGAR) y reacción del precio a cada publicación.

Resultados:
  - Se toman los valores XBRL con duración de un trimestre (80 a 100 días). Si un mismo período
    aparece en varias presentaciones, gana la más reciente (incluye correcciones).
  - Muchas empresas no reportan el 4.º trimestre por separado, sólo el año. En conceptos que se
    suman (ingresos, ganancias) ese trimestre se calcula como anual − los 3 trimestres previos,
    sólo si los tres existen, y se marca como calculado. La ganancia por acción no se suma
    (cambia la cantidad de acciones), así que ese trimestre queda "no disponible".

Reacción del precio:
  - Fechas: presentaciones 8-K con el ítem 2.02 ("Results of Operations"), que es como las
    empresas publican sus resultados ante la SEC.
  - La hora de aceptación de la SEC (pasada a hora de Nueva York) define la rueda base: si se
    publicó después del cierre (16:00), la base es el cierre de ese día; si fue antes o durante la
    rueda, la base es el cierre anterior. Sin hora, no se calcula.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.core.cache import TTLCache
from app.domain import DataStatus, Metric, SourceInfo, StrictModel
from app.services import quant
from app.services.markets import MarketsService
from app.sources import sec
from app.sources.errors import NotFoundError, SourceError
from app.sources.sec import CompanyFilings, CompanyRef, Fact, Filing, SecClient, extract_facts

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
MAX_QUARTERS = 12
MAX_EVENTS = 12
HORIZONS = (1, 5, 20)
BENCHMARK = "SPY"

REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
)
GROSS_PROFIT = ("GrossProfit",)
OPERATING_INCOME = ("OperatingIncomeLoss",)
NET_INCOME = ("NetIncomeLoss",)
EPS_DILUTED = ("EarningsPerShareDiluted",)

Period = tuple[date, date]


@dataclass(frozen=True)
class QuarterValue:
    start: date
    end: date
    value: float
    derived: bool


def _days(start: date, end: date) -> int:
    return (end - start).days


def latest_by_period(candidates: list[list[Fact]]) -> dict[Period, Fact]:
    """Un valor por período: el primer concepto que lo tenga y, dentro de él, el más reciente."""
    chosen: dict[Period, Fact] = {}
    for facts in candidates:
        best: dict[Period, Fact] = {}
        for f in facts:
            if f.start is None:
                continue
            key = (f.start, f.end)
            if key not in best or f.filed > best[key].filed:
                best[key] = f
        for key, f in best.items():
            chosen.setdefault(key, f)
    return chosen


def quarterly_series(candidates: list[list[Fact]], additive: bool) -> list[QuarterValue]:
    by_period = latest_by_period(candidates)
    quarters = {(s, e): f.value for (s, e), f in by_period.items() if 80 <= _days(s, e) <= 100}
    out = [QuarterValue(s, e, v, derived=False) for (s, e), v in quarters.items()]

    if additive:
        quarter_ends = {e for _, e in quarters}
        for (a_start, a_end), fact in by_period.items():
            if not 350 <= _days(a_start, a_end) <= 380 or a_end in quarter_ends:
                continue
            inside = sorted(
                (s, e, v)
                for (s, e), v in quarters.items()
                if s >= a_start - timedelta(days=5) and e < a_end
            )
            if len(inside) != 3:
                continue
            # Los tres trimestres tienen que ser consecutivos (sin huecos ni superposición).
            contiguous = all(abs(_days(inside[i][1], inside[i + 1][0])) <= 5 for i in range(2))
            q4_start = inside[-1][1] + timedelta(days=1)
            if not contiguous or not 80 <= _days(q4_start, a_end) <= 100:
                continue
            q4 = fact.value - math.fsum(v for _, _, v in inside)
            out.append(QuarterValue(q4_start, a_end, q4, derived=True))

    out.sort(key=lambda q: q.end)
    return out


# ---------- Modelos ----------


class CompanyInfo(StrictModel):
    ticker: str
    name: str
    cik: int


class QuarterRow(StrictModel):
    period_start: date
    period_end: date
    revenue: Metric
    revenue_yoy: Metric  # fracción
    gross_margin: Metric
    operating_margin: Metric
    net_margin: Metric
    net_income: Metric
    eps_diluted: Metric
    eps_yoy: Metric
    derived: bool  # el trimestre se calculó como anual − 3 trimestres


Session = Literal["antes de la apertura", "durante la rueda", "después del cierre", "desconocido"]


class Reaction(StrictModel):
    horizon: int  # ruedas
    stock: float | None
    benchmark: float | None
    excess: float | None  # stock − benchmark
    status: DataStatus
    note: str | None = None


class EarningsEvent(StrictModel):
    filing_date: date
    accepted_at_ny: datetime | None
    session: Session
    base_date: date | None
    reactions: list[Reaction]
    filing_url: str


class EarningsResponse(StrictModel):
    company: CompanyInfo
    quarters: list[QuarterRow]  # el más reciente primero
    events: list[EarningsEvent]  # el más reciente primero
    avg_abs_move_1d: Metric  # movimiento absoluto promedio de la acción a 1 rueda
    sources: list[SourceInfo]


# ---------- Servicio ----------


class EarningsService:
    def __init__(
        self,
        client: SecClient,
        markets: MarketsService,
        tickers_cache: TTLCache[dict[str, CompanyRef]],
        facts_cache: TTLCache[dict[str, Any]],
        filings_cache: TTLCache[CompanyFilings],
    ) -> None:
        self._sec = client
        self._markets = markets
        self._tickers_cache = tickers_cache
        self._facts_cache = facts_cache
        self._filings_cache = filings_cache

    @property
    def configured(self) -> bool:
        return self._sec.configured

    async def resolve(self, ticker: str) -> CompanyRef:
        tickers = await self._tickers_cache.get_or_load("tickers", self._sec.ticker_map)
        # Yahoo usa "BRK-B"; la SEC, "BRK-B" o "BRK.B" según el caso.
        ref = (
            tickers.get(ticker)
            or tickers.get(ticker.replace(".", "-"))
            or tickers.get(ticker.replace("-", "."))
        )
        if ref is None:
            raise NotFoundError(
                sec.SOURCE,
                f"{ticker} no figura en la SEC (sólo empresas que presentan balances en EE.UU.)",
            )
        return ref

    async def company_filings(self, ref: CompanyRef) -> CompanyFilings:
        return await self._filings_cache.get_or_load(
            str(ref.cik), lambda: self._sec.submissions(ref.cik)
        )

    async def company_facts(self, ref: CompanyRef) -> dict[str, Any]:
        return await self._facts_cache.get_or_load(
            str(ref.cik), lambda: self._sec.company_facts(ref.cik)
        )

    async def latest_release(self, ticker: str) -> datetime | None:
        """Hora (UTC) en que la SEC aceptó la última publicación de resultados (8-K ítem 2.02)."""
        ref = await self.resolve(ticker)
        filings = (await self.company_filings(ref)).filings
        times = [
            f.accepted_at
            for f in filings
            if f.form in ("8-K", "8-K/A") and "2.02" in f.items and f.accepted_at is not None
        ]
        return max(times) if times else None

    async def analyze(self, ticker: str) -> EarningsResponse:
        ref = await self.resolve(ticker)
        facts, filings = await asyncio.gather(
            self._facts_cache.get_or_load(str(ref.cik), lambda: self._sec.company_facts(ref.cik)),
            self.company_filings(ref),
        )
        quarters = build_quarters(facts)
        events = await self._events(ref, filings.filings)

        moves = [
            abs(r.stock)
            for e in events
            for r in e.reactions
            if r.horizon == 1 and r.stock is not None
        ]
        avg = (
            Metric.ok(math.fsum(moves) / len(moves))
            if moves
            else Metric.missing("no hay reacciones medibles")
        )
        now = datetime.now(UTC)
        return EarningsResponse(
            company=CompanyInfo(ticker=ref.ticker, name=ref.name, cik=ref.cik),
            quarters=quarters,
            events=events,
            avg_abs_move_1d=avg,
            sources=[
                SourceInfo(name=sec.SOURCE, url=self._sec.company_url(ref.cik), fetched_at=now),
                SourceInfo(
                    name="Yahoo Finance",
                    url=f"https://finance.yahoo.com/quote/{ticker}",
                    fetched_at=now,
                ),
            ],
        )

    async def _events(self, ref: CompanyRef, filings: list[Filing]) -> list[EarningsEvent]:
        releases = sorted(
            (f for f in filings if f.form in ("8-K", "8-K/A") and "2.02" in f.items),
            key=lambda f: f.filing_date,
            reverse=True,
        )
        # Una enmienda (8-K/A) del mismo día no es una publicación nueva.
        seen: set[date] = set()
        unique: list[Filing] = []
        for f in releases:
            if f.filing_date not in seen:
                seen.add(f.filing_date)
                unique.append(f)
        unique = unique[:MAX_EVENTS]
        if not unique:
            return []

        try:
            asset, bench = await asyncio.gather(
                self._markets.get_prices(ref.ticker, "5y"),
                self._markets.get_prices(BENCHMARK, "5y"),
            )
            aligned: quant.AlignedPrices | None = quant.align(
                asset.dates, asset.closes, bench.dates, bench.closes
            )
        except SourceError as exc:
            logger.warning("earnings: sin precios para %s: %s", ref.ticker, exc)
            aligned = None

        return [self._event(ref, f, aligned) for f in unique]

    def _event(
        self, ref: CompanyRef, filing: Filing, aligned: quant.AlignedPrices | None
    ) -> EarningsEvent:
        url = self._sec.filing_index_url(ref.cik, filing.accession)
        accepted_ny = filing.accepted_at.astimezone(NY) if filing.accepted_at else None
        session = classify_session(accepted_ny)

        def missing(note: str) -> list[Reaction]:
            return [
                Reaction(
                    horizon=h,
                    stock=None,
                    benchmark=None,
                    excess=None,
                    status="NO_DISPONIBLE",
                    note=note,
                )
                for h in HORIZONS
            ]

        if accepted_ny is None or session == "desconocido":
            return EarningsEvent(
                filing_date=filing.filing_date,
                accepted_at_ny=None,
                session="desconocido",
                base_date=None,
                reactions=missing("sin hora de publicación"),
                filing_url=url,
            )
        if aligned is None or not aligned.dates:
            return EarningsEvent(
                filing_date=filing.filing_date,
                accepted_at_ny=accepted_ny,
                session=session,
                base_date=None,
                reactions=missing("sin precios"),
                filing_url=url,
            )

        event_day = accepted_ny.date()
        base_idx = base_index(aligned.dates, event_day, after_close=session == "después del cierre")
        if base_idx is None:
            return EarningsEvent(
                filing_date=filing.filing_date,
                accepted_at_ny=accepted_ny,
                session=session,
                base_date=None,
                reactions=missing("fuera del historial de precios"),
                filing_url=url,
            )

        reactions: list[Reaction] = []
        for h in HORIZONS:
            idx = base_idx + h
            if idx >= len(aligned.dates):
                reactions.append(
                    Reaction(
                        horizon=h,
                        stock=None,
                        benchmark=None,
                        excess=None,
                        status="NO_DISPONIBLE",
                        note=f"todavía no pasaron {h} ruedas",
                    )
                )
                continue
            s = aligned.asset[idx] / aligned.asset[base_idx] - 1.0
            b = aligned.benchmark[idx] / aligned.benchmark[base_idx] - 1.0
            reactions.append(Reaction(horizon=h, stock=s, benchmark=b, excess=s - b, status="OK"))
        return EarningsEvent(
            filing_date=filing.filing_date,
            accepted_at_ny=accepted_ny,
            session=session,
            base_date=aligned.dates[base_idx],
            reactions=reactions,
            filing_url=url,
        )


def classify_session(accepted_ny: datetime | None) -> Session:
    if accepted_ny is None:
        return "desconocido"
    t = accepted_ny.time()
    if t >= time(16, 0):
        return "después del cierre"
    if t < time(9, 30):
        return "antes de la apertura"
    return "durante la rueda"


def base_index(dates: list[date], event_day: date, after_close: bool) -> int | None:
    """Índice de la rueda base: el cierre del día del evento (si fue después del cierre) o el
    último cierre anterior. Si el precio más cercano está a más de 5 días, no hay base."""
    idx: int | None = None
    for i, d in enumerate(dates):
        if d < event_day or (after_close and d == event_day):
            idx = i
        else:
            break
    if idx is None or (event_day - dates[idx]).days > 5:
        return None
    return idx


def _year_ago(table: dict[date, QuarterValue], end: date) -> QuarterValue | None:
    """El trimestre que cerró aproximadamente un año antes (350 a 380 días)."""
    for e, q in table.items():
        if 350 <= (end - e).days <= 380:
            return q
    return None


def _value_metric(q: QuarterValue | None) -> Metric:
    if q is None:
        return Metric.missing("no reportado")
    return Metric(
        value=q.value, status="OK", note="calculado: anual − 3 trimestres" if q.derived else None
    )


def _margin(q: QuarterValue | None, rev: QuarterValue) -> Metric:
    if q is None:
        return Metric.missing("no reportado")
    if rev.value == 0:
        return Metric.missing("ingresos en cero")
    return Metric(
        value=q.value / rev.value,
        status="OK",
        note="calculado" if q.derived or rev.derived else None,
    )


def _growth(cur: QuarterValue | None, prev: QuarterValue | None) -> Metric:
    if cur is None or prev is None:
        return Metric.missing("falta el trimestre comparable")
    if prev.value <= 0:
        return Metric.missing("base negativa o cero: el % no tiene sentido")
    return Metric.ok(cur.value / prev.value - 1.0)


def build_quarters(facts: dict[str, Any]) -> list[QuarterRow]:
    def series(concepts: tuple[str, ...], unit: str, additive: bool) -> dict[date, QuarterValue]:
        candidates = [extract_facts(facts, c, unit) for c in concepts]
        return {q.end: q for q in quarterly_series(candidates, additive)}

    revenue = series(REVENUE, "USD", True)
    gross = series(GROSS_PROFIT, "USD", True)
    operating = series(OPERATING_INCOME, "USD", True)
    net = series(NET_INCOME, "USD", True)
    eps = series(EPS_DILUTED, "USD/shares", False)

    rows: list[QuarterRow] = []
    for end in sorted(revenue, reverse=True)[:MAX_QUARTERS]:
        rev = revenue[end]
        eps_q = eps.get(end)
        rows.append(
            QuarterRow(
                period_start=rev.start,
                period_end=end,
                revenue=_value_metric(rev),
                revenue_yoy=_growth(rev, _year_ago(revenue, end)),
                gross_margin=_margin(gross.get(end), rev),
                operating_margin=_margin(operating.get(end), rev),
                net_margin=_margin(net.get(end), rev),
                net_income=_value_metric(net.get(end)),
                eps_diluted=(
                    _value_metric(eps_q)
                    if eps_q
                    else Metric.missing("la empresa no reportó el trimestre por separado")
                ),
                eps_yoy=_growth(eps_q, _year_ago(eps, end)),
                derived=rev.derived,
            )
        )
    return rows
