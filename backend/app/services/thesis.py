"""Tesis de inversión con el protocolo de la visión y su kill switch doble.

Estructura obligatoria de cada tesis:
  1. Hipótesis de desalineación (texto del usuario).
  2. Validación fundamental: quick ratio > 1,2, margen operativo del último trimestre por encima
     del mínimo de la tesis y Altman fuera de la zona de riesgo.
  3. Confirmación técnica: precio sobre el soporte estructural y sobre el AVWAP del último balance.
  4. Kill switch doble (se evalúa al cierre de cada rueda):
     - Fundamental: el PRIMER balance publicado después de crear la tesis muestra un margen
       operativo por debajo del mínimo.
     - Técnico: cierre debajo del soporte con volumen (≥ 1,5 × el promedio de 50 ruedas), o
       cierre debajo del AVWAP del último balance. Sin promediar a la baja: la tesis se invalida.

Un dato que falta nunca dispara el kill switch: queda "sin dato" y se informa.
"""

import logging
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import Thesis, utcnow
from app.domain import StrictModel
from app.services.earnings import EarningsService, build_quarters
from app.services.forensic import ForensicService
from app.services.technical import TechnicalResponse, TechnicalService
from app.sources.errors import NotFoundError, SourceError

logger = logging.getLogger(__name__)

QUICK_MIN = 1.2
BREAK_VOLUME = 1.5  # volumen relativo mínimo para considerar "confirmada" una ruptura

Status = Literal["cumple", "no cumple", "sin dato"]


class CheckItem(StrictModel):
    group: Literal["fundamental", "técnica"]
    name: str
    status: Status
    detail: str


class KillSwitch(StrictModel):
    triggered: bool
    detail: str


class ThesisCheck(StrictModel):
    checked_at: datetime
    close: float | None
    regime: str | None
    checks: list[CheckItem]
    kill_fundamental: KillSwitch
    kill_technical: KillSwitch
    warnings: list[str]


class ThesisIn(StrictModel):
    ticker: str
    hypothesis: str
    min_operating_margin: float  # en %
    support: float
    use_earnings_avwap: bool = True
    entry_price: float | None = None
    notes: str | None = None


class ThesisOut(StrictModel):
    id: int
    created_at: datetime
    ticker: str
    status: str
    hypothesis: str
    min_operating_margin: float
    support: float
    use_earnings_avwap: bool
    entry_price: float | None
    notes: str | None
    baseline_quarter_end: date | None
    last_check: ThesisCheck | None
    closed_at: datetime | None
    close_reason: str | None


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def to_out(t: Thesis) -> ThesisOut:
    return ThesisOut(
        id=t.id,
        created_at=_aware(t.created_at) or utcnow(),
        ticker=t.ticker,
        status=t.status,
        hypothesis=t.hypothesis,
        min_operating_margin=t.min_operating_margin,
        support=t.support,
        use_earnings_avwap=t.use_earnings_avwap,
        entry_price=t.entry_price,
        notes=t.notes,
        baseline_quarter_end=t.baseline_quarter_end,
        last_check=ThesisCheck.model_validate_json(t.last_check) if t.last_check else None,
        closed_at=_aware(t.closed_at),
        close_reason=t.close_reason,
    )


def evaluate(
    t: Thesis,
    tech: TechnicalResponse | None,
    quick: float | None,
    altman_zone: str | None,
    quarter: tuple[date, float] | None,  # (cierre del último trimestre, margen operativo)
) -> ThesisCheck:
    checks: list[CheckItem] = []
    warnings: list[str] = []
    min_margin = t.min_operating_margin / 100

    def add(
        group: Literal["fundamental", "técnica"], name: str, ok: bool | None, detail: str
    ) -> None:
        status: Status = "sin dato" if ok is None else "cumple" if ok else "no cumple"
        checks.append(CheckItem(group=group, name=name, status=status, detail=detail))

    add(
        "fundamental",
        f"Quick ratio > {QUICK_MIN}".replace(".", ","),
        None if quick is None else quick > QUICK_MIN,
        "sin balance de la SEC" if quick is None else f"{quick:.2f}",
    )
    add(
        "fundamental",
        f"Margen operativo ≥ {t.min_operating_margin:g} %",
        None if quarter is None else quarter[1] >= min_margin,
        "sin trimestres en la SEC"
        if quarter is None
        else f"{quarter[1] * 100:.1f} % (trimestre al {quarter[0].isoformat()})",
    )
    add(
        "fundamental",
        "Altman fuera de la zona de riesgo",
        None if altman_zone is None else altman_zone != "riesgo de quiebra",
        altman_zone or "sin dato",
    )

    close = tech.close if tech else None
    avwap = next((a for a in tech.avwaps if a.key == "earnings"), None) if tech else None
    add(
        "técnica",
        f"Precio sobre el soporte ({t.support:g})",
        None if close is None else close >= t.support,
        "sin precio" if close is None else f"cierre {close:.2f}",
    )
    if t.use_earnings_avwap:
        av = avwap.value if avwap and avwap.status == "OK" else None
        add(
            "técnica",
            "Precio sobre el AVWAP del último balance",
            None if close is None or av is None else close >= av,
            ((avwap.note if avwap else None) or "sin AVWAP") if av is None else f"AVWAP {av:.2f}",
        )

    # --- Kill switch fundamental: sólo con un balance NUEVO (posterior a la tesis) ---
    kill_f = KillSwitch(triggered=False, detail="sin balance nuevo desde que se creó la tesis")
    if quarter is not None and (
        t.baseline_quarter_end is None or quarter[0] > t.baseline_quarter_end
    ):
        if t.baseline_quarter_end is not None and quarter[1] < min_margin:
            kill_f = KillSwitch(
                triggered=True,
                detail=f"el balance al {quarter[0].isoformat()} mostró margen operativo de "
                f"{quarter[1] * 100:.1f} %, debajo del mínimo de {t.min_operating_margin:g} %",
            )
        elif t.baseline_quarter_end is not None:
            kill_f = KillSwitch(
                triggered=False,
                detail=f"balance al {quarter[0].isoformat()} "
                f"dentro del umbral ({quarter[1] * 100:.1f} %)",
            )

    # --- Kill switch técnico: cierre bajo soporte con volumen, o bajo el AVWAP del balance ---
    kill_t = KillSwitch(triggered=False, detail="sin ruptura")
    rel_vol = tech.volume.relative if tech else None
    if close is not None and close < t.support:
        if rel_vol is not None and rel_vol >= BREAK_VOLUME:
            kill_t = KillSwitch(
                triggered=True,
                detail=f"cierre {close:.2f} debajo del soporte "
                f"{t.support:g} con volumen {rel_vol:.1f}× el promedio",
            )
        else:
            vol = "sin dato de volumen" if rel_vol is None else f"volumen {rel_vol:.1f}×"
            warnings.append(f"perforó el soporte sin volumen que lo confirme ({vol}): vigilar")
    if (
        not kill_t.triggered
        and t.use_earnings_avwap
        and close is not None
        and avwap
        and avwap.status == "OK"
        and avwap.value is not None
        and close < avwap.value
    ):
        kill_t = KillSwitch(
            triggered=True,
            detail=f"cierre {close:.2f} debajo del AVWAP del último balance ({avwap.value:.2f})",
        )

    return ThesisCheck(
        checked_at=utcnow(),
        close=close,
        regime=tech.regime.label if tech else None,
        checks=checks,
        kill_fundamental=kill_f,
        kill_technical=kill_t,
        warnings=warnings,
    )


class ThesisService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        technical: TechnicalService,
        forensic: ForensicService,
        earnings: EarningsService,
    ) -> None:
        self._sessions = sessions
        self._technical = technical
        self._forensic = forensic
        self._earnings = earnings

    async def all(self) -> list[ThesisOut]:
        async with self._sessions() as session:
            rows = (
                (await session.execute(select(Thesis).order_by(Thesis.id.desc()))).scalars().all()
            )
        return [to_out(t) for t in rows]

    async def active_tickers(self) -> list[str]:
        async with self._sessions() as session:
            rows = (
                (await session.execute(select(Thesis.ticker).where(Thesis.status == "activa")))
                .scalars()
                .all()
            )
        return sorted(set(rows))

    async def _latest_quarter(self, ticker: str) -> tuple[date, float] | None:
        if not self._earnings.configured:
            return None
        try:
            ref = await self._earnings.resolve(ticker)
            quarters = build_quarters(await self._earnings.company_facts(ref))
        except SourceError:
            return None
        for q in quarters:  # el más reciente con margen operativo
            if q.operating_margin.value is not None:
                return q.period_end, q.operating_margin.value
        return None

    async def _inputs(
        self, ticker: str
    ) -> tuple[TechnicalResponse | None, float | None, str | None, tuple[date, float] | None]:
        tech: TechnicalResponse | None = None
        try:
            tech = await self._technical.analyze(ticker)
        except SourceError as exc:
            logger.warning("tesis %s: sin técnico: %s", ticker, exc)
        quick = altman = None
        if self._forensic.configured:
            try:
                f = await self._forensic.analyze(ticker)
                if f.years:
                    quick, altman = f.years[0].quick_ratio, f.years[0].altman.zone
            except SourceError as exc:
                logger.warning("tesis %s: sin forense: %s", ticker, exc)
        return tech, quick, altman, await self._latest_quarter(ticker)

    async def create(self, data: ThesisIn) -> ThesisOut:
        tech, quick, altman, quarter = await self._inputs(data.ticker)
        if tech is None:
            raise NotFoundError("Yahoo Finance", f"no hay precios para {data.ticker}")
        t = Thesis(
            ticker=data.ticker,
            hypothesis=data.hypothesis.strip(),
            min_operating_margin=data.min_operating_margin,
            support=data.support,
            use_earnings_avwap=data.use_earnings_avwap,
            entry_price=data.entry_price,
            notes=data.notes,
            baseline_quarter_end=quarter[0] if quarter else None,
        )
        check = evaluate(t, tech, quick, altman, quarter)
        t.last_check, t.last_check_at = check.model_dump_json(), check.checked_at
        async with self._sessions() as session:
            session.add(t)
            await session.commit()
            await session.refresh(t)
        return to_out(t)

    async def check(self, thesis_id: int) -> tuple[ThesisOut, str | None]:
        """Reevalúa la tesis. Devuelve la tesis y, si se invalidó ahora, el motivo."""
        async with self._sessions() as session:
            t = await session.get(Thesis, thesis_id)
            if t is None:
                raise NotFoundError("tesis", "no existe")
            ticker = t.ticker
        tech, quick, altman, quarter = await self._inputs(ticker)
        async with self._sessions() as session:
            t = await session.get(Thesis, thesis_id)
            assert t is not None
            check = evaluate(t, tech, quick, altman, quarter)
            t.last_check, t.last_check_at = check.model_dump_json(), check.checked_at
            reason = None
            if t.status == "activa":
                fired = [
                    k.detail for k in (check.kill_fundamental, check.kill_technical) if k.triggered
                ]
                if fired:
                    reason = "; ".join(fired)
                    t.status, t.closed_at, t.close_reason = "invalidada", utcnow(), reason
            await session.commit()
            await session.refresh(t)
            return to_out(t), reason

    async def check_all_active(self) -> list[tuple[ThesisOut, str]]:
        async with self._sessions() as session:
            ids = (
                (await session.execute(select(Thesis.id).where(Thesis.status == "activa")))
                .scalars()
                .all()
            )
        invalidated: list[tuple[ThesisOut, str]] = []
        for thesis_id in ids:
            out, reason = await self.check(thesis_id)
            if reason:
                invalidated.append((out, reason))
        return invalidated

    async def close(self, thesis_id: int, reason: str) -> ThesisOut:
        async with self._sessions() as session:
            t = await session.get(Thesis, thesis_id)
            if t is None:
                raise NotFoundError("tesis", "no existe")
            if t.status == "activa":
                t.status, t.closed_at, t.close_reason = (
                    "cerrada",
                    utcnow(),
                    reason or "cerrada a mano",
                )
            await session.commit()
            await session.refresh(t)
            return to_out(t)

    async def delete(self, thesis_id: int) -> None:
        async with self._sessions() as session:
            t = await session.get(Thesis, thesis_id)
            if t is None:
                raise NotFoundError("tesis", "no existe")
            await session.delete(t)
            await session.commit()
