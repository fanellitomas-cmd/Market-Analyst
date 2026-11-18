"""Caché TTL en memoria con deduplicación de pedidos simultáneos.

Si dos pestañas piden la misma serie a la vez, se hace una sola llamada a la fuente. Sólo se
guardan resultados exitosos: un error de la fuente no queda "pegado" hasta que venza el TTL.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float, max_entries: int = 512) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, T]] = {}
        self._inflight: dict[str, asyncio.Future[T]] = {}

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        should_cache: Callable[[T], bool] = lambda _: True,
    ) -> T:
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]

        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)

        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await loader()
        except BaseException as exc:
            future.set_exception(exc)
            # Si nadie más esperaba, evita el warning de "exception never retrieved".
            future.exception()
            raise
        finally:
            self._inflight.pop(key, None)

        if should_cache(value):
            if len(self._entries) >= self._max_entries:
                self._evict(now)
            self._entries[key] = (now + self._ttl, value)
        future.set_result(value)
        return value

    def _evict(self, now: float) -> None:
        expired = [k for k, (exp, _) in self._entries.items() if exp <= now]
        for k in expired:
            del self._entries[k]
        if len(self._entries) >= self._max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k][0])
            del self._entries[oldest]

    def clear(self) -> None:
        self._entries.clear()
