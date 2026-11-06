"""Estado del agente 24/7: tareas programadas, latido del proceso y notificaciones."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.agent.jobs import HEARTBEAT_KEY, JOBS, JOBS_BY_ID, run_job
from app.api.deps import CurrentUser
from app.container import Services
from app.core.db import JobRun, NotificationLog, SystemState, utcnow
from app.domain import StrictModel

router = APIRouter(prefix="/system", tags=["system"])
WORKER_STALE_AFTER = timedelta(minutes=3)


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


class JobRunOut(StrictModel):
    id: int
    job_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    detail: str | None


class JobOut(StrictModel):
    id: str
    name: str
    description: str
    schedule: str
    trading_days_only: bool
    next_run: datetime | None
    last_run: JobRunOut | None


class NotificationOut(StrictModel):
    id: int
    created_at: datetime
    channel: str
    trigger: str
    text: str
    status: str
    error: str | None


class SystemStatus(StrictModel):
    timezone: str
    now: datetime
    worker_last_heartbeat: datetime | None
    worker_alive: bool
    notifier_channel: str
    notifier_configured: bool
    jobs: list[JobOut]
    notifications: list[NotificationOut]


def _run_out(r: JobRun) -> JobRunOut:
    return JobRunOut(
        id=r.id,
        job_id=r.job_id,
        started_at=_aware(r.started_at),
        finished_at=_aware(r.finished_at) if r.finished_at else None,
        status=r.status,
        detail=r.detail,
    )


def _aware(dt: datetime) -> datetime:
    # SQLite no guarda la zona horaria: lo que se escribió fue UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/status", response_model=SystemStatus)
async def status(_: CurrentUser, services: ServicesDep) -> SystemStatus:
    tz = ZoneInfo(services.settings.agent_timezone)
    now = datetime.now(tz)
    async with services.sessions() as session:
        hb = await session.get(SystemState, HEARTBEAT_KEY)
        jobs: list[JobOut] = []
        for spec in JOBS:
            last = (
                await session.execute(
                    select(JobRun)
                    .where(JobRun.job_id == spec.id)
                    .order_by(JobRun.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            trigger = CronTrigger(timezone=tz, **spec.cron)
            jobs.append(
                JobOut(
                    id=spec.id,
                    name=spec.name,
                    description=spec.description,
                    schedule=spec.schedule,
                    trading_days_only=spec.trading_days_only,
                    next_run=trigger.get_next_fire_time(None, now),
                    last_run=_run_out(last) if last else None,
                )
            )
        notes = (
            (
                await session.execute(
                    select(NotificationLog).order_by(NotificationLog.id.desc()).limit(15)
                )
            )
            .scalars()
            .all()
        )

    last_hb = _aware(hb.updated_at) if hb else None
    return SystemStatus(
        timezone=services.settings.agent_timezone,
        now=now,
        worker_last_heartbeat=last_hb,
        worker_alive=last_hb is not None and utcnow() - last_hb < WORKER_STALE_AFTER,
        notifier_channel=services.notifier.channel.name,
        notifier_configured=services.notifier.configured,
        jobs=jobs,
        notifications=[
            NotificationOut(
                id=n.id,
                created_at=_aware(n.created_at),
                channel=n.channel,
                trigger=n.trigger,
                text=n.text,
                status=n.status,
                error=n.error,
            )
            for n in notes
        ],
    )


@router.post("/notify-test", response_model=NotificationOut)
async def notify_test(user: CurrentUser, services: ServicesDep) -> NotificationOut:
    n = await services.notifier.send(
        f"👋 Prueba de Market Analyst ({user}). Si leés esto, las alertas por WhatsApp funcionan.",
        trigger="prueba_manual",
    )
    return NotificationOut(
        id=n.id,
        created_at=_aware(n.created_at),
        channel=n.channel,
        trigger=n.trigger,
        text=n.text,
        status=n.status,
        error=n.error,
    )


@router.post("/jobs/{job_id}/run", response_model=JobRunOut)
async def run_now(_: CurrentUser, services: ServicesDep, job_id: str) -> JobRunOut:
    spec = JOBS_BY_ID.get(job_id)
    if spec is None:
        raise HTTPException(404, "Tarea inexistente.")
    return _run_out(await run_job(spec, services, trigger="manual"))
