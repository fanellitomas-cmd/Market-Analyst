"""Indicadores macroeconómicos anuales por país (fuente: Banco Mundial).

El catálogo de países e indicadores es cerrado a propósito: así la API no acepta códigos
arbitrarios y cada indicador lleva su unidad y una descripción en castellano para la interfaz.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.core.cache import TTLCache
from app.domain import SourceInfo
from app.schemas import (
    CountryInfo,
    CountrySeriesOut,
    CountrySnapshot,
    IndicatorInfo,
    LatestValue,
    MacroSeriesResponse,
    MacroSnapshotResponse,
)
from app.sources.errors import SourceError
from app.sources.worldbank import SOURCE, CountrySeries, WorldBankClient

logger = logging.getLogger(__name__)

COUNTRIES: list[CountryInfo] = [
    CountryInfo(code="USA", name="Estados Unidos", group="América del Norte"),
    CountryInfo(code="CAN", name="Canadá", group="América del Norte"),
    CountryInfo(code="ARG", name="Argentina", group="Latinoamérica"),
    CountryInfo(code="BRA", name="Brasil", group="Latinoamérica"),
    CountryInfo(code="MEX", name="México", group="Latinoamérica"),
    CountryInfo(code="CHL", name="Chile", group="Latinoamérica"),
    CountryInfo(code="EMU", name="Zona Euro", group="Europa"),
    CountryInfo(code="DEU", name="Alemania", group="Europa"),
    CountryInfo(code="FRA", name="Francia", group="Europa"),
    CountryInfo(code="ITA", name="Italia", group="Europa"),
    CountryInfo(code="GBR", name="Reino Unido", group="Europa"),
    CountryInfo(code="JPN", name="Japón", group="Asia"),
    CountryInfo(code="CHN", name="China", group="Asia"),
]
COUNTRY_BY_CODE = {c.code: c for c in COUNTRIES}

INDICATORS: list[IndicatorInfo] = [
    IndicatorInfo(
        key="gdp_growth",
        name="Crecimiento del PBI",
        unit="% anual",
        description="Variación anual del PBI real (a precios constantes).",
        source_id="NY.GDP.MKTP.KD.ZG",
    ),
    IndicatorInfo(
        key="gdp_usd",
        name="PBI",
        unit="USD corrientes",
        description="Producto bruto interno en dólares corrientes.",
        source_id="NY.GDP.MKTP.CD",
    ),
    IndicatorInfo(
        key="gdp_per_capita",
        name="PBI per cápita",
        unit="USD corrientes",
        description="PBI dividido por la población, en dólares corrientes.",
        source_id="NY.GDP.PCAP.CD",
    ),
    IndicatorInfo(
        key="inflation",
        name="Inflación",
        unit="% anual",
        description="Variación anual de los precios al consumidor (IPC).",
        source_id="FP.CPI.TOTL.ZG",
    ),
    IndicatorInfo(
        key="unemployment",
        name="Desempleo",
        unit="% de la fuerza laboral",
        description="Desempleo total, estimación modelada de la OIT.",
        source_id="SL.UEM.TOTL.ZS",
    ),
    IndicatorInfo(
        key="current_account",
        name="Cuenta corriente",
        unit="% del PBI",
        description="Saldo de la cuenta corriente de la balanza de pagos.",
        source_id="BN.CAB.XOKA.GD.ZS",
    ),
    IndicatorInfo(
        key="government_debt",
        name="Deuda del gobierno central",
        unit="% del PBI",
        description="Deuda total del gobierno central. Cobertura irregular según el país.",
        source_id="GC.DOD.TOTL.GD.ZS",
    ),
]
INDICATOR_BY_KEY = {i.key: i for i in INDICATORS}

DEFAULT_START_YEAR = 2000


def current_year() -> int:
    return datetime.now(UTC).year


class MacroService:
    def __init__(self, client: WorldBankClient, cache: TTLCache[dict[str, CountrySeries]]) -> None:
        self._client = client
        self._cache = cache

    async def _fetch(
        self, indicator: IndicatorInfo, codes: list[str], start: int, end: int
    ) -> dict[str, CountrySeries]:
        key = f"{indicator.source_id}:{','.join(sorted(codes))}:{start}:{end}"
        return await self._cache.get_or_load(
            key, lambda: self._client.get_indicator(indicator.source_id, codes, start, end)
        )

    def _source(self, indicator: IndicatorInfo, codes: list[str]) -> SourceInfo:
        return SourceInfo(
            name=SOURCE,
            url=self._client.indicator_url(indicator.source_id, codes),
            fetched_at=datetime.now(UTC),
        )

    async def series(
        self, indicator_key: str, codes: list[str], start: int, end: int
    ) -> MacroSeriesResponse:
        indicator = INDICATOR_BY_KEY[indicator_key]
        raw = await self._fetch(indicator, codes, start, end)
        out: list[CountrySeriesOut] = []
        for code in codes:
            country = COUNTRY_BY_CODE[code]
            found = raw.get(code)
            if found is None or not found.observations:
                out.append(
                    CountrySeriesOut(
                        country_code=code,
                        country_name=country.name,
                        status="NO_DISPONIBLE",
                        observations=[],
                        missing_periods=found.missing_periods if found else 0,
                        note="La fuente no publica este indicador para el país en el período.",
                    )
                )
                continue
            out.append(
                CountrySeriesOut(
                    country_code=code,
                    country_name=country.name,
                    status="OK",
                    observations=found.observations,
                    missing_periods=found.missing_periods,
                )
            )
        return MacroSeriesResponse(
            indicator=indicator,
            start_year=start,
            end_year=end,
            series=out,
            source=self._source(indicator, codes),
        )

    async def snapshot(self, codes: list[str]) -> MacroSnapshotResponse:
        """Último dato disponible de cada indicador para cada país.

        Se piden los últimos 10 años: el Banco Mundial publica con 1-2 años de rezago y algunos
        países se atrasan más. El año del dato se devuelve siempre junto al valor.
        """
        end = current_year()
        start = end - 10

        async def load(ind: IndicatorInfo) -> tuple[str, dict[str, CountrySeries] | str]:
            try:
                return ind.key, await self._fetch(ind, codes, start, end)
            except SourceError as exc:
                logger.warning("macro snapshot: falló %s: %s", ind.source_id, exc)
                return ind.key, exc.message

        results = dict(await asyncio.gather(*(load(i) for i in INDICATORS)))
        errors = {k: v for k, v in results.items() if isinstance(v, str)}

        countries: list[CountrySnapshot] = []
        for code in codes:
            values: dict[str, LatestValue] = {}
            for ind in INDICATORS:
                data = results[ind.key]
                if isinstance(data, str):
                    values[ind.key] = LatestValue(value=None, period=None, status="ERROR_FUENTE")
                    continue
                series = data.get(code)
                if series is None or not series.observations:
                    values[ind.key] = LatestValue(value=None, period=None, status="NO_DISPONIBLE")
                    continue
                last = series.observations[-1]
                values[ind.key] = LatestValue(value=last.value, period=last.period, status="OK")
            countries.append(CountrySnapshot(country=COUNTRY_BY_CODE[code], values=values))

        return MacroSnapshotResponse(
            indicators=INDICATORS,
            countries=countries,
            errors=errors,
            source=SourceInfo(
                name=SOURCE, url="https://data.worldbank.org/", fetched_at=datetime.now(UTC)
            ),
        )
