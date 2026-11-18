from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUser
from app.services.live import LiveService, LiveStrip

router = APIRouter(prefix="/live", tags=["live"])


def get_live(request: Request) -> LiveService:
    service: LiveService = request.app.state.services.live
    return service


@router.get("/strip", response_model=LiveStrip)
async def strip(_: CurrentUser, service: Annotated[LiveService, Depends(get_live)]) -> LiveStrip:
    """Cinta de precios clave. El frontend la consulta cada 30 segundos."""
    return await service.strip()
