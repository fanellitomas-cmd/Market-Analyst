"""Proceso en segundo plano del agente: corre las tareas programadas 24/7.

Uso (desde backend/):  python -m app.worker

Se ejecuta aparte de la API web a propósito: si la web se reinicia o corre con varias copias,
las tareas no se duplican ni se cortan.
"""

import asyncio
import logging
import signal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.agent.jobs import JOBS, run_job, write_heartbeat
from app.container import Services, build_services
from app.core.config import get_settings

logger = logging.getLogger("app.worker")


def build_scheduler(services: Services) -> AsyncIOScheduler:
    tz = ZoneInfo(services.settings.agent_timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    for spec in JOBS:
        scheduler.add_job(
            run_job,
            CronTrigger(timezone=tz, **spec.cron),
            args=[spec, services],
            id=spec.id,
            name=spec.name,
            coalesce=True,  # si la máquina estuvo apagada, una sola corrida al volver
            misfire_grace_time=15 * 60,
            max_instances=1,
        )
    scheduler.add_job(
        write_heartbeat,
        IntervalTrigger(seconds=60, timezone=tz),
        args=[services],
        id="heartbeat",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with build_services(settings) as services:
        scheduler = build_scheduler(services)
        scheduler.start()
        await write_heartbeat(services)
        logger.info("agente iniciado (zona %s)", settings.agent_timezone)
        for job in scheduler.get_jobs():
            logger.info("  %s → próxima: %s", job.id, job.next_run_time)
        if not services.notifier.configured:
            logger.warning("WhatsApp no configurado: las notificaciones quedan sólo registradas")
        await stop.wait()
        scheduler.shutdown(wait=False)
        logger.info("agente detenido")


if __name__ == "__main__":
    asyncio.run(main())
