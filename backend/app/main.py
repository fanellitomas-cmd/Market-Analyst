"""Punto de entrada de la API: `uvicorn app.main:app --reload` desde `backend/`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ar, auth, macro, markets, us
from app.core.cache import TTLCache
from app.core.config import Settings, get_settings
from app.core.security import LoginThrottle
from app.services.ar_macro import ArMacroService, SeriesPoints
from app.services.macro import MacroService
from app.services.markets import MarketsService
from app.services.us_macro import UsMacroService
from app.sources.argentina_markets import ArgentinaDatosClient, DolarApiClient, DollarQuote
from app.sources.bcra import BcraClient, BcraVariable
from app.sources.fred import FredClient, FredSeries
from app.sources.worldbank import CountrySeries, WorldBankClient
from app.sources.yahoo import PriceSeries, YahooClient

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """`transport` permite inyectar respuestas simuladas en los tests sin tocar la red."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout_seconds, transport=transport, follow_redirects=True
        ) as client:
            app.state.macro_service = MacroService(
                WorldBankClient(client, settings.worldbank_base_url),
                TTLCache[dict[str, CountrySeries]](settings.cache_ttl_macro_seconds),
            )
            app.state.markets_service = MarketsService(
                YahooClient(client, settings.yahoo_base_url),
                TTLCache[PriceSeries](settings.cache_ttl_prices_seconds),
            )
            app.state.us_service = UsMacroService(
                FredClient(client, settings.fred_base_url),
                TTLCache[FredSeries](settings.cache_ttl_fred_seconds),
            )
            app.state.ar_service = ArMacroService(
                BcraClient(client, settings.bcra_base_url),
                DolarApiClient(client, settings.dolarapi_base_url),
                ArgentinaDatosClient(client, settings.argentinadatos_base_url),
                TTLCache[list[BcraVariable]](settings.cache_ttl_bcra_seconds),
                TTLCache[list[DollarQuote]](settings.cache_ttl_dollar_quotes_seconds),
                TTLCache[SeriesPoints](settings.cache_ttl_ar_series_seconds),
            )
            app.state.login_throttle = LoginThrottle(
                settings.login_max_failures, settings.login_lockout_seconds
            )
            if not settings.auth_configured:
                logger.warning(
                    "Login deshabilitado: configurá APP_PASSWORD_HASH y JWT_SECRET en backend/.env"
                )
            yield

    app = FastAPI(title="Market Analyst API", version="0.1.0", lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    api = APIRouter(prefix="/api")
    api.include_router(auth.router)
    api.include_router(macro.router)
    api.include_router(markets.router)
    api.include_router(us.router)
    api.include_router(ar.router)
    app.include_router(api)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
