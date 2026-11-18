"""API de Principales Variables del BCRA, v4.0 (gratuita, sin clave).

Manual: https://web2.bcra.gob.ar/Catalogo/Content/files/pdf/principales-variables-v4.pdf
  - Catálogo: `GET /estadisticas/v4.0/monetarias` → una fila por variable con su último dato.
  - Serie:    `GET /estadisticas/v4.0/monetarias/{idVariable}?desde=&hasta=&limit=&offset=`
    (`limit` máximo 3000; se pagina con `offset`).
Envoltorio: `{"status": 200, "metadata": {"resultset": {"count", "offset", "limit"}},
"results": [...]}`.

El parser acepta los nombres de campo de v3 (`fecha`/`valor` en cada fila) y de v4
(`ultFechaInformada`/`ultValorInformado` en el catálogo; `detalle: [{fecha, valor}]` en las
series): el BCRA ya cambió el formato una vez sin mantener compatibilidad.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from app.sources.errors import NotFoundError, SourceError

SOURCE = "BCRA"
_PAGE_LIMIT = 3000
_MAX_PAGES = 10


@dataclass(frozen=True)
class BcraVariable:
    id: int
    description: str
    category: str | None
    unit: str | None
    last_date: date | None
    last_value: float | None


def _parse_date(raw: Any) -> date | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%d/%m/%Y").date()
        except ValueError:
            return None


def _parse_number(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


class BcraClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    @property
    def catalog_url(self) -> str:
        return f"{self._base_url}/estadisticas/v4.0/monetarias"

    def series_url(self, variable_id: int) -> str:
        return f"{self.catalog_url}/{variable_id}"

    async def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code == 404:
            raise NotFoundError(SOURCE, "variable inexistente")
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(SOURCE, "respuesta no es JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise SourceError(SOURCE, "formato de respuesta inesperado")
        return payload

    async def list_variables(self) -> list[BcraVariable]:
        payload = await self._get(self.catalog_url, {"limit": str(_PAGE_LIMIT)})
        out: list[BcraVariable] = []
        for row in payload["results"]:
            if not isinstance(row, dict):
                continue
            vid = row.get("idVariable")
            desc = row.get("descripcion")
            if not isinstance(vid, int) or isinstance(vid, bool) or not isinstance(desc, str):
                continue
            category = row.get("categoria")
            unit = _first(row, "unidadExpresion", "unidad")
            out.append(
                BcraVariable(
                    id=vid,
                    description=" ".join(desc.split()),
                    category=category if isinstance(category, str) else None,
                    unit=unit if isinstance(unit, str) else None,
                    last_date=_parse_date(_first(row, "ultFechaInformada", "fecha")),
                    last_value=_parse_number(_first(row, "ultValorInformado", "valor")),
                )
            )
        if not out:
            raise SourceError(SOURCE, "el catálogo de variables vino vacío")
        return out

    async def get_series(
        self, variable_id: int, start: date, end: date
    ) -> list[tuple[date, float]]:
        points: dict[date, float] = {}
        offset = 0
        for _ in range(_MAX_PAGES):
            payload = await self._get(
                self.series_url(variable_id),
                {
                    "desde": start.isoformat(),
                    "hasta": end.isoformat(),
                    "limit": str(_PAGE_LIMIT),
                    "offset": str(offset),
                },
            )
            rows = _flatten_series_rows(payload["results"])
            for row in rows:
                d = _parse_date(row.get("fecha"))
                v = _parse_number(row.get("valor"))
                if d is not None and v is not None:
                    points[d] = v
            count = _resultset_count(payload)
            offset += _PAGE_LIMIT
            if count is None or offset >= count or not rows:
                break
        return sorted(points.items())


def _flatten_series_rows(results: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        detalle = item.get("detalle")
        if isinstance(detalle, list):  # v4: {"idVariable": 1, "detalle": [{fecha, valor}]}
            rows.extend(r for r in detalle if isinstance(r, dict))
        else:  # v3: {"idVariable": 1, "fecha": ..., "valor": ...}
            rows.append(item)
    return rows


def _resultset_count(payload: dict[str, Any]) -> int | None:
    meta = payload.get("metadata")
    resultset = meta.get("resultset") if isinstance(meta, dict) else None
    count = resultset.get("count") if isinstance(resultset, dict) else None
    return count if isinstance(count, int) else None
