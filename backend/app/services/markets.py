"""Análisis relativo de activos contra un benchmark (por defecto SPY, el ETF del S&P 500)."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from app.core.cache import TTLCache
from app.domain import Metric, SourceInfo
from app.schemas import (
    CompareResponse,
    CompareRow,
    PricePoint,
    PriceRange,
    RelativeAnalysis,
    RelativeMetrics,
    RollingPointOut,
)
from app.services import quant
from app.sources.errors import NotFoundError, SourceError
from app.sources.yahoo import SOURCE, BarInterval, Bars, PriceSeries, YahooClient

logger = logging.getLogger(__name__)

MAX_COMPARE_SYMBOLS = 15
_COMPARE_CONCURRENCY = 4


class MarketsService:
    def __init__(self, yahoo: YahooClient, cache: TTLCache[PriceSeries]) -> None:
        self._yahoo = yahoo
        self._cache = cache
        # Las velas intradía cambian minuto a minuto: caché corta (60 s) sólo para no repetir
        # el mismo pedido cuando varias pantallas o tareas lo piden a la vez.
        self._bars_cache: TTLCache[Bars] = TTLCache(60, max_entries=128)

    async def get_bars(self, symbol: str, interval: BarInterval, bar_range: str) -> Bars:
        return await self._bars_cache.get_or_load(
            f"{symbol}:{interval}:{bar_range}",
            lambda: self._yahoo.get_bars(symbol, interval, bar_range),
        )

    async def get_prices(self, symbol: str, price_range: PriceRange) -> PriceSeries:
        return await self._cache.get_or_load(
            f"{symbol}:{price_range}",
            lambda: self._yahoo.get_daily_prices(symbol, price_range),
        )

    def _source(self, symbol: str) -> SourceInfo:
        return SourceInfo(
            name=SOURCE, url=self._yahoo.chart_url(symbol), fetched_at=datetime.now(UTC)
        )

    async def relative(
        self, symbol: str, benchmark: str, price_range: PriceRange
    ) -> RelativeAnalysis:
        asset, bench = await asyncio.gather(
            self.get_prices(symbol, price_range), self.get_prices(benchmark, price_range)
        )
        aligned = quant.align(asset.dates, asset.closes, bench.dates, bench.closes)
        metrics, observations = compute_metrics(aligned)

        rolling: list[RollingPointOut] = []
        if observations >= quant.ROLLING_WINDOW:
            ra = quant.simple_returns(aligned.asset)
            rb = quant.simple_returns(aligned.benchmark)
            rolling = [
                RollingPointOut(date=p.date, correlation=p.correlation, beta=p.beta)
                for p in quant.rolling_stats(aligned.dates, ra, rb)
            ]

        rebased: list[PricePoint] = []
        if aligned.dates:
            rebased = [
                PricePoint(date=d, asset=a, benchmark=b)
                for d, a, b in zip(
                    aligned.dates,
                    quant.rebase(aligned.asset),
                    quant.rebase(aligned.benchmark),
                    strict=True,
                )
            ]

        return RelativeAnalysis(
            symbol=symbol,
            name=asset.name,
            currency=asset.currency,
            benchmark=benchmark,
            benchmark_name=bench.name,
            range=price_range,
            start_date=aligned.dates[0] if aligned.dates else None,
            end_date=aligned.dates[-1] if aligned.dates else None,
            observations=observations,
            dropped_dates=aligned.dropped_dates,
            adjusted_prices=asset.adjusted and bench.adjusted,
            metrics=metrics,
            rolling_window=quant.ROLLING_WINDOW,
            rolling=rolling,
            rebased_prices=rebased,
            source=self._source(symbol),
            computed_at=datetime.now(UTC),
        )

    async def compare(
        self, symbols: list[str], benchmark: str, price_range: PriceRange
    ) -> CompareResponse:
        bench = await self.get_prices(benchmark, price_range)
        semaphore = asyncio.Semaphore(_COMPARE_CONCURRENCY)

        async def row_for(symbol: str) -> CompareRow:
            async with semaphore:
                try:
                    asset = await self.get_prices(symbol, price_range)
                except NotFoundError as exc:
                    return _error_row(symbol, "NO_DISPONIBLE", exc.message)
                except SourceError as exc:
                    logger.warning("compare: falló %s: %s", symbol, exc)
                    return _error_row(symbol, "ERROR_FUENTE", exc.message)
            aligned = quant.align(asset.dates, asset.closes, bench.dates, bench.closes)
            metrics, observations = compute_metrics(aligned)
            enough = observations >= quant.MIN_OBSERVATIONS
            return CompareRow(
                symbol=symbol,
                name=asset.name,
                status="OK" if enough else "NO_DISPONIBLE",
                note=None if enough else f"sólo {observations} ruedas en común con {benchmark}",
                observations=observations,
                metrics=metrics,
            )

        rows = await asyncio.gather(*(row_for(s) for s in symbols))
        return CompareResponse(
            benchmark=benchmark,
            range=price_range,
            rows=list(rows),
            source=self._source(benchmark),
            computed_at=datetime.now(UTC),
        )


def _error_row(
    symbol: str, status: Literal["NO_DISPONIBLE", "ERROR_FUENTE"], note: str
) -> CompareRow:
    return CompareRow(
        symbol=symbol,
        name=None,
        status=status,
        note=note,
        observations=0,
        metrics=None,
    )


def compute_metrics(aligned: quant.AlignedPrices) -> tuple[RelativeMetrics, int]:
    """Calcula las métricas sobre precios ya alineados. Devuelve también la cantidad de
    rendimientos usados."""
    n_prices = len(aligned.dates)
    observations = max(n_prices - 1, 0)

    if n_prices < 2:
        none = Metric.missing("no hay suficientes precios en común")
        return RelativeMetrics(**{f: none for f in RelativeMetrics.model_fields}), 0

    # El rendimiento total y el drawdown sólo necesitan dos precios: son hechos, no estimaciones.
    asset_return = Metric.ok(quant.total_return(aligned.asset))
    bench_return = Metric.ok(quant.total_return(aligned.benchmark))
    asset_dd = Metric.ok(quant.max_drawdown(aligned.asset))
    bench_dd = Metric.ok(quant.max_drawdown(aligned.benchmark))

    if observations < quant.MIN_OBSERVATIONS:
        few = Metric.missing(
            f"se necesitan al menos {quant.MIN_OBSERVATIONS} ruedas en común (hay {observations})"
        )
        return (
            RelativeMetrics(
                beta=few,
                correlation=few,
                r_squared=few,
                asset_return=asset_return,
                benchmark_return=bench_return,
                asset_volatility=few,
                benchmark_volatility=few,
                asset_max_drawdown=asset_dd,
                benchmark_max_drawdown=bench_dd,
            ),
            observations,
        )

    ra = quant.simple_returns(aligned.asset)
    rb = quant.simple_returns(aligned.benchmark)
    b = quant.beta(ra, rb)
    r = quant.correlation(ra, rb)
    constant = Metric.missing("una de las series no tuvo variación en el período")
    return (
        RelativeMetrics(
            beta=Metric.ok(b) if b is not None else constant,
            correlation=Metric.ok(r) if r is not None else constant,
            r_squared=Metric.ok(r * r) if r is not None else constant,
            asset_return=asset_return,
            benchmark_return=bench_return,
            asset_volatility=Metric.ok(quant.annualized_volatility(ra)),
            benchmark_volatility=Metric.ok(quant.annualized_volatility(rb)),
            asset_max_drawdown=asset_dd,
            benchmark_max_drawdown=bench_dd,
        ),
        observations,
    )
