"""Punto de entrada de la API: `uvicorn app.main:app --reload` desde `backend/`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    ar,
    auth,
    cadence,
    calendar,
    earnings,
    forensic,
    liquidity,
    macro,
    markets,
    options,
    screener,
    sentinel,
    system,
    technical,
    thesis,
    us,
)
from app.container import build_services
from app.core.config import Settings, get_settings
from app.core.security import LoginThrottle

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """`transport` permite inyectar respuestas simuladas en los tests sin tocar la red."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with build_services(settings, transport) as services:
            app.state.services = services
            app.state.macro_service = services.macro
            app.state.markets_service = services.markets
            app.state.us_service = services.us
            app.state.ar_service = services.ar
            app.state.earnings_service = services.earnings
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
    api.include_router(earnings.router)
    api.include_router(system.router)
    api.include_router(liquidity.router)
    api.include_router(calendar.router)
    api.include_router(technical.router)
    api.include_router(forensic.router)
    api.include_router(screener.router)
    api.include_router(sentinel.router)
    api.include_router(options.router)
    api.include_router(thesis.router)
    api.include_router(cadence.router)
    app.include_router(api)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
