"""Tareas programadas del agente.

Cada tarea se define con un `JobSpec`: horario (en la zona de `AGENT_TIMEZONE`, por defecto
Buenos Aires), si sólo corre en días hábiles de la bolsa de Nueva York, y la función que hace el
trabajo. `run_job` deja registro de cada corrida y manda la notificación si la tarea la pide.

Principio "sin ruido": una tarea sólo notifica cuando hay algo que decir (por ejemplo, una fuente
que dejó de responder), salvo el latido semanal, que existe para confirmar que el agente vive.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.container import Services
from app.core.db import JobRun, SystemState, utcnow
from app.core.market_calendar import is_trading_day
from app.sources.errors import SourceError
from app.sources.fred import FredSeries

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class JobResult:
    detail: str
    notify: str | None = None  # texto a enviar; None = no molestar
    failed: bool = False


@dataclass(frozen=True)
class JobSpec:
    id: str
    name: str
    description: str
    schedule: str  # descripción legible del horario
    cron: dict[str, Any]  # argumentos de CronTrigger (en la zona del agente)
    func: Callable[[Services], Awaitable[JobResult]]
    trading_days_only: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


# ---------- Tarea: salud de las fuentes ----------


SOURCE_TIMEOUT = 25.0


async def _check(name: str, coro: Awaitable[Any]) -> tuple[str, str | None]:
    try:
        await asyncio.wait_for(coro, timeout=SOURCE_TIMEOUT)
        return name, None
    except TimeoutError:
        return name, "no respondió a tiempo"
    except (SourceError, ValueError) as exc:
        return name, str(exc)


async def source_health(services: Services) -> JobResult:
    c = services.clients
    today = datetime.now(UTC).date()
    checks: list[tuple[str, Awaitable[Any]]] = [
        ("Yahoo Finance", c.yahoo.get_daily_prices("SPY", "6mo")),
        ("FRED", _fred_recent(services, today)),
        (
            "Banco Mundial",
            c.worldbank.get_indicator("FP.CPI.TOTL.ZG", ["USA"], today.year - 3, today.year),
        ),
        ("BCRA", c.bcra.list_variables()),
        ("DolarApi", c.dolarapi.get_quotes()),
        ("ArgentinaDatos", c.argdatos.get_country_risk_history()),
        ("ForexFactory", c.forexfactory.this_week()),
        ("OKX", c.okx.funding("BTC-USDT-SWAP")),
    ]
    if c.sec.configured:
        checks.append(("SEC EDGAR", c.sec.ticker_map()))
    results = await asyncio.gather(*(_check(n, coro) for n, coro in checks))
    failures = [(n, err) for n, err in results if err is not None]
    ok = len(results) - len(failures)
    if not failures:
        return JobResult(detail=f"{ok}/{len(results)} fuentes responden")
    lines = "\n".join(f"• {n}: {err}" for n, err in failures)
    return JobResult(
        detail=f"{ok}/{len(results)} fuentes responden; fallan: "
        + ", ".join(n for n, _ in failures),
        notify=f"⚠️ Market Analyst: {len(failures)} fuente(s) sin responder\n{lines}",
        failed=True,
    )


async def _fred_recent(services: Services, today: date) -> FredSeries:
    series = await services.clients.fred.get_series("DGS10", today - timedelta(days=30))
    if not series.values:
        raise SourceError("FRED", "la serie de control vino vacía")
    return series


# ---------- Tarea: latido semanal ----------


async def weekly_heartbeat(services: Services) -> JobResult:
    since = utcnow() - timedelta(days=7)
    async with services.sessions() as session:
        rows = (
            await session.execute(
                select(JobRun.status, func.count())
                .where(JobRun.started_at >= since, JobRun.job_id != "latido_semanal")
                .group_by(JobRun.status)
            )
        ).all()
    counts = {status: n for status, n in rows}
    text = (
        "✅ Market Analyst sigue activo.\n"
        f"Últimos 7 días: {counts.get('ok', 0)} tareas OK, {counts.get('error', 0)} con error, "
        f"{counts.get('skipped', 0)} salteadas (feriados)."
    )
    return JobResult(detail=text.replace("\n", " "), notify=text)


# ---------- Tareas: calendario económico ----------


async def econ_calendar_refresh(services: Services) -> JobResult:
    new, updated = await services.calendar.refresh()
    return JobResult(detail=f"{new} eventos nuevos, {updated} pronósticos actualizados")


async def econ_actuals(services: Services) -> JobResult:
    surprises = await services.calendar.capture_actuals()
    if not surprises:
        return JobResult(detail="sin sorpresas relevantes nuevas")
    return JobResult(
        detail=f"{len(surprises)} sorpresa(s): " + ", ".join(s.spec.name for s in surprises),
        notify="\n".join(s.message() for s in surprises),
    )


# ---------- Tarea: screener GARP ----------


async def garp_screener(services: Services) -> JobResult:
    if not services.forensic.configured:
        return JobResult(detail="sin SEC_USER_AGENT: el screener no puede leer balances")
    rows, entered = await services.screener.run()
    ok = sum(1 for r in rows if r.error is None)
    passing = [r.ticker for r in rows if r.passes]
    detail = f"{ok}/{len(rows)} empresas analizadas; cumplen todo: " + (
        ", ".join(passing) if passing else "ninguna"
    )
    if not entered:
        return JobResult(detail=detail)
    lines = []
    for r in rows:
        if r.ticker in entered:
            facts = " · ".join(c.detail for c in r.criteria)
            lines.append(f"• {r.ticker} ({r.name}): {facts}")
    return JobResult(
        detail=detail,
        notify="🔎 Screener GARP: nuevas empresas que cumplen los 4 criterios\n" + "\n".join(lines),
    )


JOBS: list[JobSpec] = [
    JobSpec(
        id="salud_fuentes",
        name="Chequeo de fuentes",
        description="Verifica que cada fuente de datos responda. Avisa sólo si alguna falla.",
        schedule="Días hábiles de NY, 07:15",
        cron={"day_of_week": "mon-fri", "hour": 7, "minute": 15},
        func=source_health,
        trading_days_only=True,
    ),
    JobSpec(
        id="calendario_economico",
        name="Calendario económico",
        description="Guarda los datos de la semana con su pronóstico (ForexFactory).",
        schedule="Todos los días, 06:30",
        cron={"hour": 6, "minute": 30},
        func=econ_calendar_refresh,
    ),
    JobSpec(
        id="datos_publicados",
        name="Datos publicados y sorpresas",
        description="Toma el dato real de FRED apenas sale y avisa si se desvía del pronóstico "
        "más que el umbral de cada indicador.",
        schedule="Días hábiles, 10:05, 11:05, 12:05 y 17:05",
        cron={"day_of_week": "mon-fri", "hour": "10,11,12,17", "minute": 5},
        func=econ_actuals,
    ),
    JobSpec(
        id="screener_garp",
        name="Screener GARP",
        description="Analiza el universo de empresas con el motor forense y avisa sólo cuando una "
        "empresa entra al filtro.",
        schedule="Días hábiles, 21:00",
        cron={"day_of_week": "mon-fri", "hour": 21, "minute": 0},
        func=garp_screener,
    ),
    JobSpec(
        id="latido_semanal",
        name="Latido semanal",
        description="Confirma por WhatsApp que el agente está vivo, con un resumen de la semana.",
        schedule="Lunes 08:00",
        cron={"day_of_week": "mon", "hour": 8, "minute": 0},
        func=weekly_heartbeat,
    ),
]
JOBS_BY_ID = {j.id: j for j in JOBS}


async def run_job(
    spec: JobSpec, services: Services, trigger: str = "programada", today: date | None = None
) -> JobRun:
    """Ejecuta una tarea con registro de auditoría. Nunca deja escapar excepciones."""
    async with services.sessions() as session:
        run = JobRun(job_id=spec.id, status="running", detail=f"ejecución {trigger}")
        session.add(run)
        await session.commit()
        await session.refresh(run)

    status, detail, notify = "ok", "", None
    ny_today = today or datetime.now(NY).date()
    if spec.trading_days_only and not is_trading_day(ny_today):
        status, detail = "skipped", f"{ny_today.isoformat()} no es día hábil en NYSE"
    else:
        try:
            result = await spec.func(services)
            status = "error" if result.failed else "ok"
            detail, notify = result.detail, result.notify
        except Exception as exc:  # una tarea rota no debe tumbar al agente
            logger.exception("tarea %s falló", spec.id)
            status, detail = "error", f"{type(exc).__name__}: {exc}"
            notify = f"❌ Market Analyst: la tarea “{spec.name}” falló ({type(exc).__name__})."

    if notify:
        sent = await services.notifier.send(notify, trigger=spec.id)
        label = {"sent": "enviada", "failed": "falló", "disabled": "sin canal configurado"}
        detail += f" · notificación: {label.get(sent.status, sent.status)}"

    async with services.sessions() as session:
        db_run = await session.get(JobRun, run.id)
        assert db_run is not None
        db_run.status = status
        db_run.detail = detail
        db_run.finished_at = utcnow()
        await session.commit()
        await session.refresh(db_run)
        return db_run


HEARTBEAT_KEY = "worker_heartbeat"


async def write_heartbeat(services: Services) -> None:
    async with services.sessions() as session:
        row = await session.get(SystemState, HEARTBEAT_KEY)
        now = utcnow()
        if row is None:
            session.add(SystemState(key=HEARTBEAT_KEY, value=now.isoformat(), updated_at=now))
        else:
            row.value = now.isoformat()
            row.updated_at = now
        await session.commit()
