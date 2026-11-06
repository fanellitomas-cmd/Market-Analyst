"""Cotizaciones del dólar y riesgo país: APIs comunitarias, gratuitas y sin clave.

  - DolarApi (https://dolarapi.com): cotización actual de cada tipo de dólar.
    `GET /v1/dolares` → `[{casa, nombre, compra, venta, fechaActualizacion}, ...]`
  - ArgentinaDatos (https://argentinadatos.com): historia diaria.
    `GET /v1/cotizaciones/dolares/{casa}` → `[{casa, compra, venta, fecha}, ...]`
    `GET /v1/finanzas/indices/riesgo-pais` → `[{valor, fecha}, ...]`

No son fuentes oficiales: recopilan cotizaciones de mercado (el blue, por definición, no tiene
fuente oficial). La interfaz lo aclara junto a cada dato.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from app.sources.errors import NotFoundError, SourceError

DOLARAPI = "DolarApi"
ARGDATOS = "ArgentinaDatos"


@dataclass(frozen=True)
class DollarQuote:
    house: str  # "oficial", "blue", "bolsa" (MEP), "contadoconliqui", ...
    name: str
    buy: float | None
    sell: float | None
    updated_at: datetime | None


def _num(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw <= 0:
        return None  # una cotización de 0 es "sin dato", no un dólar gratis
    return float(raw)


def _day(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


async def _get_json(client: httpx.AsyncClient, source: str, url: str) -> Any:
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        raise SourceError(source, f"no se pudo conectar ({type(exc).__name__})") from exc
    if response.status_code == 404:
        raise NotFoundError(source, "recurso inexistente")
    if response.status_code != 200:
        raise SourceError(source, f"respondió HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(source, "respuesta no es JSON") from exc


class DolarApiClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/v1/dolares"

    async def get_quotes(self) -> list[DollarQuote]:
        payload = await _get_json(self._client, DOLARAPI, self.url)
        if not isinstance(payload, list):
            raise SourceError(DOLARAPI, "formato de respuesta inesperado")
        out: list[DollarQuote] = []
        for row in payload:
            if not isinstance(row, dict) or not isinstance(row.get("casa"), str):
                continue
            updated: datetime | None = None
            raw_ts = row.get("fechaActualizacion")
            if isinstance(raw_ts, str):
                try:
                    updated = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except ValueError:
                    updated = None
            name = row.get("nombre")
            out.append(
                DollarQuote(
                    house=row["casa"],
                    name=name if isinstance(name, str) else row["casa"],
                    buy=_num(row.get("compra")),
                    sell=_num(row.get("venta")),
                    updated_at=updated,
                )
            )
        return out


class ArgentinaDatosClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def dollar_url(self, house: str) -> str:
        return f"{self._base_url}/v1/cotizaciones/dolares/{house}"

    @property
    def country_risk_url(self) -> str:
        return f"{self._base_url}/v1/finanzas/indices/riesgo-pais"

    async def get_dollar_history(self, house: str) -> list[tuple[date, float]]:
        """Serie diaria del precio de venta."""
        payload = await _get_json(self._client, ARGDATOS, self.dollar_url(house))
        return _series(payload, "venta")

    async def get_country_risk_history(self) -> list[tuple[date, float]]:
        payload = await _get_json(self._client, ARGDATOS, self.country_risk_url)
        return _series(payload, "valor")


def _series(payload: Any, value_key: str) -> list[tuple[date, float]]:
    if not isinstance(payload, list):
        raise SourceError(ARGDATOS, "formato de respuesta inesperado")
    points: dict[date, float] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        d = _day(row.get("fecha"))
        v = _num(row.get(value_key))
        if d is not None and v is not None:
            points[d] = v
    return sorted(points.items())
