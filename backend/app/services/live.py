"""Cinta en vivo: los precios clave que se muestran en todas las páginas y se refrescan solos.

Frecuencia real de cada fuente (el frontend la consulta cada 30 s; acá hay una caché de 20 s para
que varias pestañas no multipliquen los pedidos):
  - Yahoo: acciones e índices de EE.UU., futuros, monedas y cripto casi en tiempo real.
  - DolarApi: cotizaciones del dólar con unos minutos de rezago.
  - ArgentinaDatos: riesgo país una vez por día.
Cada valor lleva la hora de la fuente, así se ve si un dato está demorado.
"""

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from typing import Literal

from app.core.cache import TTLCache
from app.domain import StrictModel
from app.services.ar_macro import ArMacroService
from app.sources.errors import SourceError
from app.sources.yahoo import YahooClient

logger = logging.getLogger(__name__)

# (clave, etiqueta, símbolo de Yahoo, formato)
YAHOO_ITEMS: list[tuple[str, str, str, Literal["price", "pct", "index"]]] = [
    ("spx", "S&P 500", "^GSPC", "index"),
    ("ndx", "Nasdaq 100", "^NDX", "index"),
    ("vix", "VIX", "^VIX", "index"),
    ("dxy", "DXY", "DX-Y.NYB", "index"),
    ("us10y", "Tesoro 10 años", "^TNX", "pct"),
    ("gold", "Oro", "GC=F", "price"),
    ("wti", "Petróleo WTI", "CL=F", "price"),
    ("btc", "Bitcoin", "BTC-USD", "price"),
    ("merval", "Merval", "^MERV", "index"),
]


STALE_AFTER = timedelta(days=4)


class LiveItem(StrictModel):
    key: str
    label: str
    value: float | None
    change: float | None  # fracción contra el cierre anterior (o diferencia en p.p. si es tasa)
    change_kind: Literal["pct", "pp"]
    format: Literal["price", "pct", "index", "ars"]
    time: datetime | None  # hora del dato en la fuente
    source: str
    error: str | None = None
    stale: bool = False  # el último dato tiene más de 4 días (la fuente dejó de actualizarlo)


class LiveStrip(StrictModel):
    items: list[LiveItem]
    generated_at: datetime


def tnx_to_pct(raw: float) -> float:
    """^TNX: algunas series de Yahoo lo informan × 10 (42,5 = 4,25 %). Un rendimiento del Tesoro
    a 10 años por encima de 20 % no es plausible, así que se corrige la escala."""
    return raw / 10 if raw > 20 else raw


class LiveService:
    def __init__(self, yahoo: YahooClient, ar: ArMacroService) -> None:
        self._yahoo = yahoo
        self._ar = ar
        self._cache: TTLCache[LiveStrip] = TTLCache(20)

    async def strip(self) -> LiveStrip:
        return await self._cache.get_or_load("strip", self._build)

    async def _yahoo_item(
        self, key: str, label: str, symbol: str, fmt: Literal["price", "pct", "index"]
    ) -> LiveItem:
        try:
            q = await self._yahoo.get_quote(symbol)
        except SourceError as exc:
            return LiveItem(
                key=key,
                label=label,
                value=None,
                change=None,
                change_kind="pct",
                format=fmt,
                time=None,
                source="Yahoo Finance",
                error=exc.message,
            )
        if fmt == "pct":
            value = tnx_to_pct(q.price)
            prev = tnx_to_pct(q.previous_close) if q.previous_close else None
            change = value - prev if prev is not None else None
            return LiveItem(
                key=key,
                label=label,
                value=value,
                change=change,
                change_kind="pp",
                format="pct",
                time=q.time,
                source="Yahoo Finance",
            )
        change = q.price / q.previous_close - 1.0 if q.previous_close else None
        return LiveItem(
            key=key,
            label=label,
            value=q.price,
            change=change,
            change_kind="pct",
            format=fmt,
            time=q.time,
            source="Yahoo Finance",
        )

    async def _argentina(self) -> list[LiveItem]:
        out: list[LiveItem] = []
        try:
            quotes = {q.house: q for q in await self._ar.dollar_quotes()}
            for house, label in (
                ("contadoconliqui", "CCL"),
                ("blue", "Blue"),
                ("oficial", "Oficial"),
            ):
                q = quotes.get(house)
                if q is None:
                    continue
                out.append(
                    LiveItem(
                        key=f"usd_{house}",
                        label=f"Dólar {label}",
                        value=q.sell,
                        change=None,
                        change_kind="pct",
                        format="ars",
                        time=q.updated_at,
                        source="DolarApi",
                    )
                )
        except SourceError as exc:
            out.append(
                LiveItem(
                    key="usd_ccl",
                    label="Dólar CCL",
                    value=None,
                    change=None,
                    change_kind="pct",
                    format="ars",
                    time=None,
                    source="DolarApi",
                    error=exc.message,
                )
            )
        try:
            points = await self._ar.country_risk_points()
            if points:
                last_d, last_v = points[-1]
                prev = points[-2][1] if len(points) > 1 else None
                out.append(
                    LiveItem(
                        key="riesgo_pais",
                        label="Riesgo país",
                        value=last_v,
                        change=(last_v / prev - 1.0) if prev else None,
                        change_kind="pct",
                        format="index",
                        time=datetime.combine(last_d, time(0), tzinfo=UTC),
                        source="ArgentinaDatos",
                    )
                )
        except SourceError as exc:
            logger.warning("cinta: sin riesgo país: %s", exc)
        return out

    async def _build(self) -> LiveStrip:
        yahoo = await asyncio.gather(*(self._yahoo_item(*i) for i in YAHOO_ITEMS))
        items = list(yahoo) + await self._argentina()
        now = datetime.now(UTC)
        for item in items:
            # 4 días cubre un fin de semana largo; más que eso es una serie que la fuente dejó
            # de actualizar (pasa con el Merval en Yahoo).
            item.stale = item.time is not None and now - item.time > STALE_AFTER
        return LiveStrip(items=items, generated_at=now)
