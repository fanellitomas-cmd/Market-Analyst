from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.services.technical import TechnicalResponse, TechnicalService
from app.sources.errors import NotFoundError, SourceError
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/technical", tags=["technical"])


def get_technical(request: Request) -> TechnicalService:
    service: TechnicalService = request.app.state.services.technical
    return service


@router.get("/{symbol}", response_model=TechnicalResponse)
async def technical(
    _: CurrentUser,
    service: Annotated[TechnicalService, Depends(get_technical)],
    symbol: str,
    anchor: date | None = None,
) -> TechnicalResponse:
    """Régimen, AVWAPs y perfil de volumen. `anchor` agrega un AVWAP desde esa fecha."""
    try:
        sym = normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return await service.analyze(sym, anchor)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
