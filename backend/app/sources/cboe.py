"""Cadenas de opciones demoradas de CBOE (gratis, sin clave).

`GET https://cdn.cboe.com/api/global/delayed_quotes/options/{SIMBOLO}.json`
(índices con guion bajo: `_SPX`, `_NDX`, `_VIX`).

Respuesta: `{"timestamp": ..., "data": {"current_price": ..., "options": [{"option":
"SPY261218C00600000", "bid", "ask", "iv", "open_interest", "volume", "delta", "gamma", ...}]}}`.
El código de cada opción sigue el formato OCC: raíz + AAMMDD + C/P + strike × 1000 (8 dígitos).
Los datos tienen unos 15 minutos de demora.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.sources.errors import NotFoundError, SourceError

SOURCE = "CBOE (cotizaciones demoradas)"
_OCC = re.compile(r"^(?P<root>[A-Z.]+?)(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


@dataclass(frozen=True)
class OptionQuote:
    expiry: date
    kind: str  # "C" o "P"
    strike: float
    bid: float | None
    ask: float | None
    iv: float | None  # fracción (0.25 = 25 %)
    open_interest: float
    volume: float
    delta: float | None
    gamma: float | None


@dataclass(frozen=True)
class OptionChain:
    symbol: str
    spot: float
    as_of: datetime | None
    options: list[OptionQuote]


def cboe_symbol(symbol: str) -> str:
    """'^SPX' o 'SPX' → '_SPX' para los índices que CBOE publica con guion bajo."""
    s = symbol.upper().lstrip("^")
    return f"_{s}" if s in {"SPX", "NDX", "VIX", "RUT", "XSP", "DJX"} else s


def parse_occ(code: str) -> tuple[date, str, float] | None:
    m = _OCC.match(code)
    if not m:
        return None
    ymd = m.group("ymd")
    try:
        expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return expiry, m.group("cp"), int(m.group("strike")) / 1000


def _f(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


class CboeClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def url(self, symbol: str) -> str:
        return f"{self._base_url}/api/global/delayed_quotes/options/{cboe_symbol(symbol)}.json"

    async def chain(self, symbol: str) -> OptionChain:
        try:
            response = await self._client.get(self.url(symbol))
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code in (403, 404):
            raise NotFoundError(SOURCE, f"CBOE no publica opciones de {symbol}")
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(SOURCE, "respuesta no es JSON") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        spot = _f(data.get("current_price")) or _f(data.get("close"))
        if spot is None or spot <= 0:
            raise SourceError(SOURCE, f"sin precio del subyacente para {symbol}")
        as_of = None
        ts = payload.get("timestamp")
        if isinstance(ts, str):
            try:
                as_of = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=UTC)
            except ValueError:
                as_of = None
        options: list[OptionQuote] = []
        for row in data.get("options") or []:
            if not isinstance(row, dict) or not isinstance(row.get("option"), str):
                continue
            parsed = parse_occ(row["option"])
            if parsed is None:
                continue
            expiry, kind, strike = parsed
            iv = _f(row.get("iv"))
            options.append(
                OptionQuote(
                    expiry=expiry,
                    kind=kind,
                    strike=strike,
                    bid=_f(row.get("bid")),
                    ask=_f(row.get("ask")),
                    iv=iv if iv is not None and iv > 0 else None,
                    open_interest=_f(row.get("open_interest")) or 0.0,
                    volume=_f(row.get("volume")) or 0.0,
                    delta=_f(row.get("delta")),
                    gamma=_f(row.get("gamma")),
                )
            )
        if not options:
            raise NotFoundError(SOURCE, f"CBOE no tiene opciones listadas para {symbol}")
        return OptionChain(symbol=symbol.upper(), spot=spot, as_of=as_of, options=options)
