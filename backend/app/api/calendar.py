from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUser
from app.services.econ_calendar import CalendarResponse, EconCalendarService

router = APIRouter(prefix="/calendar", tags=["calendar"])


def get_calendar(request: Request) -> EconCalendarService:
    service: EconCalendarService = request.app.state.services.calendar
    return service


@router.get("", response_model=CalendarResponse)
async def calendar(
    _: CurrentUser, service: Annotated[EconCalendarService, Depends(get_calendar)]
) -> CalendarResponse:
    """Próximos datos (8 días) y sorpresas recientes. Se llena con las tareas del agente."""
    return await service.calendar()
