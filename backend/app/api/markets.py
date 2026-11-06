from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, get_markets_service
from app.schemas import CompareResponse, PriceRange, RelativeAnalysis
from app.services.markets import MAX_COMPARE_SYMBOLS, MarketsService
from app.sources.errors import NotFoundError, SourceError
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/markets", tags=["markets"])
MarketsDep = Annotated[MarketsService, Depends(get_markets_service)]


def _symbol(raw: str) -> str:
    try:
        return normalize_symbol(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/relative", response_model=RelativeAnalysis)
async def relative(
    _: CurrentUser,
    service: MarketsDep,
    symbol: str,
    benchmark: str = "SPY",
    range: PriceRange = "1y",
) -> RelativeAnalysis:
    try:
        return await service.relative(_symbol(symbol), _symbol(benchmark), range)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc


@router.get("/compare", response_model=CompareResponse)
async def compare(
    _: CurrentUser,
    service: MarketsDep,
    symbols: Annotated[str, Query(description="Símbolos separados por coma")],
    benchmark: str = "SPY",
    range: PriceRange = "1y",
) -> CompareResponse:
    parsed = list(dict.fromkeys(_symbol(s) for s in symbols.split(",") if s.strip()))
    if not parsed:
        raise HTTPException(422, "Indicá al menos un símbolo.")
    if len(parsed) > MAX_COMPARE_SYMBOLS:
        raise HTTPException(
            422,
            f"Máximo {MAX_COMPARE_SYMBOLS} símbolos por comparación.",
        )
    try:
        return await service.compare(parsed, _symbol(benchmark), range)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
