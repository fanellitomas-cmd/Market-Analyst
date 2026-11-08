"""Login de un solo usuario: contraseña bcrypt + sesión JWT en cookie httpOnly."""

import hmac
import time
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import Settings

SESSION_COOKIE = "ma_session"

# Hash de una contraseña cualquiera: se verifica contra él cuando el usuario no coincide, para que
# el tiempo de respuesta no delate si el nombre de usuario es correcto.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username.encode(), settings.app_username.encode())
    stored = settings.app_password_hash if user_ok else _DUMMY_HASH
    try:
        password_ok = bcrypt.checkpw(password.encode(), stored.encode())
    except ValueError:  # hash mal formado en la configuración
        return False
    return user_ok and password_ok


def create_session_token(settings: Settings, username: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_session_token(settings: Settings, token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str) or sub != settings.app_username:
        return None
    return sub


class LoginThrottle:
    """Bloquea una IP tras N intentos fallidos durante una ventana de tiempo (en memoria).

    Alcanza para un sitio personal con una sola instancia; no se comparte entre réplicas.
    """

    def __init__(self, max_failures: int, lockout_seconds: int) -> None:
        self._max = max_failures
        self._window = lockout_seconds
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self._window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def is_locked(self, key: str) -> bool:
        return len(self._recent(key, time.monotonic())) >= self._max

    def register_failure(self, key: str) -> None:
        now = time.monotonic()
        self._failures[key] = [*self._recent(key, now), now]

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
