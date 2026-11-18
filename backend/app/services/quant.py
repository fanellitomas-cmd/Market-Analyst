"""Matemática del análisis relativo contra un benchmark (beta, correlación, volatilidad...).

Funciones puras y sin dependencias, para que cada fórmula se pueda auditar y testear a mano.
Reglas:
  - Las series se **alinean por fecha** (sólo días en que ambos cotizaron). No se interpola: los
    días que sólo tiene una de las dos se descartan y se informan en `dropped_dates`.
  - Por debajo de `MIN_OBSERVATIONS` rendimientos, las métricas estadísticas quedan
    `NO_DISPONIBLE` en vez de devolver un número poco confiable.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

TRADING_DAYS = 252
MIN_OBSERVATIONS = 60
ROLLING_WINDOW = 63  # ~3 meses de ruedas


@dataclass(frozen=True)
class AlignedPrices:
    dates: list[date]
    asset: list[float]
    benchmark: list[float]
    dropped_dates: int


def align(
    asset_dates: Sequence[date],
    asset_closes: Sequence[float],
    bench_dates: Sequence[date],
    bench_closes: Sequence[float],
) -> AlignedPrices:
    asset_map = dict(zip(asset_dates, asset_closes, strict=True))
    bench_map = dict(zip(bench_dates, bench_closes, strict=True))
    common = sorted(asset_map.keys() & bench_map.keys())
    dropped = len(asset_map.keys() ^ bench_map.keys())
    return AlignedPrices(
        dates=common,
        asset=[asset_map[d] for d in common],
        benchmark=[bench_map[d] for d in common],
        dropped_dates=dropped,
    )


def simple_returns(prices: Sequence[float]) -> list[float]:
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


def mean(xs: Sequence[float]) -> float:
    return math.fsum(xs) / len(xs)


def covariance(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Covarianza muestral (n - 1)."""
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("se necesitan al menos 2 pares de observaciones")
    mx, my = mean(xs), mean(ys)
    return math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (len(xs) - 1)


def variance(xs: Sequence[float]) -> float:
    return covariance(xs, xs)


def beta(asset_returns: Sequence[float], bench_returns: Sequence[float]) -> float | None:
    """Sensibilidad del activo al benchmark: cov(a, b) / var(b). None si el benchmark no varía."""
    var_b = variance(bench_returns)
    if var_b == 0:
        return None
    return covariance(asset_returns, bench_returns) / var_b


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Correlación de Pearson. None si alguna serie es constante (indefinida)."""
    var_x, var_y = variance(xs), variance(ys)
    if var_x == 0 or var_y == 0:
        return None
    r = covariance(xs, ys) / math.sqrt(var_x * var_y)
    return max(-1.0, min(1.0, r))  # recorta el error de redondeo


def annualized_volatility(returns: Sequence[float]) -> float:
    return math.sqrt(variance(returns)) * math.sqrt(TRADING_DAYS)


def total_return(prices: Sequence[float]) -> float:
    return prices[-1] / prices[0] - 1.0


def max_drawdown(prices: Sequence[float]) -> float:
    """Peor caída desde un máximo previo, como fracción negativa (ej. -0.35 = -35 %)."""
    peak = prices[0]
    worst = 0.0
    for p in prices:
        peak = max(peak, p)
        worst = min(worst, p / peak - 1.0)
    return worst


@dataclass(frozen=True)
class RollingPoint:
    date: date
    correlation: float | None
    beta: float | None


def rolling_stats(
    dates: Sequence[date],
    asset_returns: Sequence[float],
    bench_returns: Sequence[float],
    window: int = ROLLING_WINDOW,
) -> list[RollingPoint]:
    """Correlación y beta móviles. `dates` corresponde a los precios (uno más que los retornos)."""
    points: list[RollingPoint] = []
    for end in range(window, len(asset_returns) + 1):
        a = asset_returns[end - window : end]
        b = bench_returns[end - window : end]
        # El retorno i va del precio i al i+1: su fecha es dates[i + 1].
        points.append(RollingPoint(date=dates[end], correlation=correlation(a, b), beta=beta(a, b)))
    return points


def rebase(prices: Sequence[float], base: float = 100.0) -> list[float]:
    first = prices[0]
    return [p / first * base for p in prices]
