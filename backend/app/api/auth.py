from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, SettingsDep, get_login_throttle
from app.core.security import (
    SESSION_COOKIE,
    LoginThrottle,
    create_session_token,
    verify_credentials,
)
from app.schemas import LoginRequest, SessionInfo

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=SessionInfo)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
) -> SessionInfo:
    if not settings.auth_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "El login no está configurado (faltan APP_PASSWORD_HASH y/o JWT_SECRET).",
        )
    client_key = request.client.host if request.client else "desconocido"
    if throttle.is_locked(client_key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Demasiados intentos fallidos. Probá de nuevo en unos minutos.",
        )
    if not verify_credentials(settings, body.username, body.password):
        throttle.register_failure(client_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos.")

    throttle.reset(client_key)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(settings, settings.app_username),
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return SessionInfo(username=settings.app_username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, settings: SettingsDep) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


@router.get("/me", response_model=SessionInfo)
def me(user: CurrentUser) -> SessionInfo:
    return SessionInfo(username=user)
