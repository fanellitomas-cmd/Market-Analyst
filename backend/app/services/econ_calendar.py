"""Calendario económico y sorpresas frente al consenso.

Flujo:
  1. `refresh()` (tarea diaria): guarda los eventos de la semana de ForexFactory con su
     pronóstico. Si el pronóstico cambia antes de la publicación, se actualiza.
  2. `capture_actuals()` (tarea varias veces por día hábil): para cada dato de EE.UU. ya publicado
     y mapeado a una serie de FRED, calcula el dato real del mes de referencia y la sorpresa
     (real − pronóstico). El dato se guarda la primera vez que aparece: así se acerca a la
     primera publicación y no a revisiones posteriores.

El dato real se calcula desde la serie oficial en FRED (por ejemplo, la variación mensual del
IPC sale del índice). Puede diferir en ±0,1 del redondeo que publica el organismo.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import EconEvent, utcnow
from app.domain import StrictModel
from app.sources.forexfactory import CalendarItem, ForexFactoryClient, parse_value
from app.sources.fred import FredClient, FredSeries

logger = logging.getLogger(__name__)

Transform = Literal["mom_pct", "yoy_pct", "diff", "level"]


@dataclass(frozen=True)
class EventSpec:
    key: str
    titles: tuple[str, ...]  # títulos exactos en ForexFactory
    name: str
    series_id: str
    transform: Transform
    unit: str  # "%" o "K" (como lo expresa ForexFactory)
    decimals: int
    threshold: float  # desvío mínimo para avisar
    higher_means: str  # qué significa un dato más alto que lo esperado


EVENT_SPECS: list[EventSpec] = [
    EventSpec(
        "cpi_mom",
        ("CPI m/m",),
        "IPC mensual",
        "CPIAUCSL",
        "mom_pct",
        "%",
        1,
        0.1,
        "más inflación de lo esperado",
    ),
    EventSpec(
        "core_cpi_mom",
        ("Core CPI m/m",),
        "IPC núcleo mensual",
        "CPILFESL",
        "mom_pct",
        "%",
        1,
        0.1,
        "más inflación de lo esperado",
    ),
    EventSpec(
        "cpi_yoy",
        ("CPI y/y",),
        "IPC interanual",
        "CPIAUCSL",
        "yoy_pct",
        "%",
        1,
        0.1,
        "más inflación de lo esperado",
    ),
    EventSpec(
        "ppi_mom",
        ("PPI m/m",),
        "Precios mayoristas (PPI) mensual",
        "PPIFIS",
        "mom_pct",
        "%",
        1,
        0.2,
        "más presión de costos",
    ),
    EventSpec(
        "core_ppi_mom",
        ("Core PPI m/m",),
        "PPI núcleo mensual",
        "PPIFES",
        "mom_pct",
        "%",
        1,
        0.2,
        "más presión de costos",
    ),
    EventSpec(
        "core_pce_mom",
        ("Core PCE Price Index m/m",),
        "PCE núcleo mensual",
        "PCEPILFE",
        "mom_pct",
        "%",
        1,
        0.1,
        "más inflación de lo esperado",
    ),
    EventSpec(
        "nfp",
        ("Non-Farm Employment Change",),
        "Empleos no agrícolas (NFP)",
        "PAYEMS",
        "diff",
        "K",
        0,
        50,
        "mercado laboral más fuerte de lo esperado",
    ),
    EventSpec(
        "unemployment",
        ("Unemployment Rate",),
        "Desempleo",
        "UNRATE",
        "level",
        "%",
        1,
        0.1,
        "mercado laboral más débil de lo esperado",
    ),
    EventSpec(
        "retail_mom",
        ("Retail Sales m/m",),
        "Ventas minoristas mensual",
        "RSAFS",
        "mom_pct",
        "%",
        1,
        0.3,
        "consumo más fuerte de lo esperado",
    ),
]
SPEC_BY_TITLE = {t: s for s in EVENT_SPECS for t in s.titles}
SPEC_BY_KEY = {s.key: s for s in EVENT_SPECS}


def reference_month(release_at: datetime) -> date:
    """Estos datos se publican el mes siguiente al que miden (el IPC de octubre sale en
    noviembre). FRED fecha cada observación el día 1 del mes medido."""
    d = release_at.astimezone(UTC).date()
    year, month = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return date(year, month, 1)


def compute_actual(series: FredSeries, spec: EventSpec, ref: date) -> float | None:
    values = dict(zip(series.dates, series.values, strict=True))
    current = values.get(ref)
    if current is None:
        return None
    if spec.transform == "level":
        return round(current, spec.decimals)
    prev_month = date(ref.year - (ref.month == 1), 12 if ref.month == 1 else ref.month - 1, 1)
    base_date = date(ref.year - 1, ref.month, 1) if spec.transform == "yoy_pct" else prev_month
    base = values.get(base_date)
    if base is None or (spec.transform != "diff" and base == 0):
        return None
    if spec.transform == "diff":
        return round(current - base, spec.decimals)
    return round((current / base - 1.0) * 100, spec.decimals)


# ---------- Modelos de respuesta ----------


class EventOut(StrictModel):
    id: int
    title: str
    name: str  # en castellano si el evento está mapeado; si no, el título original
    country: str
    release_at: datetime
    impact: str
    forecast: str | None
    forecast_value: float | None
    previous: str | None
    actual: float | None
    actual_unit: str | None
    surprise: float | None
    surprise_note: str | None
    reference_month: date | None
    tracked: bool  # True = el agente calcula el dato real y la sorpresa


class CalendarResponse(StrictModel):
    upcoming: list[EventOut]
    recent: list[EventOut]
    last_refresh: datetime | None


@dataclass(frozen=True)
class Surprise:
    event: EconEvent
    spec: EventSpec

    def message(self) -> str:
        e, s = self.event, self.spec
        assert e.actual is not None and e.forecast_value is not None and e.surprise is not None
        unit = " mil" if s.unit == "K" else " %"
        sign = "+" if e.surprise > 0 else ""
        meaning = s.higher_means if e.surprise > 0 else _opposite(s.higher_means)
        ref = e.reference_month.strftime("%m/%Y") if e.reference_month else ""
        return (
            f"📊 {s.name} ({ref}): {_fmt(e.actual, s.decimals)}{unit} vs "
            f"{_fmt(e.forecast_value, s.decimals)}{unit} esperado → "
            f"{sign}{_fmt(e.surprise, s.decimals)}{' mil' if s.unit == 'K' else ' p.p.'} "
            f"({meaning})."
        )


def _fmt(v: float, decimals: int) -> str:
    return f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _opposite(text: str) -> str:
    swaps = [
        ("más fuerte", "más débil"),
        ("más débil", "más fuerte"),
        ("más inflación", "menos inflación"),
        ("más presión", "menos presión"),
    ]
    for a, b in swaps:
        if a in text:
            return text.replace(a, b)
    return f"lo contrario: {text}"


class EconCalendarService:
    def __init__(
        self,
        ff: ForexFactoryClient,
        fred: FredClient,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._ff = ff
        self._fred = fred
        self._sessions = sessions

    async def refresh(self) -> tuple[int, int]:
        """Guarda/actualiza los eventos de la semana. Devuelve (nuevos, actualizados)."""
        items = [i for i in await self._ff.this_week() if _relevant(i)]
        new = updated = 0
        async with self._sessions() as session:
            for item in items:
                existing = (
                    await session.execute(
                        select(EconEvent).where(
                            EconEvent.title == item.title,
                            EconEvent.country == item.country,
                            EconEvent.release_at == item.release_at.astimezone(UTC),
                        )
                    )
                ).scalar_one_or_none()
                spec = SPEC_BY_TITLE.get(item.title) if item.country == "USD" else None
                forecast = parse_value(item.forecast)
                if existing is None:
                    session.add(
                        EconEvent(
                            event_key=spec.key if spec else None,
                            title=item.title,
                            country=item.country,
                            release_at=item.release_at.astimezone(UTC),
                            impact=item.impact,
                            forecast_raw=item.forecast,
                            previous_raw=item.previous,
                            forecast_value=forecast[0] if forecast else None,
                            reference_month=reference_month(item.release_at) if spec else None,
                        )
                    )
                    new += 1
                elif existing.actual is None and (
                    existing.forecast_raw != item.forecast or existing.previous_raw != item.previous
                ):
                    # El consenso puede ajustarse durante la semana; después de publicado, no.
                    existing.forecast_raw = item.forecast
                    existing.previous_raw = item.previous
                    existing.forecast_value = forecast[0] if forecast else None
                    updated += 1
            await session.commit()
        return new, updated

    async def capture_actuals(self, now: datetime | None = None) -> list[Surprise]:
        """Completa el dato real de los eventos ya publicados. Devuelve las sorpresas que superan
        el umbral y todavía no se avisaron."""
        now = now or utcnow()
        async with self._sessions() as session:
            pending = (
                (
                    await session.execute(
                        select(EconEvent).where(
                            EconEvent.event_key.is_not(None),
                            EconEvent.actual.is_(None),
                            EconEvent.release_at <= now,
                            EconEvent.release_at >= now - timedelta(days=45),
                        )
                    )
                )
                .scalars()
                .all()
            )
            fetched: dict[str, FredSeries] = {}
            surprises: list[Surprise] = []
            for ev in pending:
                spec = SPEC_BY_KEY[ev.event_key or ""]
                if spec.series_id not in fetched:
                    # Sin caché: hay que ver el dato apenas FRED lo publica.
                    fetched[spec.series_id] = await self._fred.get_series(
                        spec.series_id, date.today() - timedelta(days=800)
                    )
                ref = ev.reference_month or reference_month(_aware(ev.release_at))
                actual = compute_actual(fetched[spec.series_id], spec, ref)
                if actual is None:
                    continue  # FRED todavía no lo publicó
                ev.actual = actual
                ev.actual_captured_at = now
                if ev.forecast_value is not None:
                    ev.surprise = round(actual - ev.forecast_value, spec.decimals)
                    if abs(ev.surprise) >= spec.threshold - 1e-9 and not ev.notified:
                        ev.notified = True
                        surprises.append(Surprise(ev, spec))
            await session.commit()
        return surprises

    async def calendar(self, now: datetime | None = None) -> CalendarResponse:
        now = now or utcnow()
        async with self._sessions() as session:
            upcoming = (
                (
                    await session.execute(
                        select(EconEvent)
                        .where(
                            EconEvent.release_at >= now,
                            EconEvent.release_at <= now + timedelta(days=8),
                        )
                        .order_by(EconEvent.release_at)
                    )
                )
                .scalars()
                .all()
            )
            recent = (
                (
                    await session.execute(
                        select(EconEvent)
                        .where(
                            EconEvent.release_at < now,
                            EconEvent.release_at >= now - timedelta(days=60),
                            EconEvent.event_key.is_not(None),
                        )
                        .order_by(EconEvent.release_at.desc())
                        .limit(40)
                    )
                )
                .scalars()
                .all()
            )
            last = (
                await session.execute(
                    select(EconEvent.created_at).order_by(EconEvent.created_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
        return CalendarResponse(
            upcoming=[to_out(e) for e in upcoming],
            recent=[to_out(e) for e in recent],
            last_refresh=_aware(last) if last else None,
        )


def _relevant(item: CalendarItem) -> bool:
    # EE.UU.: impacto alto y medio. Resto del mundo: sólo alto (para el pre-market).
    if item.country == "USD":
        return item.impact in ("High", "Medium") or item.title in SPEC_BY_TITLE
    return item.impact == "High"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_out(e: EconEvent) -> EventOut:
    spec = SPEC_BY_KEY.get(e.event_key or "")
    note = None
    if spec and e.surprise is not None:
        if abs(e.surprise) < spec.threshold - 1e-9:
            note = "en línea con lo esperado"
        else:
            note = spec.higher_means if e.surprise > 0 else _opposite(spec.higher_means)
    return EventOut(
        id=e.id,
        title=e.title,
        name=spec.name if spec else e.title,
        country=e.country,
        release_at=_aware(e.release_at),
        impact=e.impact,
        forecast=e.forecast_raw,
        forecast_value=e.forecast_value,
        previous=e.previous_raw,
        actual=e.actual,
        actual_unit=("mil" if spec.unit == "K" else "%") if spec else None,
        surprise=e.surprise,
        surprise_note=note,
        reference_month=e.reference_month,
        tracked=spec is not None,
    )
