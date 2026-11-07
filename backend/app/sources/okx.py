"""Derivados cripto desde la API pública de OKX (gratis, sin clave).

Endpoints (respuesta `{"code": "0", "msg": "", "data": [...]}`; los números vienen como texto):
  - /api/v5/public/funding-rate?instId=BTC-USDT-SWAP           tasa de financiamiento vigente
  - /api/v5/public/funding-rate-history?instId=...&limit=N     historial (cada 8 h)
  - /api/v5/public/open-interest?instType=SWAP&instId=...      interés abierto actual
  - /api/v5/rubik/stat/contracts/open-interest-volume?ccy=BTC&period=1D   historial (USD)
  - /api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1H
  - /api/v5/public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled
  - /api/v5/public/instruments?instType=SWAP&instId=...        tamaño del contrato (ctVal)
  - /api/v5/market/ticker?instId=...                           último precio

Son datos de un solo exchange: muestran el apalancamiento de OKX, no el del mercado entero.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.sources.errors import NotFoundError, SourceError

SOURCE = "OKX"


def _f(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _ts(raw: Any) -> datetime | None:
    v = _f(raw)
    return datetime.fromtimestamp(v / 1000, UTC) if v is not None else None


@dataclass(frozen=True)
class Funding:
    rate: float  # por período de 8 h, como fracción (0.0001 = 0,01 %)
    time: datetime | None


@dataclass(frozen=True)
class OpenInterest:
    contracts: float | None
    coins: float | None
    usd: float | None
    time: datetime | None


@dataclass(frozen=True)
class Liquidation:
    side: str  # "long" o "short": la posición que se liquidó
    contracts: float
    price: float
    time: datetime


class OkxClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def _data(self, path: str, params: dict[str, str]) -> list[Any]:
        try:
            response = await self._client.get(f"{self._base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(SOURCE, "respuesta no es JSON") from exc
        if not isinstance(payload, dict):
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        if str(payload.get("code")) != "0":
            msg = str(payload.get("msg") or payload.get("code"))
            if str(payload.get("code")) in ("51001", "51000"):
                raise NotFoundError(SOURCE, f"instrumento inexistente: {msg}")
            raise SourceError(SOURCE, msg)
        data = payload.get("data")
        if not isinstance(data, list):
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        return data

    async def funding(self, inst: str) -> Funding:
        data = await self._data("/api/v5/public/funding-rate", {"instId": inst})
        rate = _f(data[0].get("fundingRate")) if data and isinstance(data[0], dict) else None
        if rate is None:
            raise SourceError(SOURCE, f"sin tasa de financiamiento para {inst}")
        return Funding(rate=rate, time=_ts(data[0].get("fundingTime")))

    async def funding_history(self, inst: str, limit: int = 90) -> list[Funding]:
        data = await self._data(
            "/api/v5/public/funding-rate-history", {"instId": inst, "limit": str(limit)}
        )
        out: list[Funding] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            realized = _f(row.get("realizedRate"))
            rate = realized if realized is not None else _f(row.get("fundingRate"))
            t = _ts(row.get("fundingTime"))
            if rate is not None and t is not None:
                out.append(Funding(rate=rate, time=t))
        return sorted(out, key=lambda f: f.time or datetime.min.replace(tzinfo=UTC))

    async def open_interest(self, inst: str) -> OpenInterest:
        data = await self._data(
            "/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}
        )
        row = data[0] if data and isinstance(data[0], dict) else {}
        return OpenInterest(
            contracts=_f(row.get("oi")),
            coins=_f(row.get("oiCcy")),
            usd=_f(row.get("oiUsd")),
            time=_ts(row.get("ts")),
        )

    async def oi_history(self, ccy: str) -> list[tuple[datetime, float]]:
        """Interés abierto diario (USD) de todos los contratos de la moneda en OKX."""
        data = await self._data(
            "/api/v5/rubik/stat/contracts/open-interest-volume", {"ccy": ccy, "period": "1D"}
        )
        out: list[tuple[datetime, float]] = []
        for row in data:
            if isinstance(row, list) and len(row) >= 2:
                t, v = _ts(row[0]), _f(row[1])
                if t is not None and v is not None:
                    out.append((t, v))
        return sorted(out)

    async def long_short_ratio(self, ccy: str) -> list[tuple[datetime, float]]:
        """Cuentas con posición larga / cuentas con posición corta (por hora)."""
        data = await self._data(
            "/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": ccy, "period": "1H"}
        )
        out: list[tuple[datetime, float]] = []
        for row in data:
            if isinstance(row, list) and len(row) >= 2:
                t, v = _ts(row[0]), _f(row[1])
                if t is not None and v is not None:
                    out.append((t, v))
        return sorted(out)

    async def contract_value(self, inst: str) -> float:
        data = await self._data("/api/v5/public/instruments", {"instType": "SWAP", "instId": inst})
        ct = _f(data[0].get("ctVal")) if data and isinstance(data[0], dict) else None
        if ct is None or ct <= 0:
            raise SourceError(SOURCE, f"sin tamaño de contrato para {inst}")
        return ct

    async def last_price(self, inst: str) -> float:
        data = await self._data("/api/v5/market/ticker", {"instId": inst})
        px = _f(data[0].get("last")) if data and isinstance(data[0], dict) else None
        if px is None:
            raise SourceError(SOURCE, f"sin precio para {inst}")
        return px

    async def liquidations(self, uly: str) -> list[Liquidation]:
        data = await self._data(
            "/api/v5/public/liquidation-orders",
            {"instType": "SWAP", "uly": uly, "state": "filled"},
        )
        out: list[Liquidation] = []
        for group in data:
            details = group.get("details") if isinstance(group, dict) else None
            for d in details if isinstance(details, list) else []:
                if not isinstance(d, dict):
                    continue
                side, sz, px, t = (
                    d.get("posSide"),
                    _f(d.get("sz")),
                    _f(d.get("bkPx")),
                    _ts(d.get("ts")),
                )
                if side in ("long", "short") and sz and px and t:
                    out.append(Liquidation(side=side, contracts=sz, price=px, time=t))
        return out
