from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUser
from app.domain import StrictModel
from app.services.cadence import CadenceService, LedgerReport, PremarketReport, TriggerOut

router = APIRouter(prefix="/cadence", tags=["cadence"])


def get_cadence(request: Request) -> CadenceService:
    service: CadenceService = request.app.state.services.cadence
    return service


CadenceDep = Annotated[CadenceService, Depends(get_cadence)]


class TodayResponse(StrictModel):
    watchlist: list[str]
    premarket: PremarketReport | None
    ledger: LedgerReport | None
    triggers: list[TriggerOut]


@router.get("/today", response_model=TodayResponse)
async def today(_: CurrentUser, service: CadenceDep) -> TodayResponse:
    """Últimos entregables de la cadencia. Se generan con las tareas del agente."""
    return TodayResponse(
        watchlist=await service.watchlist(),
        premarket=await service.latest_premarket(),
        ledger=await service.latest_ledger(),
        triggers=await service.recent_triggers(),
    )
