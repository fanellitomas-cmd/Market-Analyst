from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.core.security import SESSION_COOKIE, LoginThrottle, decode_session_token
from app.services.macro import MacroService
from app.services.markets import MarketsService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_user(request: Request, settings: SettingsDep) -> str:
    if not settings.auth_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "El login no está configurado (faltan APP_PASSWORD_HASH y/o JWT_SECRET).",
        )
    token = request.cookies.get(SESSION_COOKIE)
    username = decode_session_token(settings, token) if token else None
    if username is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o vencida.")
    return username


CurrentUser = Annotated[str, Depends(require_user)]


def get_macro_service(request: Request) -> MacroService:
    service: MacroService = request.app.state.macro_service
    return service


def get_markets_service(request: Request) -> MarketsService:
    service: MarketsService = request.app.state.markets_service
    return service


def get_login_throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.login_throttle
    return throttle
