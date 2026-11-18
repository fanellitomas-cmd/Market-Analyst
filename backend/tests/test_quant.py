from datetime import date, timedelta

import pytest

from app.services import quant
from app.services.markets import compute_metrics


def test_beta_and_correlation_of_scaled_returns() -> None:
    bench = [0.01, -0.02, 0.015, 0.0, -0.005, 0.02]
    asset = [2 * r for r in bench]
    assert quant.beta(asset, bench) == pytest.approx(2.0)
    assert quant.correlation(asset, bench) == pytest.approx(1.0)


def test_inverse_correlation() -> None:
    bench = [0.01, -0.02, 0.015, 0.003]
    asset = [-r for r in bench]
    assert quant.correlation(asset, bench) == pytest.approx(-1.0)
    assert quant.beta(asset, bench) == pytest.approx(-1.0)


def test_constant_benchmark_has_no_beta() -> None:
    assert quant.beta([0.01, 0.02, 0.03], [0.0, 0.0, 0.0]) is None
    assert quant.correlation([0.01, 0.02, 0.03], [0.0, 0.0, 0.0]) is None


def test_sample_variance_by_hand() -> None:
    # media 2.5, desvíos² = 2.25+0.25+0.25+2.25 = 5, / (4-1)
    assert quant.variance([1.0, 2.0, 3.0, 4.0]) == pytest.approx(5 / 3)


def test_max_drawdown_and_total_return() -> None:
    prices = [100.0, 120.0, 90.0, 110.0, 60.0, 130.0]
    assert quant.max_drawdown(prices) == pytest.approx(60 / 120 - 1)
    assert quant.total_return(prices) == pytest.approx(0.3)
    assert quant.max_drawdown([1.0, 2.0, 3.0]) == 0.0


def test_align_drops_non_common_dates_without_interpolating() -> None:
    d = [date(2024, 1, i) for i in range(1, 6)]
    aligned = quant.align(d, [1.0, 2.0, 3.0, 4.0, 5.0], [d[0], d[1], d[3]], [10.0, 20.0, 40.0])
    assert aligned.dates == [d[0], d[1], d[3]]
    assert aligned.asset == [1.0, 2.0, 4.0]
    assert aligned.benchmark == [10.0, 20.0, 40.0]
    assert aligned.dropped_dates == 2


def test_rolling_dates_match_last_return_of_each_window() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(10)]
    returns = [0.01 * ((-1) ** i) * (i + 1) for i in range(9)]
    points = quant.rolling_stats(dates, returns, returns, window=5)
    assert len(points) == 5  # 9 retornos, ventanas de 5
    assert points[0].date == dates[5]
    assert points[-1].date == dates[-1]
    assert points[0].correlation == pytest.approx(1.0)


def test_compute_metrics_marks_short_history_as_unavailable() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(20)]
    prices = [100.0 + i for i in range(20)]
    metrics, obs = compute_metrics(quant.AlignedPrices(dates, prices, prices, 0))
    assert obs == 19
    assert metrics.beta.status == "NO_DISPONIBLE"
    assert metrics.beta.value is None
    # El rendimiento del período sí es un hecho con dos precios.
    assert metrics.asset_return.status == "OK"
    assert metrics.asset_return.value == pytest.approx(19 / 100)
