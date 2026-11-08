from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, get_macro_service
from app.schemas import CountryInfo, IndicatorInfo, MacroSeriesResponse, MacroSnapshotResponse
from app.services.macro import (
    COUNTRIES,
    COUNTRY_BY_CODE,
    DEFAULT_START_YEAR,
    INDICATOR_BY_KEY,
    INDICATORS,
    MacroService,
    current_year,
)
from app.sources.errors import SourceError

router = APIRouter(prefix="/macro", tags=["macro"])
MacroDep = Annotated[MacroService, Depends(get_macro_service)]


def parse_countries(raw: str) -> list[str]:
    codes = list(dict.fromkeys(c.strip().upper() for c in raw.split(",") if c.strip()))
    if not codes:
        raise HTTPException(422, "Elegí al menos un país.")
    unknown = [c for c in codes if c not in COUNTRY_BY_CODE]
    if unknown:
        raise HTTPException(422, f"Países no soportados: {', '.join(unknown)}")
    return codes


@router.get("/countries", response_model=list[CountryInfo])
def list_countries(_: CurrentUser) -> list[CountryInfo]:
    return COUNTRIES


@router.get("/indicators", response_model=list[IndicatorInfo])
def list_indicators(_: CurrentUser) -> list[IndicatorInfo]:
    return INDICATORS


@router.get("/series", response_model=MacroSeriesResponse)
async def get_series(
    _: CurrentUser,
    service: MacroDep,
    indicator: str,
    countries: Annotated[str, Query(description="Códigos ISO3 separados por coma")] = "USA,ARG",
    start: Annotated[int, Query(ge=1960)] = DEFAULT_START_YEAR,
    end: Annotated[int | None, Query(ge=1960)] = None,
) -> MacroSeriesResponse:
    if indicator not in INDICATOR_BY_KEY:
        raise HTTPException(422, "Indicador desconocido.")
    end_year = min(end or current_year(), current_year())
    if start > end_year:
        raise HTTPException(422, "El año inicial es posterior al final.")
    try:
        return await service.series(indicator, parse_countries(countries), start, end_year)
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc


@router.get("/snapshot", response_model=MacroSnapshotResponse)
async def get_snapshot(
    _: CurrentUser,
    service: MacroDep,
    countries: str = "USA,ARG,BRA,MEX,CHL,EMU,CHN,JPN",
) -> MacroSnapshotResponse:
    return await service.snapshot(parse_countries(countries))
