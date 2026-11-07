from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.domain import StrictModel
from app.services.options import OptionsResponse, OptionsService
from app.sources.errors import NotFoundError, SourceError
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/options", tags=["options"])
MAX_WATCHLIST = 30


def get_options(request: Request) -> OptionsService:
    service: OptionsService = request.app.state.services.options
    return service


OptionsDep = Annotated[OptionsService, Depends(get_options)]


class WatchlistIn(StrictModel):
    symbols: list[str]


@router.get("/watchlist", response_model=list[str])
async def get_watchlist(_: CurrentUser, service: OptionsDep) -> list[str]:
    return await service.watchlist()


@router.put("/watchlist", response_model=list[str])
async def put_watchlist(_: CurrentUser, service: OptionsDep, body: WatchlistIn) -> list[str]:
    try:
        symbols = [normalize_symbol(s) for s in body.symbols if s.strip()]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not symbols or len(symbols) > MAX_WATCHLIST:
        raise HTTPException(422, f"Entre 1 y {MAX_WATCHLIST} símbolos.")
    return await service.set_watchlist(symbols)


@router.get("/{symbol}", response_model=OptionsResponse)
async def analyze(_: CurrentUser, service: OptionsDep, symbol: str) -> OptionsResponse:
    try:
        sym = normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return await service.analyze(sym)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
