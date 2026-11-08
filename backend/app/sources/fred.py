"""Series de FRED (Reserva Federal de St. Louis) vía la descarga CSV pública, sin clave.

URL: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2020-01-01`
Formato: una fila de encabezado (`observation_date,DGS10`; versiones viejas usan `DATE`) y una
fila por observación. Los días sin dato (feriados, por ejemplo) vienen como `.` o vacíos: se
omiten de la serie y se cuentan en `missing_points`, nunca se rellenan.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date

import httpx

from app.sources.errors import NotFoundError, SourceError

SOURCE = "FRED (Reserva Federal de St. Louis)"
_SERIES_ID_RE = re.compile(r"^[A-Z0-9_]{1,30}$")


@dataclass(frozen=True)
class FredSeries:
    series_id: str
    dates: list[date]
    values: list[float]
    missing_points: int


class FredClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    def csv_url(self, series_id: str) -> str:
        return f"{self._base_url}/graph/fredgraph.csv?id={series_id}"

    def series_page_url(self, series_id: str) -> str:
        return f"{self._base_url}/series/{series_id}"

    async def get_series(self, series_id: str, start: date | None = None) -> FredSeries:
        if not _SERIES_ID_RE.fullmatch(series_id):
            raise ValueError(f"id de serie inválido: {series_id!r}")
        params = {"id": series_id}
        if start is not None:
            params["cosd"] = start.isoformat()
        try:
            response = await self._client.get(
                f"{self._base_url}/graph/fredgraph.csv", params=params
            )
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code == 404:
            raise NotFoundError(SOURCE, f"no existe la serie {series_id}")
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        return parse_csv(series_id, response.text)


def parse_csv(series_id: str, text: str) -> FredSeries:
    rows = list(csv.reader(io.StringIO(text.strip())))
    if not rows:
        raise SourceError(SOURCE, f"respuesta vacía para {series_id}")
    header = [h.strip().upper() for h in rows[0]]
    if header[0] not in ("OBSERVATION_DATE", "DATE") or len(header) < 2:
        # FRED devuelve una página HTML (200) cuando la serie no existe.
        raise NotFoundError(SOURCE, f"no existe la serie {series_id}")

    by_date: dict[date, float] = {}
    missing = 0
    for row in rows[1:]:
        if len(row) < 2:
            continue
        try:
            day = date.fromisoformat(row[0].strip())
        except ValueError:
            continue
        raw = row[1].strip()
        try:
            value = float(raw)
        except ValueError:  # "." o vacío = sin dato ese día
            missing += 1
            continue
        by_date[day] = value

    dates = sorted(by_date)
    return FredSeries(
        series_id=series_id,
        dates=dates,
        values=[by_date[d] for d in dates],
        missing_points=missing,
    )
