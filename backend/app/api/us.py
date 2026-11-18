from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.services.us_macro import (
    INDICATOR_BY_KEY,
    INDICATORS,
    UsDashboardResponse,
    UsIndicatorInfo,
    UsMacroService,
    UsSeriesResponse,
    YieldCurveResponse,
    info,
)
from app.sources.errors import NotFoundError, SourceError

router = APIRouter(prefix="/us", tags=["us"])


def get_us_service(request: Request) -> UsMacroService:
    service: UsMacroService = request.app.state.us_service
    return service


UsDep = Annotated[UsMacroService, Depends(get_us_service)]
_EARLIEST = date(2000, 1, 1)


@router.get("/indicators", response_model=list[UsIndicatorInfo])
def list_indicators(_: CurrentUser) -> list[UsIndicatorInfo]:
    return [info(s) for s in INDICATORS]


@router.get("/dashboard", response_model=UsDashboardResponse)
async def dashboard(_: CurrentUser, service: UsDep) -> UsDashboardResponse:
    return await service.dashboard()


@router.get("/series", response_model=UsSeriesResponse)
async def series(
    _: CurrentUser, service: UsDep, indicator: str, start: date = _EARLIEST
) -> UsSeriesResponse:
    if indicator not in INDICATOR_BY_KEY:
        raise HTTPException(422, "Indicador desconocido.")
    try:
        return await service.series(indicator, max(start, _EARLIEST))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc


@router.get("/yield-curve", response_model=YieldCurveResponse)
async def yield_curve(_: CurrentUser, service: UsDep) -> YieldCurveResponse:
    try:
        return await service.yield_curve()
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
