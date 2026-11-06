"""Precios diarios desde el endpoint público de gráficos de Yahoo Finance (gratuito, sin clave).

Es un endpoint no oficial: puede cambiar o limitar pedidos sin aviso. Por eso todo error se
traduce a `SourceError` y el resto de la app lo muestra como "fuente no disponible" en lugar de
romperse o mostrar datos viejos como si fueran actuales.

Se usa el cierre **ajustado** (por dividendos y splits) para calcular rendimientos; si Yahoo no
lo trae, se cae al cierre simple y se deja constancia en `adjusted=False`.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

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

    async def get_daily_prices(self, symbol: str, price_range: PriceRange) -> PriceSeries:
        params = {"range": price_range, "interval": "1d", "events": "div,splits"}
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
        return _parse_result(symbol, results[0])


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
