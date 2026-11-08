"""Canales de notificación.

WhatsApp vía CallMeBot (https://www.callmebot.com/blog/free-api-whatsapp-messages/): gratis para
mandarse mensajes al propio número. Alta: agregar el número de CallMeBot a los contactos y
mandarle "I allow callmebot to send me messages"; responde con la `apikey`.

La API oficial de WhatsApp (Meta Cloud API) cobra los mensajes que inicia la empresa; si algún
día hace falta, se agrega otra clase con el mismo método `send` y no cambia nada más.
"""

from typing import Protocol

import httpx


class NotifyError(Exception):
    pass


class Channel(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    async def send(self, text: str) -> None: ...


class CallMeBotWhatsApp:
    name = "whatsapp"
    MAX_CHARS = 1500

    def __init__(self, client: httpx.AsyncClient, base_url: str, phone: str, apikey: str) -> None:
        self._client = client
        self._url = f"{base_url.rstrip('/')}/whatsapp.php"
        self._phone = phone.strip()
        self._apikey = apikey.strip()

    @property
    def configured(self) -> bool:
        return bool(self._phone and self._apikey)

    async def send(self, text: str) -> None:
        if not self.configured:
            raise NotifyError("WhatsApp no configurado (faltan WHATSAPP_PHONE y CALLMEBOT_APIKEY)")
        if len(text) > self.MAX_CHARS:
            text = text[: self.MAX_CHARS - 1] + "…"
        try:
            response = await self._client.get(
                self._url, params={"phone": self._phone, "text": text, "apikey": self._apikey}
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"no se pudo conectar con CallMeBot ({type(exc).__name__})") from exc
        body = response.text.lower()
        # CallMeBot responde 200 con un HTML; los errores vienen como texto dentro del cuerpo.
        if response.status_code != 200 or "error" in body or "invalid" in body:
            snippet = " ".join(response.text.split())[:160]
            for secret in (self._apikey, self._phone, self._phone.lstrip("+")):
                if secret:  # que el número y la clave no queden en registros
                    snippet = snippet.replace(secret, "***")
            raise NotifyError(
                f"CallMeBot rechazó el mensaje (HTTP {response.status_code}): {snippet}"
            )
