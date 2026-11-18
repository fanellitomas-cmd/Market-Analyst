from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.domain import StrictModel
from app.services.thesis import ThesisIn, ThesisOut, ThesisService
from app.sources.errors import NotFoundError, SourceError
from app.sources.yahoo import normalize_symbol

router = APIRouter(prefix="/theses", tags=["theses"])


def get_theses(request: Request) -> ThesisService:
    service: ThesisService = request.app.state.services.theses
    return service


ThesesDep = Annotated[ThesisService, Depends(get_theses)]


class CloseIn(StrictModel):
    reason: str = ""


@router.get("", response_model=list[ThesisOut])
async def list_theses(_: CurrentUser, service: ThesesDep) -> list[ThesisOut]:
    return await service.all()


@router.post("", response_model=ThesisOut, status_code=status.HTTP_201_CREATED)
async def create(_: CurrentUser, service: ThesesDep, body: ThesisIn) -> ThesisOut:
    try:
        ticker = normalize_symbol(body.ticker)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(body.hypothesis.strip()) < 10:
        raise HTTPException(422, "Escribí la hipótesis de desalineación (al menos una oración).")
    if body.support <= 0 or not -100 < body.min_operating_margin < 100:
        raise HTTPException(422, "El soporte debe ser positivo y el margen un % entre −100 y 100.")
    try:
        return await service.create(body.model_copy(update={"ticker": ticker}))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except SourceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fuente no disponible: {exc}") from exc


@router.post("/{thesis_id}/check", response_model=ThesisOut)
async def check(_: CurrentUser, service: ThesesDep, thesis_id: int) -> ThesisOut:
    try:
        out, _reason = await service.check(thesis_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tesis inexistente.") from exc
    return out


@router.post("/{thesis_id}/close", response_model=ThesisOut)
async def close(_: CurrentUser, service: ThesesDep, thesis_id: int, body: CloseIn) -> ThesisOut:
    try:
        return await service.close(thesis_id, body.reason)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tesis inexistente.") from exc


@router.delete("/{thesis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(_: CurrentUser, service: ThesesDep, thesis_id: int) -> None:
    try:
        await service.delete(thesis_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tesis inexistente.") from exc
