"""Cliente de la API del Banco Mundial (gratuita, sin clave).

Documentación: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
Formato de respuesta: `[metadatos, [filas...]]`, o `[{"message": [...]}]` si hay error.
Los años sin dato vienen con `value: null`: se omiten de la serie (no se convierten en 0) y se
cuentan en `missing_periods` para que la interfaz lo pueda mostrar.
"""

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.domain import Observation
from app.sources.errors import NotFoundError, SourceError

SOURCE = "Banco Mundial"
_MAX_PAGES = 20


@dataclass
class CountrySeries:
    country_code: str
    country_name: str
    observations: list[Observation] = field(default_factory=list)
    missing_periods: int = 0


class WorldBankClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def indicator_url(self, indicator_id: str, country_codes: list[str]) -> str:
        return f"{self._base_url}/country/{';'.join(country_codes)}/indicator/{indicator_id}"

    async def get_indicator(
        self,
        indicator_id: str,
        country_codes: list[str],
        start_year: int,
        end_year: int,
    ) -> dict[str, CountrySeries]:
        url = self.indicator_url(indicator_id, country_codes)
        result: dict[str, CountrySeries] = {}
        page = 1
        while True:
            params = {
                "format": "json",
                "per_page": "1000",
                "date": f"{start_year}:{end_year}",
                "page": str(page),
            }
            meta, rows = await self._fetch_page(url, params)
            for row in rows:
                self._add_row(result, row)
            pages = meta.get("pages")
            if not isinstance(pages, int) or page >= pages or page >= _MAX_PAGES:
                break
            page += 1

        for series in result.values():
            series.observations.sort(key=lambda o: o.period)
        return result

    async def _fetch_page(
        self, url: str, params: dict[str, str]
    ) -> tuple[dict[str, Any], list[Any]]:
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(SOURCE, "respuesta no es JSON") from exc

        if not isinstance(payload, list) or not payload:
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        first = payload[0]
        if isinstance(first, dict) and "message" in first:
            detail = _error_message(first["message"])
            raise NotFoundError(SOURCE, detail)
        if len(payload) < 2 or not isinstance(first, dict):
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        rows = payload[1]
        if rows is None:  # sin filas para ese rango
            return first, []
        if not isinstance(rows, list):
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        return first, rows

    @staticmethod
    def _add_row(result: dict[str, CountrySeries], row: Any) -> None:
        if not isinstance(row, dict):
            return
        code = row.get("countryiso3code") or ""
        country = row.get("country") or {}
        if not code and isinstance(country, dict):
            code = country.get("id") or ""
        if not isinstance(code, str) or not code:
            return
        name = country.get("value") if isinstance(country, dict) else None
        display = name if isinstance(name, str) else code
        series = result.setdefault(code, CountrySeries(code, display))

        year = row.get("date")
        value = row.get("value")
        if not isinstance(year, str):
            return
        if isinstance(value, bool) or not isinstance(value, int | float):
            series.missing_periods += 1
            return
        series.observations.append(Observation(period=year, value=float(value)))


def _error_message(raw: Any) -> str:
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return str(raw[0].get("value") or raw[0].get("key") or "error de la fuente")
    return "error de la fuente"
