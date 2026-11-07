"""Configuración leída del entorno (o de `backend/.env`).

Todas las fuentes de datos de esta primera versión son gratuitas y no necesitan clave:
Banco Mundial (macro anual por país), FRED (macro de EE.UU.), BCRA y APIs comunitarias
(Argentina) y Yahoo Finance (precios diarios).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Login de un solo usuario (sitio de uso personal) ---
    app_username: str = "admin"
    # Hash bcrypt de la contraseña; se genera con `python scripts/hash_password.py`.
    # Vacío = login deshabilitado (la API responde 503 en vez de aceptar cualquier cosa).
    app_password_hash: str = ""
    # Secreto para firmar la sesión. Vacío = login deshabilitado.
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60 * 24 * 7
    # En producción (HTTPS) debe ser true para que el navegador sólo mande la cookie cifrada.
    cookie_secure: bool = False
    login_max_failures: int = 5
    login_lockout_seconds: int = 15 * 60

    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Agente 24/7 ---
    database_url: str = "sqlite+aiosqlite:///./data/market_analyst.db"
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
    cache_ttl_dollar_quotes_seconds: int = 5 * 60
    cache_ttl_ar_series_seconds: int = 3 * 3600
    cache_ttl_sec_facts_seconds: int = 6 * 3600
    cache_ttl_sec_filings_seconds: int = 3600

    @property
    def auth_configured(self) -> bool:
        return bool(self.app_password_hash and self.jwt_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
