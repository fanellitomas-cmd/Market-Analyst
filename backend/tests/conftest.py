"""Fixtures: la app se arma con un transporte HTTP simulado, así los tests nunca salen a la red."""

import json
import math
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.security import hash_password
from app.main import create_app

PASSWORD = "una-clave-de-prueba"
Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_username": "tomas",
        "app_password_hash": hash_password(PASSWORD),
        "jwt_secret": "secreto-de-test-suficientemente-largo-para-hs256",
        "worldbank_base_url": "https://wb.test/v2",
        "yahoo_base_url": "https://yahoo.test",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def yahoo_payload(
    closes: list[float | None], start: datetime | None = None, adj: bool = True
) -> dict[str, Any]:
    start = start or datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    timestamps = [int((start + timedelta(days=i)).timestamp()) for i in range(len(closes))]
    indicators: dict[str, Any] = {"quote": [{"close": closes}]}
    if adj:
        indicators["adjclose"] = [{"adjclose": closes}]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "gmtoffset": -18000, "longName": "Test Corp"},
                    "timestamp": timestamps,
                    "indicators": indicators,
                }
            ],
            "error": None,
        }
    }


def synthetic_prices(n: int, drift: float, amplitude: float, phase: float = 0.0) -> list[float]:
    price = 100.0
    out = [price]
    for i in range(1, n):
        price *= 1 + drift + amplitude * math.sin(i * 0.7 + phase)
        out.append(price)
    return out


class Router:
    """Transporte simulado: asocia un fragmento de URL con una respuesta."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, Handler]] = []
        self.calls: list[httpx.Request] = []

    def add(self, fragment: str, handler: Handler) -> None:
        self.routes.insert(0, (fragment, handler))

    def json(self, fragment: str, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.add(
            fragment,
            lambda _req: httpx.Response(
                status, content=body, headers={"content-type": "application/json"}
            ),
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        for fragment, handler in self.routes:
            if fragment in url:
                return handler(request)
        return httpx.Response(599, text=f"sin ruta simulada para {url}")


@pytest.fixture
def router() -> Router:
    return Router()


@pytest.fixture
def app(router: Router) -> FastAPI:
    return create_app(make_settings(), transport=httpx.MockTransport(router))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as c,
    ):
        yield c


@pytest.fixture
async def auth_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    r = await client.post("/api/auth/login", json={"username": "tomas", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client
