"""Base de datos local (SQLite) para lo que el sitio necesita recordar: corridas del agente,
notificaciones enviadas y el latido del proceso en segundo plano.

Las tablas se crean al arrancar (`init_db`). Cuando haya que cambiar una tabla existente, se
agrega Alembic para migrar sin perder datos.
"""

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JobRun(Base):
    """Una ejecución de una tarea programada (auditoría: qué corrió, cuándo y cómo terminó)."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # ok | error | skipped | running
    detail: Mapped[str | None] = mapped_column(Text)


class NotificationLog(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    channel: Mapped[str] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(64))  # qué lo generó (job, prueba manual...)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # sent | failed | disabled
    error: Mapped[str | None] = mapped_column(Text)


class SystemState(Base):
    """Valores sueltos de estado, por ejemplo el último latido del proceso en segundo plano."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EconEvent(Base):
    """Un dato económico del calendario, con el pronóstico (consenso) y, una vez publicado, el
    dato real y la sorpresa."""

    __tablename__ = "econ_events"
    __table_args__ = (UniqueConstraint("title", "country", "release_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_key: Mapped[str | None] = mapped_column(String(32), index=True)  # None = sin seguimiento
    title: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(8))
    release_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # UTC
    impact: Mapped[str] = mapped_column(String(16))
    forecast_raw: Mapped[str | None] = mapped_column(String(32))
    previous_raw: Mapped[str | None] = mapped_column(String(32))
    forecast_value: Mapped[float | None] = mapped_column(Float)
    reference_month: Mapped[date | None] = mapped_column(Date)
    actual: Mapped[float | None] = mapped_column(Float)
    actual_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    surprise: Mapped[float | None] = mapped_column(Float)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


def make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite") and ":///" in url:
        path = url.split(":///", 1)[1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(url)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


SessionFactory = async_sessionmaker
