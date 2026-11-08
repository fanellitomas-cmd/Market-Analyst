"""Configuración leída del entorno (o de `backend/.env`).

Todas las fuentes de datos de esta primera versión son gratuitas y no necesitan clave:
Banco Mundial (macro anual por país), FRED (macro de EE.UU.), BCRA y APIs comunitarias
(Argentina) y Yahoo Finance (precios diarios).
"""

from functools import lru_cache

import bcrypt
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Login de un solo usuario (sitio de uso personal) ---
    app_username: str = "admin"
    # Hash bcrypt de la contraseña; se genera con `python scripts/hash_password.py`.
    # Vacío = login deshabilitado (la API responde 503 en vez de aceptar cualquier cosa).
    app_password_hash: str = ""
    # Alternativa para paneles de hosting (Render): la contraseña en sí, guardada como secreto
    # del proveedor. Se convierte en hash al arrancar y no se conserva en memoria.
    app_password: str = ""
    # Secreto para firmar la sesión. Vacío = login deshabilitado.
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60 * 24 * 7
    # En producción (HTTPS) debe ser true para que el navegador sólo mande la cookie cifrada.
    cookie_secure: bool = False
    login_max_failures: int = 5
    login_lockout_seconds: int = 15 * 60

    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Agente 24/7 ---
    # SQLite local, o la URL de Postgres del proveedor (postgresql://...). Ver app/core/db.py.
    database_url: str = "sqlite+aiosqlite:///./data/market_analyst.db"
    # Minutos sin latido tras los cuales "Sistema" muestra al agente como detenido. Con el
    # proceso 24/7 late cada minuto; con GitHub Actions corre cada 15 min (y a veces se demora).
    agent_heartbeat_stale_minutes: int = 3
    # Los horarios de las tareas programadas se expresan en esta zona.
    agent_timezone: str = "America/Argentina/Buenos_Aires"
    # WhatsApp vía CallMeBot (gratis para mensajes al propio número). Formato: +5491112345678
    whatsapp_phone: str = ""
    callmebot_apikey: str = ""
    callmebot_base_url: str = "https://api.callmebot.com"

    # --- Fuentes de datos ---
    http_timeout_seconds: float = 20.0
    worldbank_base_url: str = "https://api.worldbank.org/v2"
    yahoo_base_url: str = "https://query1.finance.yahoo.com"
    # Descarga CSV pública de FRED (Reserva Federal de St. Louis): gratis y sin clave.
    fred_base_url: str = "https://fred.stlouisfed.org"
    # Argentina: API oficial del BCRA y dos APIs comunitarias (dólar y riesgo país).
    bcra_base_url: str = "https://api.bcra.gob.ar"
    dolarapi_base_url: str = "https://dolarapi.com"
    argentinadatos_base_url: str = "https://api.argentinadatos.com"
    # SEC EDGAR (gratis). La SEC exige identificarse: "Nombre Apellido tu@email.com".
    # Vacío = la sección de resultados queda deshabilitada.
    forexfactory_base_url: str = "https://nfs.faireconomy.media"
    okx_base_url: str = "https://www.okx.com"
    cboe_base_url: str = "https://cdn.cboe.com"
    sec_user_agent: str = ""
    sec_www_url: str = "https://www.sec.gov"
    sec_data_url: str = "https://data.sec.gov"

    # Los datos macro son anuales: no tiene sentido pedirlos más de unas veces por día.
    cache_ttl_macro_seconds: int = 6 * 3600
    cache_ttl_prices_seconds: int = 15 * 60
    # FRED actualiza una vez por día hábil (las tasas, a la tarde de EE.UU.).
    cache_ttl_fred_seconds: int = 3 * 3600
    cache_ttl_bcra_seconds: int = 3600
    # El dólar se muestra en la cinta en vivo: la caché es corta.
    cache_ttl_dollar_quotes_seconds: int = 60
    cache_ttl_ar_series_seconds: int = 3 * 3600
    cache_ttl_sec_facts_seconds: int = 6 * 3600
    cache_ttl_sec_filings_seconds: int = 3600

    @property
    def auth_configured(self) -> bool:
        return bool(self.app_password_hash and self.jwt_secret)

    @model_validator(mode="after")
    def _hash_plain_password(self) -> "Settings":
        if self.app_password and not self.app_password_hash:
            if len(self.app_password) < 10:
                raise ValueError("APP_PASSWORD debe tener al menos 10 caracteres")
            self.app_password_hash = bcrypt.hashpw(
                self.app_password.encode(), bcrypt.gensalt()
            ).decode()
        self.app_password = ""
        return self

    @field_validator("app_password_hash", "jwt_secret", "database_url", mode="before")
    @classmethod
    def _strip_quotes(cls, v: object) -> object:
        # El script de contraseña imprime los valores entre comillas simples para el archivo
        # .env; si se pegan tal cual en el panel de un proveedor, las comillas quedan en el valor.
        if isinstance(v, str):
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
