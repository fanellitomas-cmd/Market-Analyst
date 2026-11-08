"""Base de datos para lo que el sitio necesita recordar: corridas del agente, notificaciones
enviadas, tesis y el latido del proceso en segundo plano.

Por defecto es un archivo SQLite local. En servidores sin disco persistente (Render, GitHub
Actions) se usa Postgres: basta con poner en DATABASE_URL la dirección que da el proveedor
(por ejemplo Neon, `postgresql://usuario:clave@host/base?sslmode=require`).

Las tablas se crean al arrancar (`init_db`). Cuando haya que cambiar una tabla existente, se
agrega Alembic para migrar sin perder datos.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


class ScreenerResult(Base):
    """Resultado de una empresa en una corrida del screener (se guarda el historial)."""

    __tablename__ = "screener_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ticker: Mapped[str] = mapped_column(String(16))
    passes: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[str] = mapped_column(Text)  # ScreenerRow en JSON


class OptionSnapshot(Base):
    """Foto diaria de las opciones de un activo: base para ver cómo cambia el interés abierto."""

    __tablename__ = "option_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    spot: Mapped[float] = mapped_column(Float, default=0.0)
    call_oi: Mapped[float] = mapped_column(Float, default=0.0)
    put_oi: Mapped[float] = mapped_column(Float, default=0.0)
    total_gex: Mapped[float] = mapped_column(Float, default=0.0)
    zero_gamma: Mapped[float | None] = mapped_column(Float)


class Thesis(Base):
    """Tesis de inversión con su kill switch doble (fundamental y técnico)."""

    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="activa")  # activa|invalidada|cerrada
    hypothesis: Mapped[str] = mapped_column(Text)  # hipótesis de desalineación
    min_operating_margin: Mapped[float] = mapped_column(Float)  # % (kill switch fundamental)
    support: Mapped[float] = mapped_column(Float)  # nivel estructural (kill switch técnico)
    use_earnings_avwap: Mapped[bool] = mapped_column(Boolean, default=True)
    entry_price: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    # Último trimestre conocido al crear la tesis: el kill switch fundamental mira el siguiente.
    baseline_quarter_end: Mapped[date | None] = mapped_column(Date)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check: Mapped[str | None] = mapped_column(Text)  # JSON con el resultado
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(Text)


class Report(Base):
    """Entregables de la cadencia (pre-market, cierre) guardados como JSON."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[str] = mapped_column(Text)


class TriggerLog(Base):
    """Alertas de asimetría intradía (una por activo, tipo y día como máximo)."""

    __tablename__ = "trigger_log"
    __table_args__ = (UniqueConstraint("ticker", "kind", "day", "ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    day: Mapped[date] = mapped_column(Date, index=True)
    ticker: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))  # volumen | 8-K
    ref: Mapped[str] = mapped_column(String(64), default="")  # número de presentación, nivel...
    fact: Mapped[str] = mapped_column(Text)
    cause: Mapped[str] = mapped_column(Text)
    stop: Mapped[str] = mapped_column(Text)


# Parámetros de libpq (los que traen las URLs de Neon, Supabase, etc.) que asyncpg no acepta.
_LIBPQ_ONLY = {"sslmode", "channel_binding", "sslrootcert", "target_session_attrs"}


def postgres_engine_args(url: str) -> tuple[str, dict[str, Any]]:
    """Convierte una URL de Postgres "estándar" en una para SQLAlchemy + asyncpg.

    `postgres://` y `postgresql://` pasan a `postgresql+asyncpg://`, y `sslmode=require` (que
    asyncpg no entiende como parámetro) se traduce a `ssl=True` en los argumentos de conexión.
    """
    parts = urlsplit(url)
    scheme = "postgresql+asyncpg"
    query = parse_qsl(parts.query, keep_blank_values=True)
    sslmode = next((v for k, v in query if k == "sslmode"), None)
    kept = [(k, v) for k, v in query if k not in _LIBPQ_ONLY]
    clean = urlunsplit((scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    connect_args: dict[str, Any] = {}
    if sslmode in {"require", "verify-ca", "verify-full"}:
        connect_args["ssl"] = True
    return clean, connect_args


def make_engine(url: str) -> AsyncEngine:
    if url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        clean, connect_args = postgres_engine_args(url)
        # pool_pre_ping: las bases gratuitas "duermen" y cortan conexiones inactivas.
        return create_async_engine(clean, connect_args=connect_args, pool_pre_ping=True)
    if url.startswith("sqlite") and ":///" in url:
        path = url.split(":///", 1)[1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(url)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


SessionFactory = async_sessionmaker
