"""Liquidez y condiciones financieras (Columna 1 de la visión).

Grupos:
  - Bancos centrales: activos totales de la Fed, el BCE y el BoJ (FRED). El PBoC no publica una
    serie equivalente en una fuente gratuita confiable, así que no se incluye.
  - Liquidez neta de la Fed = activos de la Fed − cuenta del Tesoro (TGA) − repos inversos (RRP).
    Es la medida más usada de cuánta liquidez del banco central queda disponible para el sistema.
  - Dólar: DXY (Yahoo) e índice amplio de la Fed (FRED).
  - Crédito: spreads high yield y grado de inversión (FRED) y el cociente de precios HYG/LQD.
  - Condiciones financieras: índice NFCI de la Fed de Chicago (negativo = más laxas que el
    promedio).
  - Costo de capital: tasa real a 10 años y cíclicos frente a defensivos (ETFs sectoriales).

Todas las series se alinean por fecha sin interpolar. Los cambios se calculan contra la última
observación publicada en la fecha de referencia o antes; si esa observación es demasiado vieja,
el cambio queda "no disponible".
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from app.core.cache import TTLCache
from app.domain import DataStatus, SourceInfo, StrictModel
from app.services.markets import MarketsService
from app.services.us_macro import UsMacroService
from app.sources.errors import NotFoundError, SourceError

logger = logging.getLogger(__name__)

Points = list[tuple[date, float]]
ChangeKind = Literal["pct", "abs"]

CYCLICALS = ("XLY", "XLI", "XLF", "XLB")
DEFENSIVES = ("XLP", "XLU", "XLV")
# (etiqueta, meses hacia atrás, máximo atraso aceptado de la observación base). Una base más
# vieja que eso no representa la fecha de referencia y el cambio queda "no disponible".
HORIZONS: list[tuple[str, int, timedelta]] = [
    ("1 mes", 1, timedelta(days=10)),
    ("3 meses", 3, timedelta(days=20)),
    ("1 año", 12, timedelta(days=40)),
]


def months_back(d: date, months: int) -> date:
    total = d.year * 12 + d.month - 1 - months
    year, month = divmod(total, 12)
    month += 1
    last_day = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day))


@dataclass(frozen=True)
class LiqSpec:
    key: str
    name: str
    group: str
    unit: str
    description: str
    change: ChangeKind
    source: str


SPECS: list[LiqSpec] = [
    LiqSpec(
        "fed_assets",
        "Activos de la Fed",
        "Bancos centrales",
        "miles de millones de USD",
        "Balance total de la Reserva Federal (semanal).",
        "pct",
        "FRED WALCL",
    ),
    LiqSpec(
        "ecb_assets",
        "Activos del BCE",
        "Bancos centrales",
        "miles de millones de EUR",
        "Balance total del Eurosistema (semanal).",
        "pct",
        "FRED ECBASSETSW",
    ),
    LiqSpec(
        "boj_assets",
        "Activos del BoJ",
        "Bancos centrales",
        "billones de JPY",
        "Balance total del Banco de Japón (mensual).",
        "pct",
        "FRED JPNASSETS",
    ),
    LiqSpec(
        "net_liquidity",
        "Liquidez neta de la Fed",
        "Bancos centrales",
        "miles de millones de USD",
        "Activos de la Fed − cuenta del Tesoro (TGA) − repos inversos (RRP). Sube = más "
        "liquidez disponible para los mercados.",
        "pct",
        "FRED WALCL, WTREGEN, RRPONTSYD",
    ),
    LiqSpec(
        "dxy",
        "DXY",
        "Dólar",
        "índice",
        "Dólar contra seis monedas principales (ICE), cierre diario.",
        "pct",
        "Yahoo DX-Y.NYB",
    ),
    LiqSpec(
        "dollar_broad",
        "Dólar amplio (Fed)",
        "Dólar",
        "índice",
        "Dólar contra una canasta amplia de socios comerciales.",
        "pct",
        "FRED DTWEXBGS",
    ),
    LiqSpec(
        "hy_spread",
        "Spread high yield",
        "Crédito",
        "p.p.",
        "Prima de los bonos corporativos de alto riesgo sobre el Tesoro.",
        "abs",
        "FRED BAMLH0A0HYM2",
    ),
    LiqSpec(
        "ig_spread",
        "Spread grado de inversión",
        "Crédito",
        "p.p.",
        "Prima de los bonos corporativos de grado de inversión sobre el Tesoro.",
        "abs",
        "FRED BAMLC0A0CM",
    ),
    LiqSpec(
        "hyg_lqd",
        "HYG / LQD",
        "Crédito",
        "cociente",
        "Precio del ETF high yield dividido por el de grado de inversión. Sube = el mercado "
        "toma más riesgo de crédito.",
        "pct",
        "Yahoo HYG, LQD",
    ),
    LiqSpec(
        "nfci",
        "Condiciones financieras (NFCI)",
        "Condiciones",
        "índice",
        "Índice de la Fed de Chicago. Negativo = condiciones más laxas que el promedio "
        "histórico; positivo = más restrictivas.",
        "abs",
        "FRED NFCI",
    ),
    LiqSpec(
        "real_10y",
        "Tasa real a 10 años",
        "Costo de capital",
        "%",
        "Rendimiento del bono del Tesoro indexado por inflación (TIPS). Es el piso del costo "
        "de capital: cuando sube, presiona los múltiplos de las acciones de crecimiento.",
        "abs",
        "FRED DFII10",
    ),
    LiqSpec(
        "cyc_def",
        "Cíclicos / defensivos",
        "Costo de capital",
        "índice (base 100)",
        "Promedio de XLY, XLI, XLF y XLB dividido por el de XLP, XLU y XLV, base 100 hace 5 "
        "años. Sube = el mercado apuesta a crecimiento económico.",
        "pct",
        "Yahoo, ETFs sectoriales SPDR",
    ),
]
SPEC_BY_KEY = {s.key: s for s in SPECS}

# Series simples de FRED: clave → (id, factor para llevar a la unidad mostrada)
_FRED_SIMPLE: dict[str, tuple[str, float]] = {
    "fed_assets": ("WALCL", 1e-3),  # millones → miles de millones
    "ecb_assets": ("ECBASSETSW", 1e-3),  # millones → miles de millones
    "boj_assets": ("JPNASSETS", 1e-4),  # 100 millones de yenes → billones (10^12)
    "dollar_broad": ("DTWEXBGS", 1.0),
    "hy_spread": ("BAMLH0A0HYM2", 1.0),
    "ig_spread": ("BAMLC0A0CM", 1.0),
    "nfci": ("NFCI", 1.0),
    "real_10y": ("DFII10", 1.0),
}


# ---------- Cálculos puros ----------


def on_or_before(points: Points, target: date) -> tuple[date, float] | None:
    lo, hi = 0, len(points)
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid][0] <= target:
            lo = mid + 1
        else:
            hi = mid
    return points[lo - 1] if lo else None


def net_liquidity(walcl: Points, tga: Points, rrp: Points) -> tuple[Points, int]:
    """WALCL (millones) − TGA (millones) − RRP (miles de millones), en miles de millones.

    Para cada miércoles de WALCL se toman TGA y RRP de ese día o del último hábil previo (hasta
    7 días). Si falta alguno, esa semana se omite y se cuenta.
    """
    out: Points = []
    skipped = 0
    for d, assets in walcl:
        t = on_or_before(tga, d)
        r = on_or_before(rrp, d)
        if t is None or r is None or (d - t[0]).days > 7 or (d - r[0]).days > 7:
            skipped += 1
            continue
        out.append((d, assets / 1000 - t[1] / 1000 - r[1]))
    return out, skipped


def ratio(a: Points, b: Points) -> Points:
    bm = dict(b)
    return [(d, v / bm[d]) for d, v in a if d in bm and bm[d] != 0]


def basket_ratio(numerator: list[Points], denominator: list[Points]) -> Points:
    """Promedio de los activos del numerador (cada uno en base 1) sobre el del denominador,
    sólo en fechas en que cotizaron todos; resultado en base 100."""
    maps = [dict(p) for p in numerator + denominator]
    common = sorted(set.intersection(*(set(m) for m in maps))) if maps else []
    if not common:
        return []
    first = common[0]
    n = len(numerator)
    out: Points = []
    for d in common:
        num = math.fsum(m[d] / m[first] for m in maps[:n]) / n
        den = math.fsum(m[d] / m[first] for m in maps[n:]) / (len(maps) - n)
        out.append((d, num / den))
    base = out[0][1]
    return [(d, v / base * 100) for d, v in out]


# ---------- Modelos ----------


class DatedValue(StrictModel):
    date: date
    value: float


class Change(StrictModel):
    label: str
    kind: ChangeKind
    value: float | None  # fracción si kind = pct; diferencia en la unidad si kind = abs
    base: DatedValue | None


class LiqIndicator(StrictModel):
    key: str
    name: str
    group: str
    unit: str
    description: str
    source: str
    status: DataStatus
    latest: DatedValue | None
    changes: list[Change]
    note: str | None = None


class LiquidityDashboard(StrictModel):
    indicators: list[LiqIndicator]
    computed_at: datetime


class LiquiditySeries(StrictModel):
    key: str
    name: str
    unit: str
    start: date
    points: list[DatedValue]
    note: str | None
    source: SourceInfo


def changes(points: Points, kind: ChangeKind) -> list[Change]:
    last_d, last_v = points[-1]
    out: list[Change] = []
    for label, months, max_lag in HORIZONS:
        target = months_back(last_d, months)
        base = on_or_before(points, target)
        if base is None or target - base[0] > max_lag:
            out.append(Change(label=label, kind=kind, value=None, base=None))
            continue
        if kind == "pct":
            value = last_v / base[1] - 1.0 if base[1] != 0 else None
        else:
            value = last_v - base[1]
        out.append(
            Change(
                label=label, kind=kind, value=value, base=DatedValue(date=base[0], value=base[1])
            )
        )
    return out


# ---------- Servicio ----------


class LiquidityService:
    def __init__(
        self,
        us: UsMacroService,
        markets: MarketsService,
        cache: TTLCache[tuple[Points, str | None]],
    ) -> None:
        self._us = us
        self._markets = markets
        self._cache = cache

    async def _fred(self, series_id: str, factor: float = 1.0) -> Points:
        s = await self._us.raw(series_id)
        return [(d, v * factor) for d, v in zip(s.dates, s.values, strict=True)]

    async def _yahoo(self, symbol: str) -> Points:
        s = await self._markets.get_prices(symbol, "5y")
        return list(zip(s.dates, s.closes, strict=True))

    async def _compute(self, key: str) -> tuple[Points, str | None]:
        if key in _FRED_SIMPLE:
            sid, factor = _FRED_SIMPLE[key]
            return await self._fred(sid, factor), None
        if key == "net_liquidity":
            walcl, tga, rrp = await asyncio.gather(
                self._fred("WALCL"), self._fred("WTREGEN"), self._fred("RRPONTSYD")
            )
            points, skipped = net_liquidity(walcl, tga, rrp)
            note = f"{skipped} semanas omitidas por falta de TGA o RRP" if skipped else None
            return points, note
        if key == "dxy":
            return await self._yahoo("DX-Y.NYB"), None
        if key == "hyg_lqd":
            hyg, lqd = await asyncio.gather(self._yahoo("HYG"), self._yahoo("LQD"))
            return ratio(hyg, lqd), "precios ajustados por dividendos"
        if key == "cyc_def":
            series = await asyncio.gather(*(self._yahoo(s) for s in CYCLICALS + DEFENSIVES))
            n = len(CYCLICALS)
            return basket_ratio(list(series[:n]), list(series[n:])), None
        raise KeyError(key)

    async def points(self, key: str) -> tuple[Points, str | None]:
        return await self._cache.get_or_load(key, lambda: self._compute(key))

    async def dashboard(self) -> LiquidityDashboard:
        async def one(spec: LiqSpec) -> LiqIndicator:
            base = {
                "key": spec.key,
                "name": spec.name,
                "group": spec.group,
                "unit": spec.unit,
                "description": spec.description,
                "source": spec.source,
            }
            try:
                pts, note = await self.points(spec.key)
            except (SourceError, ValueError) as exc:
                logger.warning("liquidez: falló %s: %s", spec.key, exc)
                status: DataStatus = (
                    "NO_DISPONIBLE" if isinstance(exc, NotFoundError) else "ERROR_FUENTE"
                )
                return LiqIndicator(**base, status=status, latest=None, changes=[], note=str(exc))
            if not pts:
                return LiqIndicator(
                    **base,
                    status="NO_DISPONIBLE",
                    latest=None,
                    changes=[],
                    note="sin observaciones",
                )
            d, v = pts[-1]
            return LiqIndicator(
                **base,
                status="OK",
                latest=DatedValue(date=d, value=v),
                changes=changes(pts, spec.change),
                note=note,
            )

        items = await asyncio.gather(*(one(s) for s in SPECS))
        return LiquidityDashboard(indicators=list(items), computed_at=datetime.now(UTC))

    async def series(self, key: str, start: date) -> LiquiditySeries:
        spec = SPEC_BY_KEY[key]
        pts, note = await self.points(key)
        return LiquiditySeries(
            key=key,
            name=spec.name,
            unit=spec.unit,
            start=start,
            points=[DatedValue(date=d, value=v) for d, v in pts if d >= start],
            note=note,
            source=SourceInfo(name=spec.source, url=_source_url(key), fetched_at=datetime.now(UTC)),
        )


def _source_url(key: str) -> str:
    if key in _FRED_SIMPLE:
        return f"https://fred.stlouisfed.org/series/{_FRED_SIMPLE[key][0]}"
    if key == "net_liquidity":
        return "https://fred.stlouisfed.org/series/WALCL"
    if key == "dxy":
        return "https://finance.yahoo.com/quote/DX-Y.NYB"
    if key == "hyg_lqd":
        return "https://finance.yahoo.com/quote/HYG"
    return "https://finance.yahoo.com/quote/XLY"
