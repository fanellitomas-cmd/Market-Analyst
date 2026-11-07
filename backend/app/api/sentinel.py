from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import CurrentUser
from app.services.sentinel import (
    ArbitrageResponse,
    CedearRatio,
    CommoditiesResponse,
    CryptoResponse,
    SentinelService,
)
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/sentinel", tags=["sentinel"])
MAX_CEDEARS = 60


def get_sentinel(request: Request) -> SentinelService:
    service: SentinelService = request.app.state.services.sentinel
    return service


SentinelDep = Annotated[SentinelService, Depends(get_sentinel)]


@router.get("/crypto", response_model=CryptoResponse)
async def crypto(_: CurrentUser, service: SentinelDep) -> CryptoResponse:
    return await service.crypto()


@router.get("/commodities", response_model=CommoditiesResponse)
async def commodities(_: CurrentUser, service: SentinelDep) -> CommoditiesResponse:
    return await service.commodities()


@router.get("/arbitrage", response_model=ArbitrageResponse)
async def arbitrage(_: CurrentUser, service: SentinelDep) -> ArbitrageResponse:
    return await service.arbitrage()


@router.put("/cedears", response_model=list[CedearRatio])
async def set_cedears(
    _: CurrentUser, service: SentinelDep, body: list[CedearRatio]
) -> list[CedearRatio]:
    if len(body) > MAX_CEDEARS:
        raise HTTPException(422, f"Máximo {MAX_CEDEARS} CEDEARs.")
    clean: list[CedearRatio] = []
    for c in body:
        try:
            local, foreign = normalize_symbol(c.local), normalize_symbol(c.foreign)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not local.endswith(".BA"):
            raise HTTPException(422, f"{local}: el símbolo local debe terminar en .BA")
        if not 0 < c.ratio <= 10_000:
            raise HTTPException(422, f"{local}: ratio inválido")
        clean.append(CedearRatio(local=local, foreign=foreign, ratio=c.ratio))
    return await service.set_cedear_ratios(clean)
