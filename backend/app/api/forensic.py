from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import CurrentUser
from app.services.forensic import ForensicResponse, ForensicService
from app.sources.errors import NotFoundError, SourceError
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/forensic", tags=["forensic"])


def get_forensic(request: Request) -> ForensicService:
    service: ForensicService = request.app.state.services.forensic
    return service


@router.get("/{ticker}", response_model=ForensicResponse)
async def forensic(
    _: CurrentUser,
    service: Annotated[ForensicService, Depends(get_forensic)],
    ticker: str,
    erp: Annotated[
        float, Query(ge=0, le=15, description="Prima de riesgo del mercado, en %")
    ] = 5.0,
) -> ForensicResponse:
    if not service.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Falta configurar SEC_USER_AGENT en backend/.env (la SEC pide nombre y email).",
        )
    try:
        symbol = normalize_symbol(ticker)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return await service.analyze(symbol, erp)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc
