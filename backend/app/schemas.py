"""Modelos de respuesta de la API (lo que consume el frontend)."""

from datetime import date, datetime
from typing import Literal

from app.domain import DataStatus, Metric, Observation, SourceInfo, StrictModel

# ---------- Auth ----------


class LoginRequest(StrictModel):
    username: str
    password: str


class SessionInfo(StrictModel):
    username: str


# ---------- Macro ----------


class IndicatorInfo(StrictModel):
    key: str
    name: str
    unit: str
    description: str
    source_id: str


class CountryInfo(StrictModel):
    code: str
    name: str
    group: str


class CountrySeriesOut(StrictModel):
    country_code: str
    country_name: str
    status: DataStatus
    observations: list[Observation]
    missing_periods: int
    note: str | None = None


class MacroSeriesResponse(StrictModel):
    indicator: IndicatorInfo
    start_year: int
    end_year: int
    series: list[CountrySeriesOut]
    source: SourceInfo


class LatestValue(StrictModel):
    value: float | None
    period: str | None
    status: DataStatus


class CountrySnapshot(StrictModel):
    country: CountryInfo
    values: dict[str, LatestValue]  # clave = IndicatorInfo.key


class MacroSnapshotResponse(StrictModel):
    indicators: list[IndicatorInfo]
    countries: list[CountrySnapshot]
    errors: dict[str, str]  # indicador -> motivo, si una consulta completa falló
    source: SourceInfo


# ---------- Mercados ----------

PriceRange = Literal["6mo", "1y", "2y", "5y"]


class RelativeMetrics(StrictModel):
    beta: Metric
    correlation: Metric
    r_squared: Metric
    asset_return: Metric
    benchmark_return: Metric
    asset_volatility: Metric
    benchmark_volatility: Metric
    asset_max_drawdown: Metric
    benchmark_max_drawdown: Metric


class PricePoint(StrictModel):
    date: date
    asset: float
    benchmark: float


class RollingPointOut(StrictModel):
    date: date
    correlation: float | None
    beta: float | None


class RelativeAnalysis(StrictModel):
    symbol: str
    name: str | None
    currency: str | None
    benchmark: str
    benchmark_name: str | None
    range: PriceRange
    start_date: date | None
    end_date: date | None
    observations: int  # rendimientos diarios usados
    dropped_dates: int  # días que cotizó sólo uno de los dos
    adjusted_prices: bool
    metrics: RelativeMetrics
    rolling_window: int
    rolling: list[RollingPointOut]
    rebased_prices: list[PricePoint]  # base 100 al inicio del período
    source: SourceInfo
    computed_at: datetime


class CompareRow(StrictModel):
    symbol: str
    name: str | None
    status: DataStatus
    note: str | None
    observations: int
    metrics: RelativeMetrics | None


class CompareResponse(StrictModel):
    benchmark: str
    range: PriceRange
    rows: list[CompareRow]
    source: SourceInfo
    computed_at: datetime
