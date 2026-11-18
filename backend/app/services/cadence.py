"""Cadencia operativa: pre-market, alertas de asimetría intradía y cierre de rueda.

Lista de seguimiento = tickers con tesis activas + lista de opciones.

- Pre-market (07:30 Buenos Aires): Asia y Europa, futuros de EE.UU., dólar, petróleo, oro y
  cripto (con su financiamiento), datos económicos del día, resultados publicados desde el cierre
  (8-K ítem 2.02) y la matriz de niveles de cada activo seguido (AVWAP, perfil de volumen, zero
  gamma y movimiento implícito).
- Alertas de asimetría (cada 15 minutos con NY abierta), sólo si pasa algo:
    · volumen acumulado del día > 2,5σ por encima de lo normal a esa misma hora (últimas
      sesiones) Y el precio rompe el máximo o mínimo de ayer o de 52 semanas;
    · o la empresa presenta un 8-K con un ítem relevante.
  Formato: Ticker / Hecho / Causa / Stop técnico. Cada alerta se manda una sola vez por día.
- Cierre: mapa de calor sectorial (rendimiento y fuerza relativa contra SPY), posición frente a
  las EMAs de 20/50/200 y estado de las tesis (que se reevalúan antes).

Las "sorpresas en balances" frente al consenso no se incluyen: no hay una fuente gratuita y
confiable de estimados de analistas.
"""

import asyncio
import logging
import math
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import Report, TriggerLog, utcnow
from app.core.market_calendar import is_trading_day
from app.domain import StrictModel
from app.services.earnings import EarningsService
from app.services.econ_calendar import EconCalendarService, EventOut
from app.services.markets import MarketsService
from app.services.options import OptionsService
from app.services.sentinel import SentinelService
from app.services.technical import TechnicalService, ema
from app.services.thesis import ThesisOut, ThesisService
from app.sources.errors import SourceError
from app.sources.sec import CurrentFiling, SecClient
from app.sources.yahoo import Bar, YahooClient

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
AR = ZoneInfo("America/Argentina/Buenos_Aires")
VOLUME_SIGMA = 2.5
MAX_WATCH = 15

OVERNIGHT: list[tuple[str, str, str]] = [
    ("^N225", "Nikkei 225", "Asia"),
    ("^HSI", "Hang Seng", "Asia"),
    ("000001.SS", "Shanghai", "Asia"),
    ("^STOXX50E", "Euro Stoxx 50", "Europa"),
    ("^GDAXI", "DAX", "Europa"),
    ("^FTSE", "FTSE 100", "Europa"),
    ("ES=F", "Futuros S&P 500", "Futuros EE.UU."),
    ("NQ=F", "Futuros Nasdaq 100", "Futuros EE.UU."),
    ("RTY=F", "Futuros Russell 2000", "Futuros EE.UU."),
    ("DX-Y.NYB", "DXY", "Dólar y materias primas"),
    ("CL=F", "Petróleo WTI", "Dólar y materias primas"),
    ("GC=F", "Oro", "Dólar y materias primas"),
    ("BTC-USD", "Bitcoin", "Cripto"),
    ("ETH-USD", "Ether", "Cripto"),
]
SECTORS: list[tuple[str, str]] = [
    ("XLK", "Tecnología"),
    ("XLC", "Comunicaciones"),
    ("XLY", "Consumo discrecional"),
    ("XLF", "Financiero"),
    ("XLI", "Industria"),
    ("XLB", "Materiales"),
    ("XLE", "Energía"),
    ("XLV", "Salud"),
    ("XLP", "Consumo básico"),
    ("XLU", "Servicios públicos"),
    ("XLRE", "Inmobiliario"),
]
BENCHMARKS: list[tuple[str, str]] = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("IWM", "Russell 2000"),
]
ITEMS: dict[str, str] = {
    "1.01": "acuerdo material",
    "1.02": "fin de un acuerdo material",
    "1.03": "quiebra o concurso",
    "2.01": "compra o venta de activos",
    "2.02": "resultados",
    "2.03": "nueva deuda",
    "2.04": "aceleración de deuda",
    "2.05": "reestructuración o despidos",
    "2.06": "deterioro de activos",
    "3.01": "aviso de deslistado",
    "4.01": "cambio de auditor",
    "4.02": "estados contables no confiables (reexpresión)",
    "5.01": "cambio de control",
    "5.02": "cambios de directores o ejecutivos",
    "8.01": "otros hechos relevantes",
}


# ---------- Modelos ----------


class QuoteRow(StrictModel):
    symbol: str
    name: str
    group: str
    price: float | None
    change: float | None  # contra el cierre anterior
    time: datetime | None
    error: str | None


class LevelRow(StrictModel):
    ticker: str
    close: float | None
    regime: str | None
    avwap_earnings: float | None
    avwap_high: float | None
    avwap_low: float | None
    poc: float | None
    vah: float | None
    val: float | None
    zero_gamma: float | None
    implied_move: float | None  # próximo vencimiento
    implied_expiry: date | None
    errors: list[str]


class FilingRow(StrictModel):
    ticker: str
    company: str
    items: list[str]
    time: datetime | None
    link: str | None


class PremarketReport(StrictModel):
    generated_at: datetime
    markets: list[QuoteRow]
    crypto_funding: list[dict[str, str | float | None]]
    events_today: list[EventOut]
    earnings_overnight: list[FilingRow]
    earnings_note: str
    levels: list[LevelRow]
    watchlist: list[str]


class TriggerOut(StrictModel):
    created_at: datetime
    ticker: str
    kind: str
    fact: str
    cause: str
    stop: str


class SectorRow(StrictModel):
    symbol: str
    name: str
    change_1d: float | None
    change_1w: float | None
    change_1m: float | None
    vs_spy_1d: float | None
    vs_spy_1m: float | None
    above_ema20: bool | None
    above_ema50: bool | None
    above_ema200: bool | None


class LedgerReport(StrictModel):
    generated_at: datetime
    session_date: date | None
    benchmarks: list[SectorRow]
    sectors: list[SectorRow]
    rotation: str | None
    theses: list[ThesisOut]
    invalidated_today: list[str]


# ---------- Cálculos puros ----------


def session_open(now: datetime) -> bool:
    ny = now.astimezone(NY)
    return is_trading_day(ny.date()) and time(9, 30) <= ny.time() <= time(16, 0)


def volume_zscore(bars: list[Bar], now_ny: datetime) -> tuple[float | None, float, int]:
    """Volumen acumulado de hoy contra el acumulado de días anteriores hasta la misma hora.

    Devuelve (z, volumen de hoy, días usados). z = None con menos de 5 días comparables.
    """
    by_day: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        by_day[b.time.astimezone(NY).date()].append(b)
    today = now_ny.date()
    cutoff = now_ny.time()

    def cum(day_bars: list[Bar]) -> float | None:
        vols = [b.volume for b in day_bars if b.time.astimezone(NY).time() <= cutoff]
        if not vols or any(v is None for v in vols):
            return None
        return math.fsum(v for v in vols if v is not None)

    today_vol = cum(by_day.get(today, []))
    history = [v for d, bs in by_day.items() if d < today and (v := cum(bs)) is not None]
    history = history[-20:]
    if today_vol is None or len(history) < 5:
        return None, today_vol or 0.0, len(history)
    sd = statistics.stdev(history)
    if sd == 0:
        return None, today_vol, len(history)
    return (today_vol - statistics.fmean(history)) / sd, today_vol, len(history)


def day_range(bars: list[Bar], day: date) -> tuple[float, float] | None:
    same = [b for b in bars if b.time.astimezone(NY).date() == day]
    if not same:
        return None
    return max(b.high for b in same), min(b.low for b in same)


def _change(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or closes[-1 - days] == 0:
        return None
    return closes[-1] / closes[-1 - days] - 1.0


# ---------- Servicio ----------


class CadenceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        yahoo: YahooClient,
        markets: MarketsService,
        technical: TechnicalService,
        options: OptionsService,
        sentinel: SentinelService,
        calendar: EconCalendarService,
        earnings: EarningsService,
        sec: SecClient,
        theses: ThesisService,
    ) -> None:
        self._sessions = sessions
        self._yahoo = yahoo
        self._markets = markets
        self._technical = technical
        self._options = options
        self._sentinel = sentinel
        self._calendar = calendar
        self._earnings = earnings
        self._sec = sec
        self._theses = theses

    async def watchlist(self) -> list[str]:
        tickers = await self._theses.active_tickers() + await self._options.watchlist()
        return list(dict.fromkeys(tickers))[:MAX_WATCH]

    async def _save(self, kind: str, payload: StrictModel) -> None:
        async with self._sessions() as session:
            session.add(Report(kind=kind, payload=payload.model_dump_json()))
            await session.commit()

    async def _latest(self, kind: str) -> str | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(Report).where(Report.kind == kind).order_by(Report.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
        return row.payload if row else None

    async def latest_premarket(self) -> PremarketReport | None:
        raw = await self._latest("premarket")
        return PremarketReport.model_validate_json(raw) if raw else None

    async def latest_ledger(self) -> LedgerReport | None:
        raw = await self._latest("ledger")
        return LedgerReport.model_validate_json(raw) if raw else None

    # --- 8-K de la lista ---

    async def _watch_ciks(self, tickers: list[str]) -> dict[int, str]:
        out: dict[int, str] = {}
        for t in tickers:
            try:
                out[(await self._earnings.resolve(t)).cik] = t
            except SourceError:
                continue  # ETFs e índices no presentan 8-K
        return out

    async def _filings(self, tickers: list[str]) -> list[tuple[str, CurrentFiling]]:
        if not self._sec.configured:
            return []
        ciks = await self._watch_ciks(tickers)
        if not ciks:
            return []
        feed = await self._sec.current_8k()
        return [(ciks[f.cik], f) for f in feed if f.cik in ciks]

    # --- Pre-market ---

    async def premarket(self) -> tuple[PremarketReport, str]:
        watch = await self.watchlist()
        now = utcnow()

        async def quote(sym: str, name: str, group: str) -> QuoteRow:
            try:
                q = await self._yahoo.get_quote(sym)
            except SourceError as exc:
                return QuoteRow(
                    symbol=sym,
                    name=name,
                    group=group,
                    price=None,
                    change=None,
                    time=None,
                    error=exc.message,
                )
            change = q.price / q.previous_close - 1.0 if q.previous_close else None
            return QuoteRow(
                symbol=sym,
                name=name,
                group=group,
                price=q.price,
                change=change,
                time=q.time,
                error=None,
            )

        markets = list(await asyncio.gather(*(quote(*o) for o in OVERNIGHT)))

        funding: list[dict[str, str | float | None]] = []
        try:
            crypto = await self._sentinel.crypto()
            funding = [
                {"coin": a.coin, "annualized": a.funding_annualized, "reading": a.funding_reading}
                for a in crypto.assets
            ]
        except SourceError as exc:
            logger.warning("pre-market: sin cripto: %s", exc)

        today_ar = now.astimezone(AR).date()
        cal = await self._calendar.calendar(now=now - timedelta(hours=12))
        events = [e for e in cal.upcoming if e.release_at.astimezone(AR).date() == today_ar]

        earnings_rows: list[FilingRow] = []
        note = "8-K con ítem 2.02 (resultados) de la lista de seguimiento en el feed de la SEC."
        try:
            for ticker, f in await self._filings(watch):
                if "2.02" in f.items:
                    earnings_rows.append(
                        FilingRow(
                            ticker=ticker,
                            company=f.company,
                            items=list(f.items),
                            time=f.updated,
                            link=f.link,
                        )
                    )
        except SourceError as exc:
            note = f"No se pudo leer el feed de la SEC: {exc.message}"
        if not self._sec.configured:
            note = "Configurá SEC_USER_AGENT para ver los resultados publicados."

        levels = [await self._levels(t) for t in watch]
        report = PremarketReport(
            generated_at=now,
            markets=markets,
            crypto_funding=funding,
            events_today=events,
            earnings_overnight=earnings_rows,
            earnings_note=note,
            levels=levels,
            watchlist=watch,
        )
        await self._save("premarket", report)
        return report, premarket_message(report)

    async def _levels(self, ticker: str) -> LevelRow:
        errors: list[str] = []
        close = regime = av_e = av_h = av_l = poc = vah = val = zg = move = None
        expiry = None
        try:
            tech = await self._technical.analyze(ticker)
            close, regime = tech.close, tech.regime.label
            av = {a.key: a.value for a in tech.avwaps if a.status == "OK"}
            av_e, av_h, av_l = av.get("earnings"), av.get("high_52w"), av.get("low_52w")
            if tech.profile:
                poc, vah, val = tech.profile.poc, tech.profile.vah, tech.profile.val
        except SourceError as exc:
            errors.append(f"técnico: {exc.message}")
        try:
            opt = await self._options.analyze(ticker)
            zg = opt.zero_gamma
            if opt.implied_moves:
                move, expiry = opt.implied_moves[0].move, opt.implied_moves[0].expiry
        except SourceError as exc:
            errors.append(f"opciones: {exc.message}")
        return LevelRow(
            ticker=ticker,
            close=close,
            regime=regime,
            avwap_earnings=av_e,
            avwap_high=av_h,
            avwap_low=av_l,
            poc=poc,
            vah=vah,
            val=val,
            zero_gamma=zg,
            implied_move=move,
            implied_expiry=expiry,
            errors=errors,
        )

    # --- Alertas intradía ---

    async def triggers(self, now: datetime | None = None, force: bool = False) -> list[TriggerOut]:
        now = now or utcnow()
        if not force and not session_open(now):
            return []
        now_ny = now.astimezone(NY)
        watch = await self.watchlist()
        try:
            filings = await self._filings(watch)
        except SourceError as exc:
            logger.warning("alertas: sin feed de la SEC: %s", exc)
            filings = []
        today_filings: dict[str, list[CurrentFiling]] = defaultdict(list)
        for ticker, f in filings:
            if f.updated is None or f.updated.astimezone(NY).date() == now_ny.date():
                today_filings[ticker].append(f)

        new: list[TriggerOut] = []
        for ticker in watch:
            prev_low: float | None = None
            try:
                bars = (await self._markets.get_bars(ticker, "5m", "1mo")).bars
            except SourceError as exc:
                logger.warning("alertas: sin velas de %s: %s", ticker, exc)
                bars = []
            days = sorted({b.time.astimezone(NY).date() for b in bars})
            prior = [d for d in days if d < now_ny.date()]
            prev_range = day_range(bars, prior[-1]) if prior else None
            if prev_range:
                prev_low = prev_range[1]
            today = [b for b in bars if b.time.astimezone(NY).date() == now_ny.date()]

            # Volumen anómalo rompiendo un nivel.
            if today and prev_range:
                z, _, used = volume_zscore(bars, now_ny)
                last = today[-1].close
                hi, lo = prev_range
                level_hi, level_lo, label_hi, label_lo = (
                    hi,
                    lo,
                    "el máximo de ayer",
                    "el mínimo de ayer",
                )
                try:
                    daily = (await self._markets.get_bars(ticker, "1d", "1y")).bars[:-1]
                    if daily:
                        y_hi = max(b.high for b in daily[-252:])
                        y_lo = min(b.low for b in daily[-252:])
                        if last > y_hi:
                            level_hi, label_hi = y_hi, "el máximo de 52 semanas"
                        if last < y_lo:
                            level_lo, label_lo = y_lo, "el mínimo de 52 semanas"
                except SourceError:
                    pass
                if z is not None and z >= VOLUME_SIGMA and (last > level_hi or last < level_lo):
                    up = last > level_hi
                    level, label = (level_hi, label_hi) if up else (level_lo, label_lo)
                    cause = _cause(today_filings.get(ticker, []))
                    fact = (
                        f"{'rompe hacia arriba' if up else 'perfora'} {label} ({level:.2f}) "
                        f"con volumen {z:.1f}σ sobre lo normal a esta hora "
                        f"(precio {last:.2f}; base: {used} sesiones)"
                    )
                    stop = (
                        f"cierre debajo de {level:.2f} invalida la ruptura"
                        if up
                        else f"recuperar {level:.2f} invalida la caída; para comprados, el "
                        "nivel ya está perdido"
                    )
                    saved = await self._log(
                        now_ny.date(),
                        ticker,
                        "volumen",
                        "arriba" if up else "abajo",
                        fact,
                        cause,
                        stop,
                    )
                    if saved:
                        new.append(saved)

            # 8-K relevantes.
            for f in today_filings.get(ticker, []):
                relevant = [i for i in f.items if i in ITEMS]
                if not relevant:
                    continue
                fact = "presentó un 8-K: " + ", ".join(f"{i} {ITEMS[i]}" for i in relevant)
                cause = f.link or "ver la presentación en EDGAR"
                stop = (
                    f"mínimo de ayer {prev_low:.2f}"
                    if prev_low is not None
                    else "sin niveles intradía disponibles"
                )
                saved = await self._log(
                    now_ny.date(), ticker, "8-K", f.accession, fact, cause, stop
                )
                if saved:
                    new.append(saved)
        return new

    async def _log(
        self, day: date, ticker: str, kind: str, ref: str, fact: str, cause: str, stop: str
    ) -> TriggerOut | None:
        row = TriggerLog(
            day=day, ticker=ticker, kind=kind, ref=ref, fact=fact, cause=cause, stop=stop
        )
        async with self._sessions() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                return None  # ya se avisó hoy
        return TriggerOut(
            created_at=utcnow(), ticker=ticker, kind=kind, fact=fact, cause=cause, stop=stop
        )

    async def recent_triggers(self, days: int = 5) -> list[TriggerOut]:
        since = utcnow().astimezone(NY).date() - timedelta(days=days)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(TriggerLog)
                        .where(TriggerLog.day >= since)
                        .order_by(TriggerLog.id.desc())
                    )
                )
                .scalars()
                .all()
            )
        return [
            TriggerOut(
                created_at=r.created_at
                if r.created_at.tzinfo
                else r.created_at.replace(tzinfo=UTC),
                ticker=r.ticker,
                kind=r.kind,
                fact=r.fact,
                cause=r.cause,
                stop=r.stop,
            )
            for r in rows
        ]

    # --- Cierre ---

    async def ledger(self) -> tuple[LedgerReport, str]:
        invalidated = await self._theses.check_all_active()
        rows = await asyncio.gather(*(self._sector_row(s, n) for s, n in BENCHMARKS + SECTORS))
        bench, sectors = list(rows[: len(BENCHMARKS)]), list(rows[len(BENCHMARKS) :])
        spy = bench[0]
        for r in bench[1:] + sectors:
            if r.change_1d is not None and spy.change_1d is not None:
                r.vs_spy_1d = r.change_1d - spy.change_1d
            if r.change_1m is not None and spy.change_1m is not None:
                r.vs_spy_1m = r.change_1m - spy.change_1m
        rotation = _rotation(sectors)
        session_date = None
        try:
            prices = await self._markets.get_prices("SPY", "1y")
            session_date = prices.dates[-1] if prices.dates else None
        except SourceError:
            pass
        report = LedgerReport(
            generated_at=utcnow(),
            session_date=session_date,
            benchmarks=bench,
            sectors=sorted(
                sectors, key=lambda r: -(r.change_1d if r.change_1d is not None else -9)
            ),
            rotation=rotation,
            theses=await self._theses.all(),
            invalidated_today=[f"{t.ticker}: {reason}" for t, reason in invalidated],
        )
        await self._save("ledger", report)
        return report, ledger_message(report)

    async def _sector_row(self, symbol: str, name: str) -> SectorRow:
        try:
            s = await self._markets.get_prices(symbol, "1y")
        except SourceError:
            return SectorRow(
                symbol=symbol,
                name=name,
                change_1d=None,
                change_1w=None,
                change_1m=None,
                vs_spy_1d=None,
                vs_spy_1m=None,
                above_ema20=None,
                above_ema50=None,
                above_ema200=None,
            )
        closes = s.closes
        last = closes[-1] if closes else None

        def above(period: int) -> bool | None:
            e = ema(closes, period)[-1] if closes else None
            return None if e is None or last is None else last > e

        return SectorRow(
            symbol=symbol,
            name=name,
            change_1d=_change(closes, 1),
            change_1w=_change(closes, 5),
            change_1m=_change(closes, 21),
            vs_spy_1d=None,
            vs_spy_1m=None,
            above_ema20=above(20),
            above_ema50=above(50),
            above_ema200=above(200),
        )


def _cause(filings: list[CurrentFiling]) -> str:
    items = [i for f in filings for i in f.items if i in ITEMS]
    if items:
        return "8-K de hoy: " + ", ".join(f"{i} {ITEMS[i]}" for i in dict.fromkeys(items))
    return "sin presentación en la SEC hoy: flujo de mercado o noticia externa"


CYCLICAL = {"XLY", "XLI", "XLF", "XLB", "XLE"}
DEFENSIVE = {"XLP", "XLU", "XLV"}


def _rotation(sectors: list[SectorRow]) -> str | None:
    cyc = [r.change_1m for r in sectors if r.symbol in CYCLICAL and r.change_1m is not None]
    dfn = [r.change_1m for r in sectors if r.symbol in DEFENSIVE and r.change_1m is not None]
    if not cyc or not dfn:
        return None
    diff = statistics.fmean(cyc) - statistics.fmean(dfn)
    win, lose = ("cíclicos", "defensivos") if diff > 0 else ("defensivos", "cíclicos")
    return (
        f"Último mes: los sectores {win} le ganan a los {lose} por "
        f"{abs(diff) * 100:.1f} puntos porcentuales.".replace(".", ",", 1)
    )


def _pct(v: float | None) -> str:
    return "s/d" if v is None else f"{v * 100:+.1f} %".replace(".", ",")


def premarket_message(r: PremarketReport) -> str:
    lines = ["🌅 Pre-market"]
    fut = [m for m in r.markets if m.group == "Futuros EE.UU."]
    lines.append(
        "Futuros: " + " · ".join(f"{m.name.replace('Futuros ', '')} {_pct(m.change)}" for m in fut)
    )
    world = [m for m in r.markets if m.group in ("Asia", "Europa") and m.change is not None]
    if world:
        big = max(world, key=lambda m: abs(m.change or 0))
        lines.append(f"Mayor movimiento afuera: {big.name} {_pct(big.change)}")
    for c in r.crypto_funding:
        if c.get("reading") and c.get("reading") != "neutral":
            lines.append(f"{c['coin']}: {c['reading']}")
    high = [e for e in r.events_today if e.impact == "High"]
    if high:
        lines.append(
            "Hoy: " + "; ".join(f"{e.release_at.astimezone(AR):%H:%M} {e.name}" for e in high[:5])
        )
    if r.earnings_overnight:
        lines.append(
            "Resultados publicados: " + ", ".join(sorted({f.ticker for f in r.earnings_overnight}))
        )
    moves = [lv for lv in r.levels if lv.implied_move is not None]
    if moves:
        lines.append(
            "Mov. implícito: "
            + " · ".join(
                f"{lv.ticker} ±{(lv.implied_move or 0) * 100:.1f} %".replace(".", ",")
                for lv in moves[:5]
            )
        )
    return "\n".join(lines)


def ledger_message(r: LedgerReport) -> str:
    lines = ["🌙 Cierre"]
    lines.append(" · ".join(f"{b.symbol} {_pct(b.change_1d)}" for b in r.benchmarks))
    ranked = [s for s in r.sectors if s.change_1d is not None]
    if ranked:
        lines.append(
            f"Mejor: {ranked[0].name} {_pct(ranked[0].change_1d)} · Peor: "
            f"{ranked[-1].name} {_pct(ranked[-1].change_1d)}"
        )
    if r.rotation:
        lines.append(r.rotation)
    if r.invalidated_today:
        lines.append("🛑 KILL SWITCH (sin promediar a la baja):")
        lines.extend(f"• {x}" for x in r.invalidated_today)
    active = [t for t in r.theses if t.status == "activa"]
    if active:
        lines.append(f"Tesis activas: {', '.join(t.ticker for t in active)}")
    return "\n".join(lines)


def trigger_message(t: TriggerOut) -> str:
    return f"⚡ {t.ticker}\nHecho: {t.fact}\nCausa: {t.cause}\nStop técnico: {t.stop}"
