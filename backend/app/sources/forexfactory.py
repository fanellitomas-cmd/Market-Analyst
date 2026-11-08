"""Calendario económico semanal de ForexFactory (feed JSON público, gratis y sin clave).

`GET https://nfs.faireconomy.media/ff_calendar_thisweek.json` →
`[{title, country, date, impact, forecast, previous}, ...]`

El pronóstico (`forecast`) es el consenso que publica ForexFactory para cada dato. El feed sólo
trae la semana en curso y no incluye el dato real: por eso el agente guarda cada pronóstico en
la base y el dato real se toma después de FRED.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.sources.errors import SourceError

SOURCE = "ForexFactory"
_VALUE_RE = re.compile(r"^\s*([<>]?)\s*(-?\d+(?:\.\d+)?)\s*([%KMBT]?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class CalendarItem:
    title: str
    country: str
    release_at: datetime  # con zona horaria
    impact: str  # High | Medium | Low | Holiday
    forecast: str | None
    previous: str | None


def parse_value(raw: str | None) -> tuple[float, str] | None:
    """'0.3%' → (0.3, '%'); '180K' → (180.0, 'K'); '' o '<0.1%' ambiguo → None."""
    if not raw:
        return None
    m = _VALUE_RE.match(raw)
    if not m or m.group(1):  # "<0.1%" no es un número exacto
        return None
    return float(m.group(2)), m.group(3).upper()


class ForexFactoryClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/ff_calendar_thisweek.json"

    async def this_week(self) -> list[CalendarItem]:
        try:
            response = await self._client.get(self.url)
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code == 429:
            raise SourceError(
                SOURCE, "límite de pedidos del sitio (429): volvé a intentar en unos minutos"
            )
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise SourceError(SOURCE, "respuesta no es JSON") from exc
        if not isinstance(payload, list):
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        items: list[CalendarItem] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            title, country, raw_date = row.get("title"), row.get("country"), row.get("date")
            if not (
                isinstance(title, str) and isinstance(country, str) and isinstance(raw_date, str)
            ):
                continue
            try:
                when = datetime.fromisoformat(raw_date)
            except ValueError:
                continue
            if when.tzinfo is None:
                continue  # sin zona horaria no se puede saber cuándo se publica
            impact = row.get("impact")
            forecast, previous = row.get("forecast"), row.get("previous")
            items.append(
                CalendarItem(
                    title=title.strip(),
                    country=country.strip().upper(),
                    release_at=when,
                    impact=impact if isinstance(impact, str) else "",
                    forecast=forecast.strip()
                    if isinstance(forecast, str) and forecast.strip()
                    else None,
                    previous=previous.strip()
                    if isinstance(previous, str) and previous.strip()
                    else None,
                )
            )
        return items
