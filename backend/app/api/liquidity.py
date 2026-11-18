from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.services.liquidity import (
    SPEC_BY_KEY,
    LiquidityDashboard,
    LiquiditySeries,
    LiquidityService,
)
from app.sources.errors import NotFoundError, SourceError

router = APIRouter(prefix="/liquidity", tags=["liquidity"])
_EARLIEST = date(2000, 1, 1)


def get_liquidity(request: Request) -> LiquidityService:
    service: LiquidityService = request.app.state.services.liquidity
    return service


LiqDep = Annotated[LiquidityService, Depends(get_liquidity)]


@router.get("/dashboard", response_model=LiquidityDashboard)
async def dashboard(_: CurrentUser, service: LiqDep) -> LiquidityDashboard:
    return await service.dashboard()


@router.get("/series", response_model=LiquiditySeries)
async def series(
    _: CurrentUser, service: LiqDep, key: str, start: date = _EARLIEST
) -> LiquiditySeries:
    if key not in SPEC_BY_KEY:
        raise HTTPException(422, "Indicador desconocido.")
    try:
        return await service.series(key, max(start, _EARLIEST))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
