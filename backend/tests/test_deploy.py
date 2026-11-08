"""Instalación sin servidor propio: Postgres externo, agente por pasadas (GitHub Actions)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.agent import tick as tick_mod
from app.agent.jobs import JOBS_BY_ID, JobResult, JobSpec
from app.container import Services, build_services
from app.core.db import postgres_engine_args
from tests.conftest import Router, make_settings

AR = ZoneInfo("America/Argentina/Buenos_Aires")


def test_postgres_url_from_neon() -> None:
    url, args = postgres_engine_args(
        "postgresql://u:p@ep-x.sa-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert url == "postgresql+asyncpg://u:p@ep-x.sa-east-1.aws.neon.tech/neondb"
    assert args == {"ssl": True}
    url, args = postgres_engine_args("postgres://u:p@localhost:5432/db")
    assert url == "postgresql+asyncpg://u:p@localhost:5432/db" and args == {}


def test_secrets_pasted_with_quotes() -> None:
    s = make_settings(app_password_hash="'$2b$12$abc'", jwt_secret=' "xyz" ')
    assert s.app_password_hash == "$2b$12$abc" and s.jwt_secret == "xyz"


def test_plain_password_from_hosting_panel() -> None:
    from app.core.security import verify_credentials

    s = make_settings(app_password_hash="", app_password="una-clave-larga")
    assert s.app_password == "" and s.app_password_hash.startswith("$2")
    assert verify_credentials(s, "tomas", "una-clave-larga")
    with pytest.raises(ValueError, match="10 caracteres"):
        make_settings(app_password_hash="", app_password="corta")


@pytest.mark.parametrize(
    ("job", "now", "expected"),
    [
        ("premarket", datetime(2026, 11, 9, 7, 44, tzinfo=AR), datetime(2026, 11, 9, 7, 30)),
        ("premarket", datetime(2026, 11, 9, 8, 25, tzinfo=AR), None),  # demasiado tarde
        ("premarket", datetime(2026, 11, 8, 7, 44, tzinfo=AR), None),  # domingo
        (
            "alertas_intradia",
            datetime(2026, 11, 9, 10, 52, tzinfo=AR),
            datetime(2026, 11, 9, 10, 45),
        ),
    ],
)
def test_last_slot(job: str, now: datetime, expected: datetime | None) -> None:
    slot = tick_mod.last_slot(JOBS_BY_ID[job], now, timedelta(minutes=50))
    assert slot == (expected.replace(tzinfo=AR) if expected else None)


async def test_tick_runs_each_slot_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake(_: Services) -> JobResult:
        calls.append("x")
        return JobResult(detail="ok")

    spec = JobSpec(
        id="prueba",
        name="Prueba",
        description="",
        schedule="cada hora",
        cron={"minute": 0},
        func=fake,
    )
    monkeypatch.setattr(tick_mod, "JOBS", [spec])
    async with build_services(make_settings(), transport=httpx.MockTransport(Router())) as s:
        t0 = datetime(2026, 11, 9, 10, 7, tzinfo=AR)
        assert await tick_mod.tick(s, t0) == ["prueba"]
        assert await tick_mod.tick(s, t0 + timedelta(minutes=15)) == []  # mismo horario
        assert await tick_mod.tick(s, t0 + timedelta(minutes=60)) == ["prueba"]  # 11:00
    assert len(calls) == 2
