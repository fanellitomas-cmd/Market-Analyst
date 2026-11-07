from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.agent.jobs import JOBS_BY_ID, run_job, source_health
from app.container import Services
from app.core.market_calendar import easter, is_trading_day, next_trading_day, nyse_holidays
from app.main import create_app
from app.notify.channels import CallMeBotWhatsApp, NotifyError
from app.sources.yahoo import YahooClient
from tests.conftest import PASSWORD, Router, make_settings, yahoo_payload

# ---------- Calendario NYSE ----------


def test_easter() -> None:
    assert easter(2024) == date(2024, 3, 31)
    assert easter(2025) == date(2025, 4, 20)
    assert easter(2026) == date(2026, 4, 5)


def test_nyse_holidays_2026() -> None:
    assert nyse_holidays(2026) == {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),  # 4/7 cae sábado
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }


def test_observed_rules() -> None:
    assert date(2027, 7, 5) in nyse_holidays(2027)  # 4/7 domingo → lunes
    assert date(2022, 6, 20) in nyse_holidays(2022)  # Juneteenth domingo → lunes
    assert date(2021, 6, 18) not in nyse_holidays(2021)  # Juneteenth recién desde 2022
    # Año Nuevo 2022 cayó sábado: el viernes 31/12/2021 la bolsa abrió.
    assert is_trading_day(date(2021, 12, 31))


def test_trading_days() -> None:
    assert not is_trading_day(date(2026, 11, 7))  # sábado
    assert not is_trading_day(date(2026, 11, 26))  # Acción de Gracias
    assert next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)


# ---------- Velas OHLCV ----------


def bars_payload(
    rows: list[tuple[float | None, ...]], adj: list[float] | None, tz: str
) -> dict[str, Any]:
    start = 1_767_623_400  # 2026-01-05 14:30 UTC
    cols: list[tuple[float | None, ...]] = list(zip(*rows, strict=True))
    indicators: dict[str, object] = {
        "quote": [
            {
                "open": list(cols[0]),
                "high": list(cols[1]),
                "low": list(cols[2]),
                "close": list(cols[3]),
                "volume": list(cols[4]),
            }
        ]
    }
    if adj is not None:
        indicators["adjclose"] = [{"adjclose": adj}]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "exchangeTimezoneName": tz, "gmtoffset": -18000},
                    "timestamp": [start + i * 86400 for i in range(len(rows))],
                    "indicators": indicators,
                }
            ],
            "error": None,
        }
    }


async def test_daily_bars_are_adjusted_and_validated() -> None:
    router = Router()
    rows: list[tuple[float | None, ...]] = [
        (10, 12, 9, 11, 1000),
        (11, 10, 12, 11, 500),  # máximo < mínimo: vela rota
        (11, 13, 10, 12, None),  # sin volumen: None, no 0
        (None, 13, 10, 12, 100),  # sin apertura
    ]
    router.json("/chart/ABC", bars_payload(rows, [5.5, 5.5, 6.0, 6.0], "America/New_York"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        bars = await YahooClient(http, "https://y.test").get_bars("ABC", "1d", "1y")
    assert bars.adjusted is True
    assert bars.missing_points == 2
    first, second = bars.bars
    # factor 5.5 / 11 = 0.5 aplicado a toda la vela
    assert (first.open, first.high, first.low, first.close) == (5.0, 6.0, 4.5, 5.5)
    assert first.volume == 1000
    assert second.volume is None
    assert first.time.utcoffset() is not None
    assert first.time.hour == 9 and first.time.minute == 30  # hora de Nueva York
    assert bars.exchange_timezone == "America/New_York"


async def test_bars_reject_bad_range() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(Router())) as http:
        with pytest.raises(ValueError):
            await YahooClient(http, "https://y.test").get_bars("ABC", "30m", "1y")


# ---------- WhatsApp (CallMeBot) ----------


async def test_callmebot_sends_and_detects_errors() -> None:
    router = Router()
    router.add(
        "phone=%2B5491100000000", lambda _r: httpx.Response(200, text="<p>Message queued</p>")
    )
    router.add(
        "phone=%2B5491199999999", lambda _r: httpx.Response(200, text="<b>APIKey is invalid</b>")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        ok = CallMeBotWhatsApp(http, "https://cmb.test", "+5491100000000", "123")
        await ok.send("hola")
        assert router.calls[0].url.params["text"] == "hola"
        bad = CallMeBotWhatsApp(http, "https://cmb.test", "+5491199999999", "123")
        with pytest.raises(NotifyError, match="invalid"):
            await bad.send("hola")
        with pytest.raises(NotifyError, match="no configurado"):
            await CallMeBotWhatsApp(http, "https://cmb.test", "", "").send("hola")


# ---------- Tareas y API de sistema ----------


@pytest.fixture
def wa_app(router: Router) -> FastAPI:
    settings = make_settings(whatsapp_phone="+5491100000000", callmebot_apikey="k")
    router.add("callmebot.test", lambda _r: httpx.Response(200, text="Message queued"))
    return create_app(settings, transport=httpx.MockTransport(router))


@pytest.fixture
async def wa_client(wa_app: FastAPI):  # type: ignore[no-untyped-def]
    async with (
        wa_app.router.lifespan_context(wa_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=wa_app), base_url="http://t") as c,
    ):
        r = await c.post("/api/auth/login", json={"username": "tomas", "password": PASSWORD})
        assert r.status_code == 200
        yield c


def healthy_sources(router: Router) -> None:
    router.json("/v8/finance/chart/SPY", yahoo_payload([400.0, 401.0]))
    router.add(
        "fred.test", lambda _r: httpx.Response(200, text="observation_date,DGS10\n2026-11-05,4.1\n")
    )
    router.json("wb.test", [{"page": 1, "pages": 1}, []])
    router.json(
        "bcra.test", {"status": 200, "results": [{"idVariable": 1, "descripcion": "Reservas"}]}
    )
    router.json("dolarapi.test", [])
    router.json("argdatos.test", [])
    router.json("sec.test", {})
    router.json("ff.test", [])
    router.json(
        "cboe.test",
        {
            "data": {
                "current_price": 600,
                "options": [{"option": "SPY261218C00600000", "open_interest": 1, "iv": 0.2}],
            }
        },
    )
    router.json("okx.test", {"code": "0", "msg": "", "data": [{"fundingRate": "0.0001"}]})


async def test_source_health_job(
    wa_app: FastAPI, wa_client: httpx.AsyncClient, router: Router
) -> None:
    services: Services = wa_app.state.services
    healthy_sources(router)
    result = await source_health(services)
    assert result.notify is None and not result.failed
    assert result.detail == "10/10 fuentes responden"

    router.add("bcra.test", lambda _r: httpx.Response(503))
    result = await source_health(services)
    assert result.failed
    assert result.notify is not None and "BCRA" in result.notify


async def test_run_job_skips_holidays_and_logs(
    wa_app: FastAPI, wa_client: httpx.AsyncClient
) -> None:
    services: Services = wa_app.state.services
    run = await run_job(JOBS_BY_ID["salud_fuentes"], services, today=date(2026, 11, 26))
    assert run.status == "skipped"
    assert "no es día hábil" in (run.detail or "")


async def test_system_status_and_notify(wa_client: httpx.AsyncClient, router: Router) -> None:
    r = await wa_client.get("/api/system/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timezone"] == "America/Argentina/Buenos_Aires"
    assert body["notifier_configured"] is True
    assert body["worker_alive"] is False  # el proceso en segundo plano no corre en los tests
    jobs = {j["id"]: j for j in body["jobs"]}
    assert set(jobs) == {
        "salud_fuentes",
        "calendario_economico",
        "datos_publicados",
        "opciones_cierre",
        "screener_garp",
        "latido_semanal",
    }
    assert jobs["latido_semanal"]["next_run"].endswith("-03:00")  # hora de Buenos Aires
    assert jobs["salud_fuentes"]["last_run"] is None

    r = await wa_client.post("/api/system/notify-test")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    assert any("callmebot.test" in str(c.url) for c in router.calls)

    healthy_sources(router)
    r = await wa_client.post("/api/system/jobs/latido_semanal/run")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    body = (await wa_client.get("/api/system/status")).json()
    assert body["notifications"][0]["trigger"] == "latido_semanal"
    assert body["notifications"][0]["status"] == "sent"
    assert {j["id"]: j for j in body["jobs"]}["latido_semanal"]["last_run"]["status"] == "ok"

    assert (await wa_client.post("/api/system/jobs/nada/run")).status_code == 404


async def test_notification_disabled_is_logged(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.post("/api/system/notify-test")
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


async def test_system_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/system/status")).status_code == 401
    assert (await client.post("/api/system/notify-test")).status_code == 401


def test_job_times_use_agent_timezone() -> None:
    from zoneinfo import ZoneInfo

    from apscheduler.triggers.cron import CronTrigger

    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    spec = JOBS_BY_ID["salud_fuentes"]
    nxt = CronTrigger(timezone=tz, **spec.cron).get_next_fire_time(
        None,
        datetime(2026, 11, 6, 12, 0, tzinfo=UTC),  # viernes 09:00 en Buenos Aires
    )
    assert nxt is not None
    assert nxt.isoformat() == "2026-11-09T07:15:00-03:00"  # lunes 07:15


async def test_bars_endpoint(auth_client: httpx.AsyncClient, router: Router) -> None:
    rows: list[tuple[float | None, ...]] = [(10, 12, 9, 11, 1000), (11, 13, 10, 12, 2000)]
    router.json("/chart/QQQ", bars_payload(rows, None, "America/New_York"))
    r = await auth_client.get(
        "/api/markets/bars", params={"symbol": "qqq", "interval": "30m", "range": "60d"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "QQQ" and body["interval"] == "30m"
    assert body["adjusted"] is False
    assert body["bars"][0]["time"].endswith("-05:00")
    assert body["bars"][1]["volume"] == 2000
    r = await auth_client.get(
        "/api/markets/bars", params={"symbol": "QQQ", "interval": "30m", "range": "5y"}
    )
    assert r.status_code == 422
