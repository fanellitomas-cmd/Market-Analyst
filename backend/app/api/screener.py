from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import CurrentUser
from app.domain import StrictModel
from app.services.screener import ScreenerResponse, ScreenerService
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/screener", tags=["screener"])
MAX_UNIVERSE = 150


def get_screener(request: Request) -> ScreenerService:
    service: ScreenerService = request.app.state.services.screener
    return service


ScreenerDep = Annotated[ScreenerService, Depends(get_screener)]


class UniverseIn(StrictModel):
    tickers: list[str]


@router.get("", response_model=ScreenerResponse)
async def latest(_: CurrentUser, service: ScreenerDep) -> ScreenerResponse:
    """Resultados de la última corrida (la corre el agente; también desde Sistema)."""
    return await service.latest()


@router.put("/universe", response_model=list[str])
async def set_universe(_: CurrentUser, service: ScreenerDep, body: UniverseIn) -> list[str]:
    try:
        tickers = [normalize_symbol(t) for t in body.tickers if t.strip()]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not tickers:
        raise HTTPException(422, "El universo no puede quedar vacío.")
    if len(tickers) > MAX_UNIVERSE:
        raise HTTPException(422, f"Máximo {MAX_UNIVERSE} empresas.")
    return await service.set_universe(tickers)
