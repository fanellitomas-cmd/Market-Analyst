"""Argentina: variables del BCRA, cotizaciones del dólar y riesgo país.

Las variables del BCRA se identifican por su **descripción** y no por un número fijo: el BCRA
renumeró variables entre versiones de la API. Cada indicador define palabras que la descripción
debe contener (y otras que no), y la respuesta incluye la descripción exacta que encontró, para
que se pueda verificar a simple vista qué serie se está mostrando.
"""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from app.core.cache import TTLCache
from app.domain import DataStatus, SourceInfo, StrictModel
from app.sources import argentina_markets as am
from app.sources import bcra
from app.sources.argentina_markets import ArgentinaDatosClient, DolarApiClient, DollarQuote
from app.sources.bcra import BcraClient, BcraVariable
from app.sources.errors import NotFoundError, SourceError

logger = logging.getLogger(__name__)

Kind = Literal["musd", "mars", "fx", "pct", "index", "bps"]
SeriesPoints = list[tuple[date, float]]

BCRA_HISTORY_START = date(2010, 1, 1)
_NOT_EFFECTIVE = r"e\.\s?a\.|efectiva anual|\btea\b"


@dataclass(frozen=True)
class BcraSpec:
    key: str
    name: str
    group: str
    kind: Kind
    description: str
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    # Patrones sobre la unidad: el BCRA publica la BADLAR nominal y la efectiva con la MISMA
    # descripción, y sólo las distingue la unidad ("nominal anual" / "efectivo anual").
    unit_include: tuple[str, ...] = ()


NOMINAL = (r"nominal",)

BCRA_SPECS: list[BcraSpec] = [
    BcraSpec(
        "reservas",
        "Reservas internacionales",
        "BCRA",
        "musd",
        "Reservas brutas del BCRA.",
        (r"reservas internacionales",),
        (r"variaci",),
    ),
    BcraSpec(
        "base_monetaria",
        "Base monetaria",
        "BCRA",
        "mars",
        "Billetes y monedas en circulación más depósitos de los bancos en el BCRA.",
        (r"^base monetaria",),
        (r"variaci", r"promedio"),
    ),
    BcraSpec(
        "m2_privado",
        "M2 privado (var. interanual)",
        "BCRA",
        "pct",
        "Variación interanual del promedio móvil de 30 días del M2 privado.",
        (r"m2 privado",),
    ),
    BcraSpec(
        "prestamos_privados",
        "Préstamos al sector privado",
        "BCRA",
        "mars",
        "Stock de préstamos de los bancos al sector privado.",
        (r"^prestamos de las entidades financieras al sector privado",),
    ),
    BcraSpec(
        "tc_mayorista",
        "Dólar mayorista (BCRA)",
        "Tipo de cambio",
        "fx",
        "Tipo de cambio mayorista de referencia (Comunicación A 3500), pesos por dólar.",
        (r"tipo de cambio mayorista",),
    ),
    BcraSpec(
        "tc_minorista",
        "Dólar minorista (BCRA)",
        "Tipo de cambio",
        "fx",
        "Tipo de cambio minorista, promedio vendedor.",
        (r"tipo de cambio minorista",),
    ),
    BcraSpec(
        "banda_inferior",
        "Banda cambiaria: piso",
        "Tipo de cambio",
        "fx",
        "Límite inferior del régimen de bandas cambiarias.",
        (r"bandas cambiarias", r"inferior"),
    ),
    BcraSpec(
        "banda_superior",
        "Banda cambiaria: techo",
        "Tipo de cambio",
        "fx",
        "Límite superior del régimen de bandas cambiarias.",
        (r"bandas cambiarias", r"superior"),
    ),
    BcraSpec(
        "tasa_politica",
        "Tasa de política monetaria",
        "Tasas",
        "pct",
        "Tasa de referencia del BCRA (nominal anual). Fijate la fecha: el BCRA puede "
        "dejar de publicarla.",
        (r"politica monetaria",),
        unit_include=NOMINAL,
    ),
    BcraSpec(
        "badlar",
        "BADLAR bancos privados",
        "Tasas",
        "pct",
        "Tasa por depósitos a plazo fijo de más de $1 millón a 30-35 días (nominal anual).",
        (r"badlar", r"privad"),
        unit_include=NOMINAL,
    ),
    BcraSpec(
        "tamar",
        "TAMAR bancos privados",
        "Tasas",
        "pct",
        "Tasa mayorista de depósitos a plazo fijo de más de $1.000 millones (nominal anual).",
        (r"tamar", r"privad"),
        unit_include=NOMINAL,
    ),
    BcraSpec(
        "plazo_fijo",
        "Plazo fijo a 30 días",
        "Tasas",
        "pct",
        "Tasa de depósitos a 30 días en entidades financieras (nominal anual).",
        (r"depositos a 30 dias de plazo",),
        unit_include=NOMINAL,
    ),
    BcraSpec(
        "inflacion_mensual",
        "Inflación mensual",
        "Inflación",
        "pct",
        "Variación mensual del IPC (INDEC), publicada por el BCRA.",
        (r"^inflacion mensual",),
    ),
    BcraSpec(
        "inflacion_interanual",
        "Inflación interanual",
        "Inflación",
        "pct",
        "Variación del IPC contra el mismo mes del año anterior.",
        (r"^inflacion interanual",),
    ),
    BcraSpec(
        "inflacion_esperada",
        "Inflación esperada (REM)",
        "Inflación",
        "pct",
        "Mediana de la inflación esperada para los próximos 12 meses según el "
        "relevamiento de expectativas de mercado (REM).",
        (r"expectativas de mercado", r"proximos 12 meses"),
    ),
    BcraSpec(
        "cer",
        "CER",
        "Índices de ajuste",
        "index",
        "Coeficiente de Estabilización de Referencia: ajusta bonos por inflación.",
        (r"^coeficiente de estabilizacion de referencia",),
    ),
    BcraSpec(
        "uva",
        "UVA",
        "Índices de ajuste",
        "index",
        "Unidad de Valor Adquisitivo: ajusta créditos hipotecarios y plazos fijos UVA.",
        (r"^unidad de valor adquisitivo",),
    ),
]
BCRA_BY_KEY = {s.key: s for s in BCRA_SPECS}

# Tipos de dólar que se muestran, en este orden. Clave = `casa` de DolarApi/ArgentinaDatos.
DOLLAR_HOUSES: list[tuple[str, str]] = [
    ("oficial", "Oficial (minorista)"),
    ("mayorista", "Mayorista"),
    ("bolsa", "MEP"),
    ("contadoconliqui", "Contado con liqui"),
    ("blue", "Blue"),
    ("cripto", "Cripto"),
    ("tarjeta", "Tarjeta"),
]
DOLLAR_NAMES = dict(DOLLAR_HOUSES)


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar descripciones del BCRA."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


PRINCIPAL_CATEGORY = "principales variables"
# Las series mensuales se publican con ~40 días de rezago; más de 60 días sin dato es raro.
STALE_DAYS = 60


def match_variable(spec: BcraSpec, variables: list[BcraVariable]) -> BcraVariable | None:
    """La variable cuya descripción (y unidad) cumple el patrón. Ante varias, gana la de la
    categoría "Principales Variables" y después la de menor id (orden estable). El catálogo tiene
    más de mil series (depósitos y préstamos por tipo de titular, etc.), así que la categoría
    evita confundir, por ejemplo, el CER con "depósitos ajustables por CER"."""
    candidates = [
        v
        for v in variables
        if all(re.search(p, normalize(v.description)) for p in spec.include)
        and not any(re.search(p, normalize(v.description)) for p in spec.exclude)
        and all(re.search(p, normalize(v.unit or "")) for p in spec.unit_include)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda v: (normalize(v.category or "") != PRINCIPAL_CATEGORY, v.id),
    )


# ---------- Modelos de respuesta ----------


class ArIndicatorInfo(StrictModel):
    key: str
    name: str
    group: str
    kind: Kind
    description: str
    source: str


class ArLatest(StrictModel):
    indicator: ArIndicatorInfo
    status: DataStatus
    value: float | None
    date: date | None
    source_description: str | None  # descripción exacta de la serie en la fuente
    source_unit: str | None
    note: str | None = None


class DollarRow(StrictModel):
    house: str
    name: str
    buy: float | None
    sell: float | None
    updated_at: datetime | None
    gap_vs_official: float | None  # venta / venta del oficial − 1 (fracción)


class DollarsBlock(StrictModel):
    status: DataStatus
    rows: list[DollarRow]
    note: str | None = None
    source: SourceInfo


class ArDashboardResponse(StrictModel):
    dollars: DollarsBlock
    country_risk: ArLatest
    country_risk_change: float | None  # contra el dato anterior, en puntos básicos
    bcra: list[ArLatest]
    bcra_source: SourceInfo


class ArPoint(StrictModel):
    date: date
    value: float


class ArSeriesResponse(StrictModel):
    indicator: ArIndicatorInfo
    start: date
    observations: list[ArPoint]
    source_description: str | None
    source: SourceInfo


def series_catalog() -> list[ArIndicatorInfo]:
    items = [
        ArIndicatorInfo(
            key=s.key,
            name=s.name,
            group=s.group,
            kind=s.kind,
            description=s.description,
            source=bcra.SOURCE,
        )
        for s in BCRA_SPECS
    ]
    items.append(
        ArIndicatorInfo(
            key="riesgo_pais",
            name="Riesgo país",
            group="Riesgo",
            kind="bps",
            description="Spread de los bonos argentinos en dólares sobre los del Tesoro de "
            "EE.UU. (EMBI, JP Morgan), en puntos básicos.",
            source=am.ARGDATOS,
        )
    )
    items.extend(
        ArIndicatorInfo(
            key=f"dolar_{house}",
            name=f"Dólar {name}",
            group="Dólar",
            kind="fx",
            description=f"Cotización de venta del dólar {name.lower()}, pesos por dólar.",
            source=am.ARGDATOS,
        )
        for house, name in DOLLAR_HOUSES
    )
    return items


CATALOG_BY_KEY = {i.key: i for i in series_catalog()}


# ---------- Servicio ----------


class ArMacroService:
    def __init__(
        self,
        bcra_client: BcraClient,
        dolarapi: DolarApiClient,
        argdatos: ArgentinaDatosClient,
        catalog_cache: TTLCache[list[BcraVariable]],
        quotes_cache: TTLCache[list[DollarQuote]],
        series_cache: TTLCache[SeriesPoints],
    ) -> None:
        self._bcra = bcra_client
        self._dolarapi = dolarapi
        self._argdatos = argdatos
        self._catalog_cache = catalog_cache
        self._quotes_cache = quotes_cache
        self._series_cache = series_cache

    @staticmethod
    def _src(name: str, url: str) -> SourceInfo:
        return SourceInfo(name=name, url=url, fetched_at=datetime.now(UTC))

    async def _variables(self) -> list[BcraVariable]:
        return await self._catalog_cache.get_or_load("catalog", self._bcra.list_variables)

    async def dollar_quotes(self) -> list[DollarQuote]:
        return await self._quotes()

    async def _quotes(self) -> list[DollarQuote]:
        return await self._quotes_cache.get_or_load("quotes", self._dolarapi.get_quotes)

    async def country_risk_points(self) -> SeriesPoints:
        return await self._country_risk()

    async def _country_risk(self) -> SeriesPoints:
        return await self._series_cache.get_or_load(
            "riesgo_pais", self._argdatos.get_country_risk_history
        )

    # --- Dashboard ---

    async def dashboard(self) -> ArDashboardResponse:
        dollars, risk, bcra_items = await asyncio.gather(
            self._dollars_block(), self._country_risk_latest(), self._bcra_latest()
        )
        return ArDashboardResponse(
            dollars=dollars,
            country_risk=risk[0],
            country_risk_change=risk[1],
            bcra=bcra_items,
            bcra_source=self._src(bcra.SOURCE, self._bcra.catalog_url),
        )

    async def _dollars_block(self) -> DollarsBlock:
        source = self._src(am.DOLARAPI, self._dolarapi.url)
        try:
            quotes = {q.house: q for q in await self._quotes()}
        except SourceError as exc:
            logger.warning("Argentina: falló DolarApi: %s", exc)
            return DollarsBlock(status="ERROR_FUENTE", rows=[], note=exc.message, source=source)
        official = quotes.get("oficial")
        official_sell = official.sell if official else None
        rows: list[DollarRow] = []
        for house, name in DOLLAR_HOUSES:
            q = quotes.get(house)
            if q is None:
                continue
            gap = (
                q.sell / official_sell - 1.0
                if q.sell is not None and official_sell is not None and house != "oficial"
                else None
            )
            rows.append(
                DollarRow(
                    house=house,
                    name=name,
                    buy=q.buy,
                    sell=q.sell,
                    updated_at=q.updated_at,
                    gap_vs_official=gap,
                )
            )
        return DollarsBlock(
            status="OK" if rows else "NO_DISPONIBLE",
            rows=rows,
            note=None if rows else "La fuente no devolvió cotizaciones conocidas.",
            source=source,
        )

    async def _country_risk_latest(self) -> tuple[ArLatest, float | None]:
        info = CATALOG_BY_KEY["riesgo_pais"]
        try:
            points = await self._country_risk()
        except SourceError as exc:
            logger.warning("Argentina: falló riesgo país: %s", exc)
            return (
                ArLatest(
                    indicator=info,
                    status="ERROR_FUENTE",
                    value=None,
                    date=None,
                    source_description=None,
                    source_unit=None,
                    note=exc.message,
                ),
                None,
            )
        if not points:
            return (
                ArLatest(
                    indicator=info,
                    status="NO_DISPONIBLE",
                    value=None,
                    date=None,
                    source_description=None,
                    source_unit=None,
                ),
                None,
            )
        last_d, last_v = points[-1]
        change = last_v - points[-2][1] if len(points) > 1 else None
        return (
            ArLatest(
                indicator=info,
                status="OK",
                value=last_v,
                date=last_d,
                source_description="Riesgo país (EMBI)",
                source_unit="puntos básicos",
            ),
            change,
        )

    async def _bcra_latest(self) -> list[ArLatest]:
        try:
            variables = await self._variables()
        except SourceError as exc:
            logger.warning("Argentina: falló el catálogo del BCRA: %s", exc)
            return [
                ArLatest(
                    indicator=CATALOG_BY_KEY[s.key],
                    status="ERROR_FUENTE",
                    value=None,
                    date=None,
                    source_description=None,
                    source_unit=None,
                    note=exc.message,
                )
                for s in BCRA_SPECS
            ]
        out: list[ArLatest] = []
        for spec in BCRA_SPECS:
            info = CATALOG_BY_KEY[spec.key]
            var = match_variable(spec, variables)
            if var is None:
                out.append(
                    ArLatest(
                        indicator=info,
                        status="NO_DISPONIBLE",
                        value=None,
                        date=None,
                        source_description=None,
                        source_unit=None,
                        note="El BCRA no publica una variable con esta descripción.",
                    )
                )
                continue
            ok = var.last_value is not None and var.last_date is not None
            note = None
            if var.last_date is not None:
                age = (datetime.now(UTC).date() - var.last_date).days
                if age > STALE_DAYS:
                    note = f"dato desactualizado: el BCRA no lo publica desde hace {age} días"
            out.append(
                ArLatest(
                    indicator=info,
                    status="OK" if ok else "NO_DISPONIBLE",
                    value=var.last_value,
                    date=var.last_date,
                    source_description=f"{var.description} (id {var.id})",
                    source_unit=var.unit,
                    note=note,
                )
            )
        return out

    # --- Series ---

    async def series(self, key: str, start: date) -> ArSeriesResponse:
        info = CATALOG_BY_KEY[key]
        description: str | None = None
        if key in BCRA_BY_KEY:
            var = match_variable(BCRA_BY_KEY[key], await self._variables())
            if var is None:
                raise NotFoundError(bcra.SOURCE, "el BCRA no publica esta variable")
            today = datetime.now(UTC).date()
            points = await self._series_cache.get_or_load(
                f"bcra:{var.id}",
                lambda: self._bcra.get_series(var.id, BCRA_HISTORY_START, today),
            )
            description = f"{var.description} (id {var.id})"
            source = self._src(bcra.SOURCE, self._bcra.series_url(var.id))
        elif key == "riesgo_pais":
            points = await self._country_risk()
            source = self._src(am.ARGDATOS, self._argdatos.country_risk_url)
        else:
            house = key.removeprefix("dolar_")
            points = await self._series_cache.get_or_load(
                f"dolar:{house}", lambda: self._argdatos.get_dollar_history(house)
            )
            source = self._src(am.ARGDATOS, self._argdatos.dollar_url(house))

        return ArSeriesResponse(
            indicator=info,
            start=start,
            observations=[ArPoint(date=d, value=v) for d, v in points if d >= start],
            source_description=description,
            source=source,
        )
