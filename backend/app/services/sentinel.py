"""Centinela 24/7 (Columna 4): derivados cripto, commodities y monedas, y arbitraje ADR/local.

- Cripto (OKX): la tasa de financiamiento de los perpetuos mide quién paga por mantener la
  posición. Positiva y alta = muchos apalancados comprados; negativa = predominan los cortos. El
  interés abierto y las liquidaciones forzadas muestran cuánto apalancamiento hay y cuánto se
  está limpiando. Cripto opera los fines de semana: es la primera lectura antes de la apertura.
- Commodities y monedas (Yahoo, futuros de primer vencimiento y tipos de cambio): alertas
  tempranas de costos. El cociente cobre/oro sube cuando el mercado espera más actividad.
- Arbitraje: CCL implícito de cada ADR argentino = precio local (ARS) × acciones por ADR / precio
  del ADR (USD). Se compara con el CCL de mercado y con la mediana de los ADRs. Los CEDEARs usan
  ratios cargados por el usuario, porque cambian con el tiempo.
"""

import asyncio
import json
import logging
import math
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cache import TTLCache
from app.core.db import SystemState, utcnow
from app.domain import StrictModel
from app.services.ar_macro import ArMacroService
from app.services.markets import MarketsService
from app.sources.errors import SourceError
from app.sources.okx import OkxClient
from app.sources.yahoo import Quote, YahooClient

logger = logging.getLogger(__name__)
T = TypeVar("T")

COINS = ("BTC", "ETH", "SOL")
FUNDINGS_PER_DAY = 3
LIQ_LIMIT = 100


@dataclass(frozen=True)
class AdrSpec:
    adr: str
    local: str
    shares_per_adr: float
    name: str


# Acciones locales por ADR, según los programas de ADR vigentes al armar esta lista. Si un
# programa cambia su ratio, se corrige acá (la página muestra el ratio usado).
ADRS: list[AdrSpec] = [
    AdrSpec("GGAL", "GGAL.BA", 10, "Grupo Financiero Galicia"),
    # Verificado contra precios reales (nov-2026): con 10 el CCL implícito coincide con el resto
    # de los ADRs; con 1 daba $167, imposible.
    AdrSpec("YPF", "YPFD.BA", 10, "YPF"),
    AdrSpec("PAM", "PAMP.BA", 25, "Pampa Energía"),
    AdrSpec("BMA", "BMA.BA", 10, "Banco Macro"),
    AdrSpec("BBAR", "BBAR.BA", 3, "BBVA Argentina"),
    AdrSpec("SUPV", "SUPV.BA", 5, "Grupo Supervielle"),
    AdrSpec("CEPU", "CEPU.BA", 10, "Central Puerto"),
    AdrSpec("TGS", "TGSU2.BA", 5, "Transportadora de Gas del Sur"),
    AdrSpec("TEO", "TECO2.BA", 5, "Telecom Argentina"),
    AdrSpec("LOMA", "LOMA.BA", 5, "Loma Negra"),
    AdrSpec("EDN", "EDN.BA", 20, "Edenor"),
    AdrSpec("CRESY", "CRES.BA", 10, "Cresud"),
    AdrSpec("IRS", "IRSA.BA", 10, "IRSA"),
]
CEDEAR_KEY = "cedear_ratios"
# Dos precios tomados con más de 2 horas de diferencia no se pueden comparar.
MAX_QUOTE_GAP = timedelta(hours=2)
RATIO_SANITY = 0.30

COMMODITIES: list[tuple[str, str, str]] = [
    ("CL=F", "Petróleo WTI", "Energía"),
    ("BZ=F", "Petróleo Brent", "Energía"),
    ("NG=F", "Gas natural", "Energía"),
    ("HG=F", "Cobre", "Metales"),
    ("GC=F", "Oro", "Metales"),
    ("SI=F", "Plata", "Metales"),
    ("ZS=F", "Soja", "Granos"),
    ("ZC=F", "Maíz", "Granos"),
    ("ZW=F", "Trigo", "Granos"),
    ("EURUSD=X", "Euro (USD por EUR)", "Monedas"),
    ("USDJPY=X", "Yen (JPY por USD)", "Monedas"),
    ("USDCNY=X", "Yuan (CNY por USD)", "Monedas"),
    ("USDBRL=X", "Real (BRL por USD)", "Monedas"),
    ("USDMXN=X", "Peso mexicano (MXN por USD)", "Monedas"),
]
CHANGE_WINDOWS: list[tuple[str, int]] = [
    ("1 día", 1),
    ("1 semana", 7),
    ("1 mes", 30),
    ("1 año", 365),
]


# ---------- Modelos ----------


class Point(StrictModel):
    t: str  # fecha u hora ISO
    v: float


class CryptoAsset(StrictModel):
    coin: str
    price: float | None
    funding_rate: float | None  # por 8 h (fracción)
    funding_annualized: float | None
    funding_avg_7d: float | None  # anualizada
    funding_reading: str | None
    funding_history: list[Point]  # anualizada
    open_interest_usd: float | None
    open_interest_change_7d: float | None
    open_interest_history: list[Point]
    long_short_ratio: float | None
    long_short_change_24h: float | None
    liq_long_usd: float | None
    liq_short_usd: float | None
    liq_count: int
    liq_since: datetime | None
    liq_capped: bool  # OKX devuelve como máximo 100 órdenes: puede cubrir menos de 24 h
    errors: list[str]


class CryptoResponse(StrictModel):
    assets: list[CryptoAsset]
    exchange: str
    computed_at: datetime


class Change(StrictModel):
    label: str
    value: float | None  # fracción


class MarketRow(StrictModel):
    symbol: str
    name: str
    group: str
    last: float | None
    date: date | None
    changes: list[Change]
    spark: list[float]  # últimos 60 cierres
    error: str | None


class CommoditiesResponse(StrictModel):
    rows: list[MarketRow]
    copper_gold: list[Point]  # cociente cobre/oro (× 1000), último año
    copper_gold_change_3m: float | None
    note: str
    computed_at: datetime


class ArbRow(StrictModel):
    kind: str  # "ADR" o "CEDEAR"
    foreign: str
    local: str
    name: str | None
    ratio: float
    foreign_price: float | None
    local_price: float | None
    foreign_time: datetime | None
    local_time: datetime | None
    implied_fx: float | None
    vs_ccl: float | None  # implícito / CCL de mercado − 1
    vs_median: float | None
    comparable: bool
    note: str | None


class ArbitrageResponse(StrictModel):
    rows: list[ArbRow]
    ccl_market: float | None
    ccl_median_implied: float | None
    cedear_ratios: list[dict[str, Any]]
    computed_at: datetime


class CedearRatio(StrictModel):
    local: str  # ej. AAPL.BA
    foreign: str  # ej. AAPL
    ratio: float  # CEDEARs por acción


# ---------- Cálculos ----------


def annualize(rate: float) -> float:
    return rate * FUNDINGS_PER_DAY * 365


def funding_reading(annualized: float) -> str:
    if annualized >= 0.30:
        return "apalancamiento comprado muy alto: riesgo de liquidaciones en cadena si cae"
    if annualized >= 0.10:
        return "predominan los comprados apalancados"
    if annualized >= 0:
        return "neutral"
    return "predominan los vendidos: posible rebote por cierre de cortos"


def implied_fx(local_ars: float, shares_per_adr: float, adr_usd: float) -> float:
    return local_ars * shares_per_adr / adr_usd


def change_since(dates: list[date], closes: list[float], days: int) -> float | None:
    if len(dates) < 2:
        return None
    target = dates[-1] - timedelta(days=days)
    base = None
    for d, c in zip(dates, closes, strict=True):
        if d <= target:
            base = (d, c)
        else:
            break
    if base is None or (target - base[0]).days > max(4, days // 7):
        return None
    return closes[-1] / base[1] - 1.0


# ---------- Servicio ----------


class SentinelService:
    def __init__(
        self,
        okx: OkxClient,
        yahoo: YahooClient,
        markets: MarketsService,
        ar: ArMacroService,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._okx = okx
        self._yahoo = yahoo
        self._markets = markets
        self._ar = ar
        self._sessions = sessions
        # Cortas: la página se refresca sola cada 30-60 s.
        self._crypto_cache: TTLCache[CryptoResponse] = TTLCache(30)
        self._quote_cache: TTLCache[Quote] = TTLCache(30, max_entries=256)

    # --- Cripto ---

    async def crypto(self) -> CryptoResponse:
        return await self._crypto_cache.get_or_load("crypto", self._crypto)

    async def _crypto(self) -> CryptoResponse:
        assets = await asyncio.gather(*(self._coin(c) for c in COINS))
        return CryptoResponse(assets=list(assets), exchange="OKX", computed_at=datetime.now(UTC))

    async def _coin(self, coin: str) -> CryptoAsset:
        inst, uly = f"{coin}-USDT-SWAP", f"{coin}-USDT"
        errors: list[str] = []

        async def safe(label: str, factory: Callable[[], Awaitable[T]]) -> T | None:
            try:
                return await factory()
            except SourceError as exc:
                errors.append(f"{label}: {exc.message}")
                return None

        # Tareas separadas (y no un único gather) para conservar el tipo de cada resultado.
        t_price = asyncio.create_task(safe("precio", lambda: self._okx.last_price(inst)))
        t_funding = asyncio.create_task(safe("financiamiento", lambda: self._okx.funding(inst)))
        t_history = asyncio.create_task(
            safe("historial de financiamiento", lambda: self._okx.funding_history(inst, 90))
        )
        t_oi = asyncio.create_task(safe("interés abierto", lambda: self._okx.open_interest(inst)))
        t_oi_hist = asyncio.create_task(
            safe("historial de interés abierto", lambda: self._okx.oi_history(coin))
        )
        t_ls = asyncio.create_task(safe("largos/cortos", lambda: self._okx.long_short_ratio(coin)))
        t_ct = asyncio.create_task(
            safe("tamaño de contrato", lambda: self._okx.contract_value(inst))
        )
        t_liqs = asyncio.create_task(safe("liquidaciones", lambda: self._okx.liquidations(uly)))
        price, funding, history = await t_price, await t_funding, await t_history
        oi, oi_hist, ls, ct, liqs = (
            await t_oi,
            await t_oi_hist,
            await t_ls,
            await t_ct,
            await t_liqs,
        )
        ann = annualize(funding.rate) if funding else None
        recent = [f.rate for f in (history or [])][-21:]  # 7 días = 21 períodos de 8 h
        oi_usd = (
            oi.usd
            if oi and oi.usd is not None
            else (oi.coins * price if oi and oi.coins is not None and price is not None else None)
        )
        oi_change = None
        if oi_hist and len(oi_hist) >= 8:
            oi_change = oi_hist[-1][1] / oi_hist[-8][1] - 1.0 if oi_hist[-8][1] else None
        ls_now = ls[-1][1] if ls else None
        ls_change = ls[-1][1] - ls[-25][1] if ls and len(ls) >= 25 else None

        liq_long = liq_short = None
        liq_count = 0
        liq_since = None
        liq_capped = False
        if liqs is not None and ct is not None:
            day_ago = datetime.now(UTC) - timedelta(hours=24)
            window = [x for x in liqs if x.time >= day_ago]
            liq_count = len(window)
            liq_long = math.fsum(x.contracts * ct * x.price for x in window if x.side == "long")
            liq_short = math.fsum(x.contracts * ct * x.price for x in window if x.side == "short")
            liq_since = min((x.time for x in window), default=None)
            liq_capped = len(liqs) >= LIQ_LIMIT

        return CryptoAsset(
            coin=coin,
            price=price,
            funding_rate=funding.rate if funding else None,
            funding_annualized=ann,
            funding_avg_7d=annualize(statistics.fmean(recent)) if recent else None,
            funding_reading=funding_reading(ann) if ann is not None else None,
            # Redondeado a la hora para que los cobros de las tres monedas caigan en el mismo punto.
            funding_history=[
                Point(
                    t=f.time.replace(minute=0, second=0, microsecond=0).isoformat(),
                    v=annualize(f.rate),
                )
                for f in (history or [])
                if f.time
            ],
            open_interest_usd=oi_usd,
            open_interest_change_7d=oi_change,
            open_interest_history=[Point(t=t.date().isoformat(), v=v) for t, v in (oi_hist or [])],
            long_short_ratio=ls_now,
            long_short_change_24h=ls_change,
            liq_long_usd=liq_long,
            liq_short_usd=liq_short,
            liq_count=liq_count,
            liq_since=liq_since,
            liq_capped=liq_capped,
            errors=errors,
        )

    # --- Commodities y monedas ---

    async def commodities(self) -> CommoditiesResponse:
        async def row(symbol: str, name: str, group: str) -> tuple[MarketRow, Any]:
            try:
                # 2 años para que exista el dato base del cambio a 1 año.
                s = await self._markets.get_prices(symbol, "2y")
            except SourceError as exc:
                return MarketRow(
                    symbol=symbol,
                    name=name,
                    group=group,
                    last=None,
                    date=None,
                    changes=[],
                    spark=[],
                    error=exc.message,
                ), None
            year_start = date(s.dates[-1].year, 1, 1) if s.dates else None
            ytd = None
            if year_start and s.dates:
                prior = [c for d, c in zip(s.dates, s.closes, strict=True) if d < year_start]
                ytd = s.closes[-1] / prior[-1] - 1.0 if prior else None
            changes = [
                Change(label=label, value=change_since(s.dates, s.closes, days))
                for label, days in CHANGE_WINDOWS
            ]
            changes.insert(3, Change(label="en el año", value=ytd))
            return MarketRow(
                symbol=symbol,
                name=name,
                group=group,
                last=s.closes[-1] if s.closes else None,
                date=s.dates[-1] if s.dates else None,
                changes=changes,
                spark=s.closes[-60:],
                error=None,
            ), s

        results = await asyncio.gather(*(row(*c) for c in COMMODITIES))
        series = {sym: s for (sym, _, _), (_, s) in zip(COMMODITIES, results, strict=True)}
        copper, gold = series.get("HG=F"), series.get("GC=F")
        cg: list[Point] = []
        cg_change = None
        if copper is not None and gold is not None:
            gm = dict(zip(gold.dates, gold.closes, strict=True))
            pairs = [
                (d, c / gm[d] * 1000)
                for d, c in zip(copper.dates, copper.closes, strict=True)
                if gm.get(d)
            ]
            pairs = (
                [(d, v) for d, v in pairs if d >= pairs[-1][0] - timedelta(days=365)]
                if pairs
                else []
            )
            cg = [Point(t=d.isoformat(), v=v) for d, v in pairs]
            if pairs:
                cg_change = change_since([d for d, _ in pairs], [v for _, v in pairs], 91)
        return CommoditiesResponse(
            rows=[r for r, _ in results],
            copper_gold=cg,
            copper_gold_change_3m=cg_change,
            note="Futuros de primer vencimiento: al cambiar de contrato puede haber saltos que no "
            "son movimientos de precio.",
            computed_at=datetime.now(UTC),
        )

    # --- Arbitraje ---

    async def cedear_ratios(self) -> list[CedearRatio]:
        async with self._sessions() as session:
            row = await session.get(SystemState, CEDEAR_KEY)
        if row is None:
            return []
        return [CedearRatio.model_validate(x) for x in json.loads(row.value)]

    async def set_cedear_ratios(self, ratios: list[CedearRatio]) -> list[CedearRatio]:
        payload = json.dumps([r.model_dump() for r in ratios])
        async with self._sessions() as session:
            row = await session.get(SystemState, CEDEAR_KEY)
            if row is None:
                session.add(SystemState(key=CEDEAR_KEY, value=payload))
            else:
                row.value = payload
                row.updated_at = utcnow()
            await session.commit()
        return ratios

    async def _quote(self, symbol: str) -> Quote | None:
        try:
            return await self._quote_cache.get_or_load(
                symbol, lambda: self._yahoo.get_quote(symbol)
            )
        except SourceError as exc:
            logger.warning("arbitraje: sin precio de %s: %s", symbol, exc)
            return None

    async def arbitrage(self) -> ArbitrageResponse:
        cedears = await self.cedear_ratios()
        pairs: list[tuple[str, str, str, float, str | None]] = [
            ("ADR", a.adr, a.local, a.shares_per_adr, a.name) for a in ADRS
        ] + [("CEDEAR", c.foreign, c.local, c.ratio, None) for c in cedears]
        symbols = sorted({p[1] for p in pairs} | {p[2] for p in pairs})
        quotes = dict(
            zip(symbols, await asyncio.gather(*(self._quote(s) for s in symbols)), strict=True)
        )
        ccl = await self._ccl_market()

        rows: list[ArbRow] = []
        for kind, foreign, local, ratio, name in pairs:
            fq, lq = quotes.get(foreign), quotes.get(local)
            note, comparable, fx = None, False, None
            if fq is None or lq is None:
                note = "sin precio de " + (foreign if fq is None else local)
            elif lq.currency not in (None, "ARS"):
                note = f"el precio local viene en {lq.currency}, no en pesos"
            else:
                # CEDEAR: el ratio es CEDEARs por acción → acción = ratio × precio del CEDEAR.
                fx = implied_fx(lq.price, ratio, fq.price)
                gap = abs(fq.time - lq.time)
                comparable = gap <= MAX_QUOTE_GAP
                if not comparable:
                    note = f"precios tomados con {gap.total_seconds() / 3600:.0f} h de diferencia"
            rows.append(
                ArbRow(
                    kind=kind,
                    foreign=foreign,
                    local=local,
                    name=name or (fq.name if fq else None),
                    ratio=ratio,
                    foreign_price=fq.price if fq else None,
                    local_price=lq.price if lq else None,
                    foreign_time=fq.time if fq else None,
                    local_time=lq.time if lq else None,
                    implied_fx=fx,
                    vs_ccl=None,
                    vs_median=None,
                    comparable=comparable,
                    note=note,
                )
            )

        valid = [
            r.implied_fx
            for r in rows
            if r.implied_fx is not None and r.comparable and r.kind == "ADR"
        ]
        median = statistics.median(valid) if valid else None
        for r in rows:
            if r.implied_fx is None:
                continue
            r.vs_ccl = r.implied_fx / ccl - 1.0 if ccl else None
            r.vs_median = r.implied_fx / median - 1.0 if median else None
            # Un desvío enorme no es un arbitraje: casi siempre es un ratio mal cargado o que
            # cambió. Se marca y se saca de la comparación.
            if r.vs_median is not None and abs(r.vs_median) > RATIO_SANITY:
                r.comparable = False
                warning = (
                    f"CCL implícito {r.vs_median * 100:+.0f} % contra la mediana: el ratio de "
                    f"{r.ratio:g} probablemente es incorrecto o cambió"
                )
                r.note = f"{r.note}; {warning}" if r.note else warning
        return ArbitrageResponse(
            rows=rows,
            ccl_market=ccl,
            ccl_median_implied=median,
            cedear_ratios=[c.model_dump() for c in cedears],
            computed_at=datetime.now(UTC),
        )

    async def _ccl_market(self) -> float | None:
        try:
            quotes = await self._ar.dollar_quotes()
        except SourceError:
            return None
        q = next((q for q in quotes if q.house == "contadoconliqui"), None)
        return q.sell if q else None
