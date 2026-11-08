"""Una "pasada" del agente: ejecuta las tareas programadas que tocan ahora y termina.

Uso (desde backend/):  python -m app.agent.tick

Reemplaza al proceso 24/7 (`app.worker`) donde no hay un servidor siempre prendido: GitHub
Actions lo corre cada 15 minutos. Como esos disparos se pueden demorar (o, a veces, saltear),
cada pasada busca, para cada tarea, el último horario programado de los últimos
`GRACE_MINUTES` y lo ejecuta si todavía no se hizo. El horario ya atendido queda guardado en la
base, así dos pasadas seguidas nunca repiten una tarea.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from app.agent.jobs import JOBS, JobSpec, run_job, write_heartbeat
from app.container import Services, build_services
from app.core.config import get_settings
from app.core.db import SystemState, utcnow

logger = logging.getLogger("app.agent.tick")

# Ventana para recuperar un horario demorado. Más larga no tiene sentido: un aviso de
# pre-market que llega dos horas tarde ya no sirve.
GRACE_MINUTES = 50
SLOT_KEY = "tick_slot:"


def last_slot(spec: JobSpec, now: datetime, grace: timedelta) -> datetime | None:
    """El último horario programado de la tarea dentro de (now − grace, now]."""
    trigger = CronTrigger(timezone=now.tzinfo, **spec.cron)
    slot = None
    fire = trigger.get_next_fire_time(None, now - grace)
    while fire is not None and fire <= now:
        slot = fire
        fire = trigger.get_next_fire_time(fire, fire + timedelta(seconds=1))
    return slot


async def due_jobs(services: Services, now: datetime) -> list[tuple[JobSpec, datetime]]:
    grace = timedelta(minutes=GRACE_MINUTES)
    due: list[tuple[JobSpec, datetime]] = []
    async with services.sessions() as session:
        for spec in JOBS:
            slot = last_slot(spec, now, grace)
            if slot is None:
                continue
            done = await session.get(SystemState, SLOT_KEY + spec.id)
            if done is None or done.value != slot.isoformat():
                due.append((spec, slot))
    return due


async def mark_done(services: Services, spec: JobSpec, slot: datetime) -> None:
    async with services.sessions() as session:
        row = await session.get(SystemState, SLOT_KEY + spec.id)
        if row is None:
            session.add(SystemState(key=SLOT_KEY + spec.id, value=slot.isoformat()))
        else:
            row.value = slot.isoformat()
            row.updated_at = utcnow()
        await session.commit()


async def tick(services: Services, now: datetime | None = None) -> list[str]:
    """Ejecuta lo pendiente y devuelve los ids de las tareas que corrió."""
    tz = ZoneInfo(services.settings.agent_timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    await write_heartbeat(services)
    ran: list[str] = []
    for spec, slot in await due_jobs(services, now):
        # Se marca antes de correr: si la tarea tarda o falla, la próxima pasada no la repite
        # (el error queda registrado en "Sistema" y, si corresponde, avisa por WhatsApp).
        await mark_done(services, spec, slot)
        run = await run_job(spec, services, trigger=f"programada {slot:%H:%M}")
        logger.info("%s (%s) → %s", spec.id, slot.strftime("%Y-%m-%d %H:%M"), run.status)
        ran.append(spec.id)
    if not ran:
        logger.info("nada pendiente a las %s", now.strftime("%Y-%m-%d %H:%M"))
    return ran


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx registra cada URL pedida: la de WhatsApp lleva el número y la clave, y los
    # registros de GitHub Actions de un repositorio público los puede ver cualquiera.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with build_services(get_settings()) as services:
        await tick(services)


if __name__ == "__main__":
    asyncio.run(main())
