"""Macro de EE.UU. con series de FRED: tasas, curva de rendimientos, inflación, empleo, actividad
y riesgo.

Transformaciones (todas exactas, sin interpolar):
  - `level`: el valor tal como lo publica FRED.
  - `yoy_pct`: variación interanual de un índice mensual: valor del mes / valor del mismo mes del
    año anterior − 1. Si falta ese mes del año anterior, la observación se descarta.
  - `diff`: cambio contra el mes anterior (ej. empleos creados). Si falta el mes anterior, se
    descarta.
Las observaciones descartadas se informan en `dropped_points`.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from app.core.cache import TTLCache
from app.domain import DataStatus, SourceInfo, StrictModel
from app.sources.errors import NotFoundError, SourceError
from app.sources.fred import SOURCE, FredClient, FredSeries

logger = logging.getLogger(__name__)

Transform = Literal["level", "yoy_pct", "diff"]
# Desde cuándo se descarga cada serie (una sola vez por TTL; los rangos se recortan acá).
HISTORY_START = date(1999, 1, 1)
_FETCH_CONCURRENCY = 6


@dataclass(frozen=True)
class UsIndicatorSpec:
    key: str
    series_id: str
    name: str
    unit: str
    frequency: str
    category: str
    description: str
    transform: Transform = "level"


INDICATORS: list[UsIndicatorSpec] = [
    # --- Tasas y política monetaria ---
    UsIndicatorSpec(
        "fed_funds",
        "FEDFUNDS",
        "Tasa de fondos federales",
        "%",
        "Mensual",
        "Tasas",
        "Tasa efectiva a la que se prestan los bancos a un día; la "
        "herramienta principal de la Fed.",
    ),
    UsIndicatorSpec(
        "t3m",
        "DGS3MO",
        "Letra del Tesoro a 3 meses",
        "%",
        "Diaria",
        "Tasas",
        "Rendimiento a vencimiento constante.",
    ),
    UsIndicatorSpec(
        "t2y",
        "DGS2",
        "Bono del Tesoro a 2 años",
        "%",
        "Diaria",
        "Tasas",
        "Muy sensible a lo que el mercado espera de la Fed.",
    ),
    UsIndicatorSpec(
        "t10y",
        "DGS10",
        "Bono del Tesoro a 10 años",
        "%",
        "Diaria",
        "Tasas",
        "Referencia para hipotecas y para valuar acciones.",
    ),
    UsIndicatorSpec(
        "t30y",
        "DGS30",
        "Bono del Tesoro a 30 años",
        "%",
        "Diaria",
        "Tasas",
        "Rendimiento a vencimiento constante.",
    ),
    UsIndicatorSpec(
        "spread_10y2y",
        "T10Y2Y",
        "Pendiente 10 años − 2 años",
        "p.p.",
        "Diaria",
        "Tasas",
        "Negativa = curva invertida, históricamente previa a recesiones.",
    ),
    UsIndicatorSpec(
        "spread_10y3m",
        "T10Y3M",
        "Pendiente 10 años − 3 meses",
        "p.p.",
        "Diaria",
        "Tasas",
        "La medida de inversión de curva que sigue la Fed de Nueva York.",
    ),
    UsIndicatorSpec(
        "mortgage_30y",
        "MORTGAGE30US",
        "Hipoteca a 30 años",
        "%",
        "Semanal",
        "Tasas",
        "Tasa fija promedio (encuesta de Freddie Mac).",
    ),
    # --- Inflación ---
    UsIndicatorSpec(
        "cpi_yoy",
        "CPIAUCSL",
        "Inflación (IPC)",
        "% interanual",
        "Mensual",
        "Inflación",
        "Índice de precios al consumidor, todos los ítems.",
        "yoy_pct",
    ),
    UsIndicatorSpec(
        "core_cpi_yoy",
        "CPILFESL",
        "Inflación núcleo (IPC)",
        "% interanual",
        "Mensual",
        "Inflación",
        "IPC sin alimentos ni energía.",
        "yoy_pct",
    ),
    UsIndicatorSpec(
        "core_pce_yoy",
        "PCEPILFE",
        "Inflación núcleo PCE",
        "% interanual",
        "Mensual",
        "Inflación",
        "Deflactor del consumo sin alimentos ni energía: la medida que mira la Fed "
        "para su meta del 2 %.",
        "yoy_pct",
    ),
    UsIndicatorSpec(
        "breakeven_5y",
        "T5YIE",
        "Inflación esperada a 5 años",
        "%",
        "Diaria",
        "Inflación",
        "Implícita en bonos indexados (breakeven).",
    ),
    # --- Empleo y actividad ---
    UsIndicatorSpec(
        "unemployment",
        "UNRATE",
        "Desempleo",
        "%",
        "Mensual",
        "Empleo y actividad",
        "Tasa de desocupación.",
    ),
    UsIndicatorSpec(
        "payrolls",
        "PAYEMS",
        "Empleos creados (no agrícolas)",
        "miles",
        "Mensual",
        "Empleo y actividad",
        "Variación mensual de los puestos de trabajo no agrícolas (nóminas).",
        "diff",
    ),
    UsIndicatorSpec(
        "jobless_claims",
        "ICSA",
        "Pedidos de seguro de desempleo",
        "personas",
        "Semanal",
        "Empleo y actividad",
        "Solicitudes iniciales, desestacionalizadas.",
    ),
    UsIndicatorSpec(
        "gdp_growth",
        "A191RL1Q225SBEA",
        "Crecimiento del PBI real",
        "% anualizado",
        "Trimestral",
        "Empleo y actividad",
        "Variación del trimestre contra el anterior, anualizada.",
    ),
    # --- Riesgo y mercados ---
    UsIndicatorSpec(
        "vix",
        "VIXCLS",
        "VIX",
        "puntos",
        "Diaria",
        "Riesgo y mercados",
        "Volatilidad implícita del S&P 500 a 30 días: el 'índice del miedo'.",
    ),
    UsIndicatorSpec(
        "hy_spread",
        "BAMLH0A0HYM2",
        "Spread de bonos high yield",
        "p.p.",
        "Diaria",
        "Riesgo y mercados",
        "Prima que pagan los bonos corporativos de alto riesgo sobre los del Tesoro.",
    ),
    UsIndicatorSpec(
        "dollar",
        "DTWEXBGS",
        "Dólar (índice amplio)",
        "índice",
        "Diaria",
        "Riesgo y mercados",
        "Valor del dólar contra una canasta amplia de monedas (ene 2006 = 100).",
    ),
    UsIndicatorSpec(
        "wti",
        "DCOILWTICO",
        "Petróleo WTI",
        "USD/barril",
        "Diaria",
        "Riesgo y mercados",
        "Precio spot en Cushing, Oklahoma.",
    ),
]
INDICATOR_BY_KEY = {i.key: i for i in INDICATORS}

# Curva de rendimientos del Tesoro (vencimiento constante).
YIELD_CURVE: list[tuple[str, str]] = [
    ("1M", "DGS1MO"),
    ("3M", "DGS3MO"),
    ("6M", "DGS6MO"),
    ("1A", "DGS1"),
    ("2A", "DGS2"),
    ("3A", "DGS3"),
    ("5A", "DGS5"),
    ("7A", "DGS7"),
    ("10A", "DGS10"),
    ("20A", "DGS20"),
    ("30A", "DGS30"),
]
# Una tasa de más de una semana antes de la fecha buscada no representa ese día.
_MAX_CURVE_LAG = timedelta(days=7)


# ---------- Modelos de respuesta ----------


class UsIndicatorInfo(StrictModel):
    key: str
    series_id: str
    name: str
    unit: str
    frequency: str
    category: str
    description: str
    transform: Transform


class DatedValue(StrictModel):
    date: date
    value: float


class UsLatest(StrictModel):
    indicator: UsIndicatorInfo
    status: DataStatus
    latest: DatedValue | None
    previous: DatedValue | None
    change: float | None  # latest − previous, en la unidad del indicador
    year_ago: DatedValue | None
    note: str | None = None


class UsDashboardResponse(StrictModel):
    items: list[UsLatest]
    source: SourceInfo


class UsSeriesResponse(StrictModel):
    indicator: UsIndicatorInfo
    start: date
    observations: list[DatedValue]
    missing_points: int  # días sin dato en la fuente
    dropped_points: int  # observaciones descartadas por la transformación
    source: SourceInfo


class CurvePoint(StrictModel):
    maturity: str
    series_id: str
    value: float | None
    date: date | None
    status: DataStatus


class Curve(StrictModel):
    label: str
    target_date: date
    points: list[CurvePoint]


class YieldCurveResponse(StrictModel):
    curves: list[Curve]
    source: SourceInfo


def info(spec: UsIndicatorSpec) -> UsIndicatorInfo:
    return UsIndicatorInfo(
        key=spec.key,
        series_id=spec.series_id,
        name=spec.name,
        unit=spec.unit,
        frequency=spec.frequency,
        category=spec.category,
        description=spec.description,
        transform=spec.transform,
    )


# ---------- Transformaciones ----------


def _shift_month(d: date, months: int) -> tuple[int, int]:
    total = d.year * 12 + (d.month - 1) + months
    return total // 12, total % 12 + 1


def transform_series(
    dates: Sequence[date], values: Sequence[float], transform: Transform
) -> tuple[list[DatedValue], list[date]]:
    """Devuelve la serie transformada y las fechas que se descartaron por falta de base."""
    if transform == "level":
        return [DatedValue(date=d, value=v) for d, v in zip(dates, values, strict=True)], []

    by_month = {(d.year, d.month): v for d, v in zip(dates, values, strict=True)}
    out: list[DatedValue] = []
    dropped: list[date] = []
    lag = -12 if transform == "yoy_pct" else -1
    for d, v in zip(dates, values, strict=True):
        base = by_month.get(_shift_month(d, lag))
        if base is None or (transform == "yoy_pct" and base == 0):
            dropped.append(d)
            continue
        value = (v / base - 1.0) * 100.0 if transform == "yoy_pct" else v - base
        out.append(DatedValue(date=d, value=value))
    return out, dropped


def value_on_or_before(series: FredSeries, target: date) -> DatedValue | None:
    """Última observación publicada en `target` o antes (sin interpolar)."""
    lo, hi = 0, len(series.dates)
    while lo < hi:  # búsqueda binaria: primer índice con fecha > target
        mid = (lo + hi) // 2
        if series.dates[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    return DatedValue(date=series.dates[lo - 1], value=series.values[lo - 1])


# ---------- Servicio ----------


class UsMacroService:
    def __init__(self, client: FredClient, cache: TTLCache[FredSeries]) -> None:
        self._client = client
        self._cache = cache
        self._semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def _get(self, series_id: str) -> FredSeries:
        async def load() -> FredSeries:
            async with self._semaphore:
                return await self._client.get_series(series_id, HISTORY_START)

        return await self._cache.get_or_load(series_id, load)

    async def raw(self, series_id: str) -> FredSeries:
        """Serie de FRED completa (desde 1999), con la misma caché que usa la página EE.UU."""
        return await self._get(series_id)

    def _source(self, url: str) -> SourceInfo:
        return SourceInfo(name=SOURCE, url=url, fetched_at=datetime.now(UTC))

    async def series(self, key: str, start: date) -> UsSeriesResponse:
        spec = INDICATOR_BY_KEY[key]
        raw = await self._get(spec.series_id)
        transformed, dropped = transform_series(raw.dates, raw.values, spec.transform)
        in_range = [o for o in transformed if o.date >= start]
        return UsSeriesResponse(
            indicator=info(spec),
            start=start,
            observations=in_range,
            missing_points=raw.missing_points,
            dropped_points=sum(1 for d in dropped if d >= start),
            source=self._source(self._client.series_page_url(spec.series_id)),
        )

    async def dashboard(self) -> UsDashboardResponse:
        async def item(spec: UsIndicatorSpec) -> UsLatest:
            try:
                raw = await self._get(spec.series_id)
            except (SourceError, ValueError) as exc:
                logger.warning("FRED: falló %s: %s", spec.series_id, exc)
                status: DataStatus = (
                    "NO_DISPONIBLE" if isinstance(exc, NotFoundError) else "ERROR_FUENTE"
                )
                return UsLatest(
                    indicator=info(spec),
                    status=status,
                    latest=None,
                    previous=None,
                    change=None,
                    year_ago=None,
                    note=str(exc),
                )
            obs, _ = transform_series(raw.dates, raw.values, spec.transform)
            if not obs:
                return UsLatest(
                    indicator=info(spec),
                    status="NO_DISPONIBLE",
                    latest=None,
                    previous=None,
                    change=None,
                    year_ago=None,
                    note="La serie no tiene observaciones.",
                )
            latest = obs[-1]
            previous = obs[-2] if len(obs) > 1 else None
            year_ago = _on_or_before(obs, latest.date - timedelta(days=365))
            return UsLatest(
                indicator=info(spec),
                status="OK",
                latest=latest,
                previous=previous,
                change=latest.value - previous.value if previous else None,
                year_ago=year_ago,
            )

        items = await asyncio.gather(*(item(s) for s in INDICATORS))
        return UsDashboardResponse(
            items=list(items), source=self._source("https://fred.stlouisfed.org/")
        )

    async def yield_curve(self) -> YieldCurveResponse:
        results = await asyncio.gather(
            *(self._get(sid) for _, sid in YIELD_CURVE), return_exceptions=True
        )
        series: dict[str, FredSeries | None] = {}
        for (_, sid), res in zip(YIELD_CURVE, results, strict=True):
            if isinstance(res, FredSeries):
                series[sid] = res
            elif isinstance(res, SourceError | ValueError):
                logger.warning("FRED curva: falló %s: %s", sid, res)
                series[sid] = None
            else:
                raise res

        last_dates = [s.dates[-1] for s in series.values() if s is not None and s.dates]
        if not last_dates:
            raise SourceError(SOURCE, "no se pudo obtener ningún plazo de la curva")
        today = max(last_dates)
        targets = [
            ("Último dato", today),
            ("Hace 1 mes", today - timedelta(days=30)),
            ("Hace 1 año", today - timedelta(days=365)),
        ]

        curves: list[Curve] = []
        for label, target in targets:
            points: list[CurvePoint] = []
            for maturity, sid in YIELD_CURVE:
                s = series[sid]
                found = value_on_or_before(s, target) if s is not None else None
                if found is None or target - found.date > _MAX_CURVE_LAG:
                    points.append(
                        CurvePoint(
                            maturity=maturity,
                            series_id=sid,
                            value=None,
                            date=None,
                            status="ERROR_FUENTE" if s is None else "NO_DISPONIBLE",
                        )
                    )
                else:
                    points.append(
                        CurvePoint(
                            maturity=maturity,
                            series_id=sid,
                            value=found.value,
                            date=found.date,
                            status="OK",
                        )
                    )
            curves.append(Curve(label=label, target_date=target, points=points))

        return YieldCurveResponse(
            curves=curves,
            source=self._source("https://fred.stlouisfed.org/categories/115"),
        )


def _on_or_before(obs: Sequence[DatedValue], target: date) -> DatedValue | None:
    found: DatedValue | None = None
    for o in obs:
        if o.date > target:
            break
        found = o
    return found
