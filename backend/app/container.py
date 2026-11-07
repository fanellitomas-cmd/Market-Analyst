"""Arma todos los clientes y servicios a partir de la configuración.

Lo usan tanto la API web (`app.main`) como el proceso en segundo plano (`app.worker`), para que
los dos lean los datos exactamente de la misma manera.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.db import init_db, make_engine
from app.notify.channels import CallMeBotWhatsApp
from app.notify.service import Notifier
from app.services.ar_macro import ArMacroService, SeriesPoints
from app.services.earnings import EarningsService
from app.services.econ_calendar import EconCalendarService
from app.services.forensic import ForensicService
from app.services.liquidity import LiquidityService, Points
from app.services.macro import MacroService
from app.services.markets import MarketsService
from app.services.options import OptionsService
from app.services.screener import ScreenerService
from app.services.sentinel import SentinelService
from app.services.technical import TechnicalService
from app.services.us_macro import UsMacroService
from app.sources.argentina_markets import ArgentinaDatosClient, DolarApiClient, DollarQuote
from app.sources.bcra import BcraClient, BcraVariable
from app.sources.cboe import CboeClient
from app.sources.forexfactory import ForexFactoryClient
from app.sources.fred import FredClient, FredSeries
from app.sources.okx import OkxClient
from app.sources.sec import CompanyFilings, CompanyRef, SecClient
from app.sources.worldbank import CountrySeries, WorldBankClient
from app.sources.yahoo import PriceSeries, YahooClient


@dataclass
class Clients:
    worldbank: WorldBankClient
    yahoo: YahooClient
    fred: FredClient
    bcra: BcraClient
    dolarapi: DolarApiClient
    argdatos: ArgentinaDatosClient
    sec: SecClient
    forexfactory: ForexFactoryClient
    okx: OkxClient
    cboe: CboeClient


@dataclass
class Services:
    settings: Settings
    clients: Clients
    macro: MacroService
    markets: MarketsService
    us: UsMacroService
    ar: ArMacroService
    earnings: EarningsService
    liquidity: LiquidityService
    calendar: EconCalendarService
    technical: TechnicalService
    forensic: ForensicService
    screener: ScreenerService
    sentinel: SentinelService
    options: OptionsService
    notifier: Notifier
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def build_services(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[Services]:
    """`transport` permite inyectar respuestas simuladas en los tests sin tocar la red."""
    engine = make_engine(settings.database_url)
    await init_db(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout_seconds, transport=transport, follow_redirects=True
        ) as client:
            clients = Clients(
                worldbank=WorldBankClient(client, settings.worldbank_base_url),
                yahoo=YahooClient(client, settings.yahoo_base_url),
                fred=FredClient(client, settings.fred_base_url),
                bcra=BcraClient(client, settings.bcra_base_url),
                dolarapi=DolarApiClient(client, settings.dolarapi_base_url),
                argdatos=ArgentinaDatosClient(client, settings.argentinadatos_base_url),
                sec=SecClient(
                    client, settings.sec_www_url, settings.sec_data_url, settings.sec_user_agent
                ),
                forexfactory=ForexFactoryClient(client, settings.forexfactory_base_url),
                okx=OkxClient(client, settings.okx_base_url),
                cboe=CboeClient(client, settings.cboe_base_url),
            )
            markets = MarketsService(
                clients.yahoo, TTLCache[PriceSeries](settings.cache_ttl_prices_seconds)
            )
            earnings = EarningsService(
                clients.sec,
                markets,
                TTLCache[dict[str, CompanyRef]](24 * 3600),
                TTLCache[dict[str, Any]](settings.cache_ttl_sec_facts_seconds, max_entries=64),
                TTLCache[CompanyFilings](settings.cache_ttl_sec_filings_seconds, max_entries=64),
            )
            us = UsMacroService(clients.fred, TTLCache[FredSeries](settings.cache_ttl_fred_seconds))
            forensic = ForensicService(earnings, markets, us)
            ar = ArMacroService(
                clients.bcra,
                clients.dolarapi,
                clients.argdatos,
                TTLCache[list[BcraVariable]](settings.cache_ttl_bcra_seconds),
                TTLCache[list[DollarQuote]](settings.cache_ttl_dollar_quotes_seconds),
                TTLCache[SeriesPoints](settings.cache_ttl_ar_series_seconds),
            )
            yield Services(
                settings=settings,
                clients=clients,
                macro=MacroService(
                    clients.worldbank,
                    TTLCache[dict[str, CountrySeries]](settings.cache_ttl_macro_seconds),
                ),
                markets=markets,
                us=us,
                calendar=EconCalendarService(clients.forexfactory, clients.fred, sessions),
                liquidity=LiquidityService(
                    us,
                    markets,
                    TTLCache[tuple[Points, str | None]](settings.cache_ttl_prices_seconds),
                ),
                ar=ar,
                sentinel=SentinelService(clients.okx, clients.yahoo, markets, ar, sessions),
                options=OptionsService(clients.cboe, sessions),
                earnings=earnings,
                technical=TechnicalService(markets, earnings),
                forensic=forensic,
                screener=ScreenerService(forensic, sessions),
                notifier=Notifier(
                    CallMeBotWhatsApp(
                        client,
                        settings.callmebot_base_url,
                        settings.whatsapp_phone,
                        settings.callmebot_apikey,
                    ),
                    sessions,
                ),
                engine=engine,
                sessions=sessions,
            )
    finally:
        await engine.dispose()
