"""Precios diarios desde el endpoint público de gráficos de Yahoo Finance (gratuito, sin clave).

Es un endpoint no oficial: puede cambiar o limitar pedidos sin aviso. Por eso todo error se
traduce a `SourceError` y el resto de la app lo muestra como "fuente no disponible" en lugar de
romperse o mostrar datos viejos como si fueran actuales.

Se usa el cierre **ajustado** (por dividendos y splits) para calcular rendimientos; si Yahoo no
lo trae, se cae al cierre simple y se deja constancia en `adjusted=False`.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.sources.errors import NotFoundError, SourceError

SOURCE = "Yahoo Finance"
PriceRange = Literal["6mo", "1y", "2y", "5y"]
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,15}$")
# Yahoo rechaza pedidos sin User-Agent; se usa uno genérico que identifica a la app.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; market-analyst/0.1)"}


@dataclass(frozen=True)
class PriceSeries:
    symbol: str
    currency: str | None
    name: str | None
    dates: list[date]
    closes: list[float]
    adjusted: bool
    missing_points: int


BarInterval = Literal["1d", "1h", "30m", "15m", "5m"]
# Límites de historia de Yahoo para cada intervalo (los intradía son cortos).
_BAR_RANGES: dict[str, tuple[str, ...]] = {
    "1d": ("3mo", "6mo", "1y", "2y", "5y", "10y"),
    "1h": ("5d", "1mo", "3mo", "6mo", "1y", "2y"),
    "30m": ("5d", "1mo", "60d"),
    "15m": ("5d", "1mo", "60d"),
    "5m": ("1d", "5d", "1mo", "60d"),
}


@dataclass(frozen=True)
class Bar:
    time: datetime  # inicio de la vela, en la zona horaria de la bolsa
    open: float
    high: float
    low: float
    close: float
    volume: float | None  # None = la fuente no informó volumen (no es cero)


@dataclass(frozen=True)
class Bars:
    symbol: str
    interval: str
    currency: str | None
    name: str | None
    exchange_timezone: str | None
    bars: list[Bar]
    adjusted: bool
    missing_points: int  # velas descartadas por datos faltantes o inconsistentes


def normalize_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError(f"símbolo inválido: {raw!r}")
    return symbol


class YahooClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def chart_url(self, symbol: str) -> str:
        return f"{self._base_url}/v8/finance/chart/{symbol}"

    async def _fetch_chart(self, symbol: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._client.get(
                self.chart_url(symbol), params=params, headers=_HEADERS
            )
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code} sin JSON") from exc

        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict):
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        error = chart.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", ""))
            description = str(error.get("description") or code or "error")
            if code == "Not Found" or response.status_code == 404:
                raise NotFoundError(SOURCE, f"no existe el símbolo {symbol}")
            raise SourceError(SOURCE, description)
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")

        results = chart.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise NotFoundError(SOURCE, f"sin datos para {symbol}")
        return results[0]

    async def get_daily_prices(self, symbol: str, price_range: PriceRange) -> PriceSeries:
        params = {"range": price_range, "interval": "1d", "events": "div,splits"}
        return _parse_result(symbol, await self._fetch_chart(symbol, params))

    async def get_bars(self, symbol: str, interval: BarInterval, bar_range: str) -> Bars:
        """Velas OHLCV. Diarias: ajustadas por dividendos y splits. Intradía: sin ajustar."""
        allowed = _BAR_RANGES[interval]
        if bar_range not in allowed:
            raise ValueError(f"rango {bar_range!r} no válido para velas de {interval}")
        params = {"range": bar_range, "interval": interval, "events": "div,splits"}
        return _parse_bars(symbol, interval, await self._fetch_chart(symbol, params))


def _parse_result(symbol: str, result: dict[str, Any]) -> PriceSeries:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    assert isinstance(meta, dict)
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        raise NotFoundError(SOURCE, f"sin precios para {symbol}")

    closes_raw = _first_list(indicators.get("adjclose"), "adjclose")
    adjusted = closes_raw is not None
    if closes_raw is None:
        closes_raw = _first_list(indicators.get("quote"), "close")
    if closes_raw is None or len(closes_raw) != len(timestamps):
        raise SourceError(SOURCE, f"serie de precios incompleta para {symbol}")

    gmtoffset = meta.get("gmtoffset")
    offset = timedelta(seconds=gmtoffset if isinstance(gmtoffset, int) else 0)

    by_date: dict[date, float] = {}
    missing = 0
    for ts, close in zip(timestamps, closes_raw, strict=True):
        valid_close = isinstance(close, int | float) and not isinstance(close, bool) and close > 0
        if not isinstance(ts, int) or not valid_close:
            missing += 1
            continue
        # La fecha de la rueda es la de la bolsa, no la de UTC.
        day = (datetime.fromtimestamp(ts, UTC) + offset).date()
        by_date[day] = float(close)  # si se repite el día (vela en curso), gana la última

    dates = sorted(by_date)
    currency = meta.get("currency")
    name = meta.get("longName") or meta.get("shortName")
    return PriceSeries(
        symbol=symbol,
        currency=currency if isinstance(currency, str) else None,
        name=name if isinstance(name, str) else None,
        dates=dates,
        closes=[by_date[d] for d in dates],
        adjusted=adjusted,
        missing_points=missing,
    )


def _first_list(container: Any, key: str) -> list[Any] | None:
    if isinstance(container, list) and container and isinstance(container[0], dict):
        values = container[0].get(key)
        if isinstance(values, list):
            return values
    return None


def _exchange_tz(meta: dict[str, Any]) -> tuple[tzinfo, str | None]:
    name = meta.get("exchangeTimezoneName")
    if isinstance(name, str):
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError):
            pass
    offset = meta.get("gmtoffset")
    return timezone(timedelta(seconds=offset if isinstance(offset, int) else 0)), None


def _num(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw != raw:  # NaN
        return None
    return float(raw)


def _parse_bars(symbol: str, interval: str, result: dict[str, Any]) -> Bars:
    raw_meta = result.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        raise NotFoundError(SOURCE, f"sin precios para {symbol}")
    quote = indicators.get("quote")
    q = quote[0] if isinstance(quote, list) and quote and isinstance(quote[0], dict) else {}
    cols = {k: q.get(k) for k in ("open", "high", "low", "close", "volume")}
    n = len(timestamps)
    if any(not isinstance(v, list) or len(v) != n for k, v in cols.items() if k != "volume"):
        raise SourceError(SOURCE, f"velas incompletas para {symbol}")
    volumes = (
        cols["volume"] if isinstance(cols["volume"], list) and len(cols["volume"]) == n else None
    )
    adjclose = _first_list(indicators.get("adjclose"), "adjclose") if interval == "1d" else None
    adjusted = adjclose is not None and len(adjclose) == n

    tz, tz_name = _exchange_tz(meta)
    bars: list[Bar] = []
    missing = 0
    for i, ts in enumerate(timestamps):
        o, h, lo, c = (_num(cols[k][i]) for k in ("open", "high", "low", "close"))  # type: ignore[index]
        if not isinstance(ts, int) or o is None or h is None or lo is None or c is None:
            missing += 1
            continue
        # Una vela con máximo menor al mínimo (o precios no positivos) es un dato roto.
        if min(o, h, lo, c) <= 0 or h < max(o, c, lo) or lo > min(o, c, h):
            missing += 1
            continue
        factor = 1.0
        if adjusted:
            adj = _num(adjclose[i]) if adjclose is not None else None
            if adj is None or adj <= 0:
                missing += 1
                continue
            factor = adj / c
        vol = _num(volumes[i]) if volumes is not None else None
        bars.append(
            Bar(
                time=datetime.fromtimestamp(ts, UTC).astimezone(tz),
                open=o * factor,
                high=h * factor,
                low=lo * factor,
                close=c * factor,
                volume=vol if vol is not None and vol >= 0 else None,
            )
        )
    bars.sort(key=lambda b: b.time)
    currency = meta.get("currency")
    name = meta.get("longName") or meta.get("shortName")
    return Bars(
        symbol=symbol,
        interval=interval,
        currency=currency if isinstance(currency, str) else None,
        name=name if isinstance(name, str) else None,
        exchange_timezone=tz_name,
        bars=bars,
        adjusted=adjusted,
        missing_points=missing,
    )
