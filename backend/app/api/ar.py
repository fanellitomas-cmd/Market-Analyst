from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.services.ar_macro import (
    CATALOG_BY_KEY,
    ArDashboardResponse,
    ArIndicatorInfo,
    ArMacroService,
    ArSeriesResponse,
    series_catalog,
)
from app.sources.errors import NotFoundError, SourceError

router = APIRouter(prefix="/ar", tags=["argentina"])
_EARLIEST = date(2010, 1, 1)


def get_ar_service(request: Request) -> ArMacroService:
    service: ArMacroService = request.app.state.ar_service
    return service


ArDep = Annotated[ArMacroService, Depends(get_ar_service)]


@router.get("/indicators", response_model=list[ArIndicatorInfo])
def list_indicators(_: CurrentUser) -> list[ArIndicatorInfo]:
    return series_catalog()


@router.get("/dashboard", response_model=ArDashboardResponse)
async def dashboard(_: CurrentUser, service: ArDep) -> ArDashboardResponse:
    return await service.dashboard()


@router.get("/series", response_model=ArSeriesResponse)
async def series(
    _: CurrentUser, service: ArDep, indicator: str, start: date = _EARLIEST
) -> ArSeriesResponse:
    if indicator not in CATALOG_BY_KEY:
        raise HTTPException(422, "Indicador desconocido.")
    try:
        return await service.series(indicator, max(start, _EARLIEST))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
