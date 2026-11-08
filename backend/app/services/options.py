"""Opciones: exposición gamma de los market makers (GEX), movimiento implícito y variación del
interés abierto.

GEX ("naive", el cálculo estándar de la industria): se supone que los market makers están
comprados en calls y vendidos en puts (el público compra calls para apostar y puts para
cubrirse). Por cada contrato:
    GEX = gamma × interés abierto × 100 × precio² × 1 %     (USD por cada 1 % de movimiento)
con signo + para calls y − para puts. GEX total positivo: los market makers venden en las subas
y compran en las bajas → la volatilidad se amortigua. Negativo: hacen lo contrario → se acelera.

Gamma: Black-Scholes con la volatilidad implícita de cada contrato (tasa 0, sin dividendos), así
se puede recalcular para otros precios y buscar el nivel "zero gamma" donde cambia el signo.
Los contratos sin volatilidad implícita se excluyen y se cuentan.

Movimiento implícito: precio del straddle ATM (call + put del strike más cercano, a precio medio)
sobre el precio del subyacente. Es el movimiento que el mercado descuenta hasta ese vencimiento.
"""

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cache import TTLCache
from app.core.db import OptionSnapshot, SystemState, utcnow
from app.domain import SourceInfo, StrictModel
from app.sources.cboe import SOURCE, CboeClient, OptionChain, OptionQuote

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
CONTRACT = 100
MIN_T = 1 / (365 * 24)  # una hora: evita gammas infinitas en el día del vencimiento
PROFILE_RANGE = 0.15  # strikes y simulación de precio a ±15 %
WATCHLIST_KEY = "options_watchlist"
DEFAULT_WATCHLIST = ["SPY", "QQQ", "IWM", "^SPX", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META"]


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_gamma(spot: float, strike: float, t: float, sigma: float) -> float:
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    return norm_pdf(d1) / (spot * sigma * math.sqrt(t))


def years_to_expiry(expiry: date, now: datetime) -> float:
    close = datetime.combine(expiry, time(16, 0), tzinfo=NY)
    return max((close - now).total_seconds() / (365 * 86400), MIN_T)


def option_gex(o: OptionQuote, spot: float, now: datetime) -> float | None:
    if o.iv is None or o.open_interest <= 0:
        return None
    g = bs_gamma(spot, o.strike, years_to_expiry(o.expiry, now), o.iv)
    sign = 1.0 if o.kind == "C" else -1.0
    return sign * g * o.open_interest * CONTRACT * spot * spot * 0.01


def live(options: list[OptionQuote], now: datetime) -> list[OptionQuote]:
    today = now.astimezone(NY).date()
    return [o for o in options if o.expiry >= today]


def total_gex(options: list[OptionQuote], spot: float, now: datetime) -> float:
    return math.fsum(g for o in options if (g := option_gex(o, spot, now)) is not None)


def zero_gamma(
    options: list[OptionQuote], spot: float, now: datetime, steps: int = 120
) -> float | None:
    """Precio en el que el GEX total cambia de signo, el más cercano al actual (aprox. lineal
    entre los puntos de la grilla)."""
    lo, hi = spot * (1 - PROFILE_RANGE), spot * (1 + PROFILE_RANGE)
    grid = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    values = [total_gex(options, s, now) for s in grid]
    crossings: list[float] = []
    for i in range(steps):
        a, b = values[i], values[i + 1]
        if a == 0:
            crossings.append(grid[i])
        elif a * b < 0:
            crossings.append(grid[i] + (grid[i + 1] - grid[i]) * a / (a - b))
    return min(crossings, key=lambda s: abs(s - spot)) if crossings else None


def mid(o: OptionQuote) -> float | None:
    if o.bid is None or o.ask is None or o.bid <= 0 or o.ask <= 0 or o.ask < o.bid:
        return None
    return (o.bid + o.ask) / 2


@dataclass(frozen=True)
class ImpliedMoveCalc:
    expiry: date
    strike: float
    straddle: float
    move: float  # fracción del precio


def implied_moves(chain: OptionChain, now: datetime, count: int = 4) -> list[ImpliedMoveCalc]:
    by_expiry: dict[date, dict[tuple[str, float], OptionQuote]] = defaultdict(dict)
    for o in live(chain.options, now):
        by_expiry[o.expiry][(o.kind, o.strike)] = o
    out: list[ImpliedMoveCalc] = []
    for expiry in sorted(by_expiry)[: count + 2]:
        quotes = by_expiry[expiry]
        strikes = sorted({k for _, k in quotes}, key=lambda k: abs(k - chain.spot))
        for k in strikes[:3]:  # el más cercano con call y put cotizando
            c, p = quotes.get(("C", k)), quotes.get(("P", k))
            mc, mp = (mid(c) if c else None), (mid(p) if p else None)
            if mc is not None and mp is not None:
                out.append(ImpliedMoveCalc(expiry, k, mc + mp, (mc + mp) / chain.spot))
                break
        if len(out) == count:
            break
    return out


# ---------- Modelos ----------


class StrikeGex(StrictModel):
    strike: float
    calls: float
    puts: float
    total: float
    call_oi: float
    put_oi: float


class ImpliedMove(StrictModel):
    expiry: date
    days: int
    strike: float
    straddle: float
    move: float
    low: float
    high: float


class SnapshotOut(StrictModel):
    date: date
    spot: float
    call_oi: float
    put_oi: float
    total_gex: float
    zero_gamma: float | None


class OiReading(StrictModel):
    since: date
    price_change: float
    call_oi_change: float | None
    put_oi_change: float | None
    reading: str


class OptionsResponse(StrictModel):
    symbol: str
    spot: float
    as_of: datetime | None
    total_gex: float
    regime: str  # gamma positiva / negativa
    zero_gamma: float | None
    call_wall: float | None
    put_wall: float | None
    put_call_oi: float | None
    call_oi: float
    put_oi: float
    by_strike: list[StrikeGex]
    implied_moves: list[ImpliedMove]
    excluded_contracts: int
    history: list[SnapshotOut]
    oi_reading: OiReading | None
    source: SourceInfo


@dataclass(frozen=True)
class Analysis:
    chain: OptionChain
    total: float
    flip: float | None
    call_wall: float | None
    put_wall: float | None
    call_oi: float
    put_oi: float
    by_strike: list[StrikeGex]
    moves: list[ImpliedMoveCalc]
    excluded: int


def analyze_chain(chain: OptionChain, now: datetime) -> Analysis:
    opts = live(chain.options, now)
    spot = chain.spot
    agg: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    excluded = 0
    for o in opts:
        g = option_gex(o, spot, now)
        if g is None:
            if o.open_interest > 0:
                excluded += 1
            continue
        row = agg[o.strike]
        if o.kind == "C":
            row[0] += g
            row[2] += o.open_interest
        else:
            row[1] += g
            row[3] += o.open_interest
    near = [
        StrikeGex(strike=k, calls=v[0], puts=v[1], total=v[0] + v[1], call_oi=v[2], put_oi=v[3])
        for k, v in sorted(agg.items())
        if abs(k / spot - 1) <= PROFILE_RANGE
    ]
    call_wall = max(near, key=lambda s: s.calls).strike if near else None
    put_wall = min(near, key=lambda s: s.puts).strike if near else None
    call_oi = math.fsum(o.open_interest for o in opts if o.kind == "C")
    put_oi = math.fsum(o.open_interest for o in opts if o.kind == "P")
    return Analysis(
        chain=chain,
        total=total_gex(opts, spot, now),
        flip=zero_gamma(opts, spot, now),
        call_wall=call_wall,
        put_wall=put_wall,
        call_oi=call_oi,
        put_oi=put_oi,
        by_strike=near,
        moves=implied_moves(chain, now),
        excluded=excluded,
    )


def oi_reading(prev: OptionSnapshot, cur: OptionSnapshot) -> OiReading:
    dp = cur.spot / prev.spot - 1.0
    dc = cur.call_oi / prev.call_oi - 1.0 if prev.call_oi else None
    dpt = cur.put_oi / prev.put_oi - 1.0 if prev.put_oi else None
    total_prev, total_cur = prev.call_oi + prev.put_oi, cur.call_oi + cur.put_oi
    oi_up = total_cur > total_prev
    if dp >= 0 and oi_up:
        text = "suba con posiciones nuevas: el movimiento tiene respaldo"
    elif dp >= 0:
        text = (
            "suba con cierre de posiciones: puede ser cobertura de vendidos (squeeze) más que "
            "compra genuina"
        )
    elif oi_up:
        text = "baja con posiciones nuevas: presión vendedora o compra de coberturas"
    else:
        text = "baja con cierre de posiciones: liquidación de comprados"
    if dc is not None and dp > 0 and dc > 0.05:
        text += "; fuerte suba del interés abierto en calls (posible gamma squeeze)"
    return OiReading(
        since=prev.date, price_change=dp, call_oi_change=dc, put_oi_change=dpt, reading=text
    )


# ---------- Servicio ----------


class OptionsService:
    def __init__(self, cboe: CboeClient, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._cboe = cboe
        self._sessions = sessions
        self._cache: TTLCache[OptionChain] = TTLCache(10 * 60, max_entries=32)

    async def _chain(self, symbol: str) -> OptionChain:
        return await self._cache.get_or_load(symbol, lambda: self._cboe.chain(symbol))

    async def watchlist(self) -> list[str]:
        async with self._sessions() as session:
            row = await session.get(SystemState, WATCHLIST_KEY)
        return [str(s) for s in json.loads(row.value)] if row else list(DEFAULT_WATCHLIST)

    async def set_watchlist(self, symbols: list[str]) -> list[str]:
        clean = list(dict.fromkeys(symbols))
        async with self._sessions() as session:
            row = await session.get(SystemState, WATCHLIST_KEY)
            if row is None:
                session.add(SystemState(key=WATCHLIST_KEY, value=json.dumps(clean)))
            else:
                row.value = json.dumps(clean)
                row.updated_at = utcnow()
            await session.commit()
        return clean

    async def analyze(self, symbol: str, now: datetime | None = None) -> OptionsResponse:
        now = now or datetime.now(UTC)
        a = analyze_chain(await self._chain(symbol), now)
        history = await self._history(symbol)
        reading = oi_reading(history[-2], history[-1]) if len(history) >= 2 else None
        spot = a.chain.spot
        return OptionsResponse(
            symbol=a.chain.symbol,
            spot=spot,
            as_of=a.chain.as_of,
            total_gex=a.total,
            regime=(
                "gamma positiva: los market makers amortiguan los movimientos"
                if a.total >= 0
                else "gamma negativa: los market makers amplifican los movimientos"
            ),
            zero_gamma=a.flip,
            call_wall=a.call_wall,
            put_wall=a.put_wall,
            put_call_oi=a.put_oi / a.call_oi if a.call_oi else None,
            call_oi=a.call_oi,
            put_oi=a.put_oi,
            by_strike=a.by_strike,
            implied_moves=[
                ImpliedMove(
                    expiry=m.expiry,
                    days=(m.expiry - now.astimezone(NY).date()).days,
                    strike=m.strike,
                    straddle=m.straddle,
                    move=m.move,
                    low=spot * (1 - m.move),
                    high=spot * (1 + m.move),
                )
                for m in a.moves
            ],
            excluded_contracts=a.excluded,
            history=[
                SnapshotOut(
                    date=h.date,
                    spot=h.spot,
                    call_oi=h.call_oi,
                    put_oi=h.put_oi,
                    total_gex=h.total_gex,
                    zero_gamma=h.zero_gamma,
                )
                for h in history
            ],
            oi_reading=reading,
            source=SourceInfo(
                name=SOURCE, url=self._cboe.url(symbol), fetched_at=datetime.now(UTC)
            ),
        )

    async def _history(self, symbol: str) -> list[OptionSnapshot]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(OptionSnapshot)
                        .where(OptionSnapshot.symbol == symbol.upper())
                        .order_by(OptionSnapshot.date.desc())
                        .limit(60)
                    )
                )
                .scalars()
                .all()
            )
        return list(reversed(rows))

    async def snapshot(
        self, symbol: str, now: datetime | None = None
    ) -> tuple[OptionSnapshot, OptionSnapshot | None]:
        """Guarda (o reemplaza) la foto del día. Devuelve la nueva y la anterior."""
        now = now or datetime.now(UTC)
        self._cache.clear()  # la foto de cierre se toma con datos frescos
        a = analyze_chain(await self._chain(symbol), now)
        day = now.astimezone(NY).date()
        sym = a.chain.symbol
        async with self._sessions() as session:
            prev = (
                await session.execute(
                    select(OptionSnapshot)
                    .where(OptionSnapshot.symbol == sym, OptionSnapshot.date < day)
                    .order_by(OptionSnapshot.date.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            row = (
                await session.execute(
                    select(OptionSnapshot).where(
                        OptionSnapshot.symbol == sym, OptionSnapshot.date == day
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = OptionSnapshot(symbol=sym, date=day)
                session.add(row)
            row.spot, row.call_oi, row.put_oi = a.chain.spot, a.call_oi, a.put_oi
            row.total_gex, row.zero_gamma = a.total, a.flip
            row.created_at = utcnow()
            await session.commit()
            await session.refresh(row)
        return row, prev


def regime_flip_message(sym: str, cur: OptionSnapshot, prev: OptionSnapshot | None) -> str | None:
    """Aviso sólo cuando el régimen de gamma cambia de signo respecto de la foto anterior."""
    if prev is None or (prev.total_gex >= 0) == (cur.total_gex >= 0):
        return None
    if cur.total_gex < 0:
        level = f" (zero gamma ≈ {cur.zero_gamma:.2f})" if cur.zero_gamma else ""
        return f"⚡ {sym} pasó a gamma negativa{level}: los movimientos pueden acelerarse."
    return f"🧊 {sym} volvió a gamma positiva: la volatilidad tiende a amortiguarse."
