from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.services.earnings import EarningsResponse, EarningsService
from app.sources.errors import NotFoundError, SourceError
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/earnings", tags=["earnings"])


def get_earnings_service(request: Request) -> EarningsService:
    service: EarningsService = request.app.state.earnings_service
    return service


EarningsDep = Annotated[EarningsService, Depends(get_earnings_service)]


@router.get("/{ticker}", response_model=EarningsResponse)
async def earnings(_: CurrentUser, service: EarningsDep, ticker: str) -> EarningsResponse:
    if not service.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Falta configurar SEC_USER_AGENT en backend/.env (la SEC pide nombre y email de "
            "contacto en cada consulta).",
        )
    try:
        symbol = normalize_symbol(ticker)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return await service.analyze(symbol)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
