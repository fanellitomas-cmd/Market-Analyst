"""Envía notificaciones y deja registro de cada intento (enviada, fallida o deshabilitada)."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import NotificationLog
from app.notify.channels import Channel, NotifyError

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, channel: Channel, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.channel = channel
        self._sessions = sessions

    @property
    def configured(self) -> bool:
        return self.channel.configured

    async def send(self, text: str, trigger: str) -> NotificationLog:
        status, error = "sent", None
        if not self.channel.configured:
            status, error = "disabled", "canal no configurado"
        else:
            try:
                await self.channel.send(text)
            except NotifyError as exc:
                status, error = "failed", str(exc)
                logger.warning("notificación %s falló: %s", trigger, exc)
        row = NotificationLog(
            channel=self.channel.name, trigger=trigger, text=text, status=status, error=error
        )
        async with self._sessions() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row
