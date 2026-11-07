"""Motor técnico (Columna 3): régimen, AVWAP y perfil de volumen.

Régimen (se clasifica ANTES de usar cualquier oscilador):
  - Tendencia alcista: EMA20 > EMA50 > EMA200 y ADX(14) > 25.
  - Tendencia bajista: EMA20 < EMA50 < EMA200 y ADX(14) > 25.
  - Rango en compresión: ADX ≤ 25 y ancho de Bollinger(20, 2) en el 20 % más bajo del último año.
  - Rango lateral: ADX ≤ 25 sin compresión.
  - Sin régimen claro: el resto (por ejemplo, ADX alto con medias desordenadas).

AVWAP (precio promedio ponderado por volumen desde un ancla): se calcula con velas de 1 hora y
precio típico (máximo + mínimo + cierre) / 3, que es la aproximación estándar cuando no hay datos
operación por operación. Anclas automáticas: última publicación de resultados (8-K), máximo y
mínimo de 52 semanas, inicio del año; más una fecha elegida por el usuario.

Perfil de volumen: velas de 30 minutos de los últimos 60 días. El volumen de cada vela se reparte
en partes iguales entre los niveles de precio que recorrió (aproximación: sin datos tick a tick no
se sabe a qué precio exacto se operó). Área de valor = 70 % del volumen alrededor del POC.

Todas las funciones son puras y no rellenan datos: una vela sin volumen se excluye y se cuenta.
"""

import asyncio
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from app.domain import SourceInfo, StrictModel
from app.services.earnings import EarningsService
from app.services.markets import MarketsService
from app.sources.errors import SourceError
from app.sources.yahoo import Bar, BarInterval

logger = logging.getLogger(__name__)

ADX_TREND = 25.0
SQUEEZE_PERCENTILE = 0.20
VALUE_AREA = 0.70
PROFILE_BINS = 48


# ---------- Indicadores ----------


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """EMA estándar, sembrada con la media simple de los primeros `period` valores."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    current = math.fsum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = alpha * values[i] + (1 - alpha) * current
        out[i] = current
    return out


def adx(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    """ADX de Wilder. Necesita al menos 2 × period velas."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < 2 * period + 1:
        return out
    tr, pdm, mdm = [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(1, n):
        h, lo, pc = bars[i].high, bars[i].low, bars[i - 1].close
        tr[i] = max(h - lo, abs(h - pc), abs(lo - pc))
        up, down = h - bars[i - 1].high, bars[i - 1].low - lo
        pdm[i] = up if up > down and up > 0 else 0.0
        mdm[i] = down if down > up and down > 0 else 0.0

    s_tr = math.fsum(tr[1 : period + 1])
    s_p = math.fsum(pdm[1 : period + 1])
    s_m = math.fsum(mdm[1 : period + 1])
    dx: list[float] = []
    for i in range(period, n):
        if i > period:
            s_tr = s_tr - s_tr / period + tr[i]
            s_p = s_p - s_p / period + pdm[i]
            s_m = s_m - s_m / period + mdm[i]
        if s_tr == 0:
            dx.append(0.0)
            continue
        pdi, mdi = 100 * s_p / s_tr, 100 * s_m / s_tr
        dx.append(0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi))
        if len(dx) == period:
            out[i] = math.fsum(dx) / period
        elif len(dx) > period:
            prev = out[i - 1]
            assert prev is not None
            out[i] = (prev * (period - 1) + dx[-1]) / period
    return out


def bollinger_width(
    closes: Sequence[float], period: int = 20, k: float = 2.0
) -> list[float | None]:
    """Ancho relativo de las bandas: (superior − inferior) / media."""
    out: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = math.fsum(window) / period
        sd = math.sqrt(math.fsum((x - mean) ** 2 for x in window) / period)
        out[i] = (2 * k * sd) / mean if mean else None
    return out


def percentile_rank(history: Sequence[float], value: float) -> float:
    """Fracción de la historia que es menor o igual al valor."""
    return sum(1 for h in history if h <= value) / len(history)


def vwap(bars: Sequence[Bar]) -> tuple[list[float | None], int]:
    """VWAP acumulado desde la primera vela. Devuelve la serie y cuántas velas sin volumen se
    excluyeron (su posición queda con el último valor válido, no con uno inventado)."""
    pv = vol = 0.0
    out: list[float | None] = []
    skipped = 0
    for b in bars:
        if b.volume is None or b.volume <= 0:
            skipped += 1
        else:
            typical = (b.high + b.low + b.close) / 3
            pv += typical * b.volume
            vol += b.volume
        out.append(pv / vol if vol > 0 else None)
    return out, skipped


@dataclass(frozen=True)
class Profile:
    bins: list[tuple[float, float, float]]  # (precio desde, precio hasta, volumen)
    poc: float
    vah: float
    val: float
    bars_used: int
    bars_skipped: int


def volume_profile(bars: Sequence[Bar], n_bins: int = PROFILE_BINS) -> Profile | None:
    usable = [b for b in bars if b.volume is not None and b.volume > 0]
    if len(usable) < 10:
        return None
    lo = min(b.low for b in usable)
    hi = max(b.high for b in usable)
    if hi <= lo:
        return None
    step = (hi - lo) / n_bins
    volume = [0.0] * n_bins
    for b in usable:
        assert b.volume is not None
        first = min(int((b.low - lo) / step), n_bins - 1)
        last = min(int((b.high - lo) / step), n_bins - 1)
        share = b.volume / (last - first + 1)
        for j in range(first, last + 1):
            volume[j] += share

    # Si varios niveles empatan en volumen (velas que recorren el mismo rango), el POC es el del
    # medio entre ellos y no el primero, que sesgaría el resultado hacia abajo.
    top = max(volume)
    tied = [j for j in range(n_bins) if volume[j] >= top * (1 - 1e-9)]
    poc_i = tied[len(tied) // 2]
    total = math.fsum(volume)
    lo_i = hi_i = poc_i
    acc = volume[poc_i]
    while acc < VALUE_AREA * total and (lo_i > 0 or hi_i < n_bins - 1):
        below = volume[lo_i - 1] if lo_i > 0 else -1.0
        above = volume[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        if above >= below:
            hi_i += 1
            acc += above
        else:
            lo_i -= 1
            acc += below
    bins = [(lo + j * step, lo + (j + 1) * step, volume[j]) for j in range(n_bins)]
    return Profile(
        bins=bins,
        poc=lo + (poc_i + 0.5) * step,
        vah=lo + (hi_i + 1) * step,
        val=lo + lo_i * step,
        bars_used=len(usable),
        bars_skipped=len(bars) - len(usable),
    )


RegimeKind = Literal["uptrend", "downtrend", "squeeze", "range", "unclear", "insufficient"]

REGIME_TEXT: dict[RegimeKind, tuple[str, str]] = {
    "uptrend": (
        "Tendencia alcista",
        "Medias ordenadas al alza y tendencia con fuerza (ADX > 25). En tendencia, los "
        "osciladores marcan sobrecompra durante mucho tiempo: no sirven para vender.",
    ),
    "downtrend": (
        "Tendencia bajista",
        "Medias ordenadas a la baja y tendencia con fuerza (ADX > 25). Los rebotes "
        "suelen frenarse en las medias o en el AVWAP.",
    ),
    "squeeze": (
        "Rango en compresión",
        "Sin tendencia y con la volatilidad en mínimos del año: suele preceder una "
        "ruptura. Conviene esperar la confirmación con volumen.",
    ),
    "range": (
        "Rango lateral",
        "Sin tendencia definida. En rango, los osciladores y los extremos del área de "
        "valor funcionan mejor que en tendencia.",
    ),
    "unclear": (
        "Sin régimen claro",
        "Las señales no coinciden (por ejemplo, fuerza de tendencia sin medias "
        "ordenadas). Mejor no forzar una lectura.",
    ),
    "insufficient": (
        "Historia insuficiente",
        "Se necesitan al menos 252 ruedas para la EMA de 200 y el percentil de Bollinger.",
    ),
}


def classify(e20: float, e50: float, e200: float, adx_v: float, bb_pct: float) -> RegimeKind:
    aligned_up = e20 > e50 > e200
    aligned_down = e20 < e50 < e200
    if adx_v > ADX_TREND and aligned_up:
        return "uptrend"
    if adx_v > ADX_TREND and aligned_down:
        return "downtrend"
    if adx_v <= ADX_TREND:
        return "squeeze" if bb_pct <= SQUEEZE_PERCENTILE else "range"
    return "unclear"


# ---------- Modelos ----------


class Condition(StrictModel):
    name: str
    ok: bool
    detail: str


class Regime(StrictModel):
    kind: RegimeKind
    label: str
    explanation: str
    ema20: float | None
    ema50: float | None
    ema200: float | None
    adx: float | None
    bb_width: float | None
    bb_width_percentile: float | None
    conditions: list[Condition]


class VolumeStats(StrictModel):
    last: float | None
    avg50: float | None
    zscore: float | None  # desvíos estándar del volumen de hoy contra las últimas 50 ruedas
    relative: float | None  # volumen de hoy / promedio de 50 ruedas


class TimeValue(StrictModel):
    time: str  # fecha ISO (diario)
    value: float


class Avwap(StrictModel):
    key: str
    label: str
    anchor: datetime | None
    value: float | None
    distance: float | None  # cierre / AVWAP − 1
    status: Literal["OK", "NO_DISPONIBLE"]
    note: str | None
    series: list[TimeValue]


class ProfileBin(StrictModel):
    low: float
    high: float
    volume: float


class ProfileOut(StrictModel):
    interval: str
    range: str
    bins: list[ProfileBin]
    poc: float
    vah: float
    val: float
    bars_used: int
    bars_skipped: int


class Candle(StrictModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None


class EmaPoint(StrictModel):
    time: str
    ema20: float | None
    ema50: float | None
    ema200: float | None


class TechnicalResponse(StrictModel):
    symbol: str
    name: str | None
    currency: str | None
    as_of: date
    close: float
    regime: Regime
    volume: VolumeStats
    avwaps: list[Avwap]
    profile: ProfileOut | None
    profile_note: str | None
    candles: list[Candle]  # último año, diarias
    emas: list[EmaPoint]
    notes: list[str]
    sources: list[SourceInfo]


# ---------- Servicio ----------


CHART_DAYS = 260


class TechnicalService:
    def __init__(self, markets: MarketsService, earnings: EarningsService) -> None:
        self._markets = markets
        self._earnings = earnings

    async def analyze(self, symbol: str, custom_anchor: date | None = None) -> TechnicalResponse:
        daily = await self._markets.get_bars(symbol, "1d", "2y")
        if len(daily.bars) < 30:
            raise SourceError("Yahoo Finance", f"historia diaria insuficiente para {symbol}")
        hourly, intraday, release = await asyncio.gather(
            self._safe_bars(symbol, "1h", "2y"),
            self._safe_bars(symbol, "30m", "60d"),
            self._earnings_anchor(symbol),
        )
        notes: list[str] = []
        bars = daily.bars
        closes = [b.close for b in bars]
        e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
        adx_s = adx(bars)
        bbw = bollinger_width(closes)
        regime = _regime(e20[-1], e50[-1], e200[-1], adx_s[-1], bbw)

        # --- AVWAP ---
        avwaps: list[Avwap] = []
        last_close = closes[-1]
        year = bars[-1].time.year
        window = bars[-252:]
        high_bar = max(window, key=lambda b: b.high)
        low_bar = min(window, key=lambda b: b.low)
        anchors: list[tuple[str, str, datetime | None, str | None]] = [
            ("earnings", "Último balance", release[0], release[1]),
            (
                "high_52w",
                f"Máximo 52 sem. ({high_bar.time.date().isoformat()})",
                _start_of(high_bar.time),
                None,
            ),
            (
                "low_52w",
                f"Mínimo 52 sem. ({low_bar.time.date().isoformat()})",
                _start_of(low_bar.time),
                None,
            ),
            ("ytd", f"Inicio de {year}", _first_of_year(bars, year), None),
        ]
        if custom_anchor is not None:
            anchors.append(
                (
                    "custom",
                    f"Ancla elegida ({custom_anchor.isoformat()})",
                    datetime.combine(custom_anchor, time(0), tzinfo=bars[-1].time.tzinfo),
                    None,
                )
            )
        for key, label, anchor, note in anchors:
            avwaps.append(_avwap(key, label, anchor, note, hourly, last_close))

        # --- Volumen ---
        vols = [b.volume for b in bars[-51:-1] if b.volume is not None and b.volume > 0]
        last_vol = bars[-1].volume
        vstats = VolumeStats(last=last_vol, avg50=None, zscore=None, relative=None)
        if len(vols) >= 30 and last_vol is not None:
            mean = math.fsum(vols) / len(vols)
            sd = math.sqrt(math.fsum((v - mean) ** 2 for v in vols) / (len(vols) - 1))
            vstats = VolumeStats(
                last=last_vol,
                avg50=mean,
                zscore=(last_vol - mean) / sd if sd > 0 else None,
                relative=last_vol / mean if mean > 0 else None,
            )
        elif last_vol is None:
            notes.append("La fuente no informa volumen para este activo (común en índices).")

        # --- Perfil de volumen ---
        profile_out: ProfileOut | None = None
        profile_note: str | None = None
        if intraday is None:
            profile_note = "Sin velas intradía de la fuente."
        else:
            prof = volume_profile(intraday)
            if prof is None:
                profile_note = "No hay suficientes velas con volumen para armar el perfil."
            else:
                profile_out = ProfileOut(
                    interval="30m",
                    range="60d",
                    bins=[ProfileBin(low=a, high=b, volume=v) for a, b, v in prof.bins],
                    poc=prof.poc,
                    vah=prof.vah,
                    val=prof.val,
                    bars_used=prof.bars_used,
                    bars_skipped=prof.bars_skipped,
                )

        chart = bars[-CHART_DAYS:]
        offset = len(bars) - len(chart)
        now = datetime.now(UTC)
        return TechnicalResponse(
            symbol=symbol,
            name=daily.name,
            currency=daily.currency,
            as_of=bars[-1].time.date(),
            close=last_close,
            regime=regime,
            volume=vstats,
            avwaps=avwaps,
            profile=profile_out,
            profile_note=profile_note,
            candles=[
                Candle(
                    time=b.time.date().isoformat(),
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                )
                for b in chart
            ],
            emas=[
                EmaPoint(
                    time=b.time.date().isoformat(),
                    ema20=e20[offset + i],
                    ema50=e50[offset + i],
                    ema200=e200[offset + i],
                )
                for i, b in enumerate(chart)
            ],
            notes=notes,
            sources=[
                SourceInfo(
                    name="Yahoo Finance",
                    url=f"https://finance.yahoo.com/quote/{symbol}",
                    fetched_at=now,
                ),
            ],
        )

    async def _safe_bars(self, symbol: str, interval: BarInterval, rng: str) -> list[Bar] | None:
        try:
            data = await self._markets.get_bars(symbol, interval, rng)
        except SourceError as exc:
            logger.warning("técnico: sin velas %s de %s: %s", interval, symbol, exc)
            return None
        return data.bars

    async def _earnings_anchor(self, symbol: str) -> tuple[datetime | None, str | None]:
        if not self._earnings.configured:
            return None, "Falta SEC_USER_AGENT para conocer la fecha del último balance."
        try:
            release = await self._earnings.latest_release(symbol)
        except SourceError as exc:
            return None, f"Sin fecha de balance: {exc.message}"
        if release is None:
            return None, "No hay publicaciones de resultados (8-K) recientes."
        return release, None


def _start_of(dt: datetime) -> datetime:
    return datetime.combine(dt.date(), time(0), tzinfo=dt.tzinfo)


def _first_of_year(bars: Sequence[Bar], year: int) -> datetime | None:
    first = next((b for b in bars if b.time.year == year), None)
    return _start_of(first.time) if first else None


def anchor_start(release: datetime, bars: Sequence[Bar]) -> int | None:
    """Índice de la primera vela que ya refleja la publicación.

    Después del cierre (≥ 16:00 NY): la primera vela de la rueda siguiente. Antes o durante la
    rueda: la primera vela que empieza en ese momento o después.
    """
    if not bars:
        return None
    local = release.astimezone(bars[0].time.tzinfo)
    after_close = local.time() >= time(16, 0)
    for i, b in enumerate(bars):
        if after_close and b.time.date() > local.date():
            return i
        if not after_close and b.time >= local:
            return i
    return None


def _avwap(
    key: str,
    label: str,
    anchor: datetime | None,
    note: str | None,
    hourly: list[Bar] | None,
    last_close: float,
) -> Avwap:
    def missing(reason: str) -> Avwap:
        return Avwap(
            key=key,
            label=label,
            anchor=anchor,
            value=None,
            distance=None,
            status="NO_DISPONIBLE",
            note=reason,
            series=[],
        )

    if anchor is None:
        return missing(note or "sin fecha de ancla")
    if not hourly:
        return missing("sin velas de 1 hora de la fuente")
    if anchor < hourly[0].time - timedelta(days=7):
        # Arrancar más tarde daría otro AVWAP: mejor no mostrarlo.
        return missing("el ancla es anterior a la historia de velas de 1 hora (2 años)")
    if key == "earnings":
        start = anchor_start(anchor, hourly)
    else:
        start = next((i for i, b in enumerate(hourly) if b.time >= anchor), None)
    if start is None:
        return missing("el ancla es posterior a la última vela")
    segment = hourly[start:]
    values, skipped = vwap(segment)
    # Un punto por día (el último valor de cada rueda) para dibujarlo sobre las velas diarias.
    daily: dict[str, float] = {}
    for b, v in zip(segment, values, strict=True):
        if v is not None:
            daily[b.time.date().isoformat()] = v
    current = values[-1] if values else None
    if current is None:
        return missing("ninguna vela desde el ancla informa volumen")
    extra = f"{skipped} velas sin volumen excluidas" if skipped else None
    return Avwap(
        key=key,
        label=label,
        anchor=segment[0].time,
        value=current,
        distance=last_close / current - 1.0,
        status="OK",
        note=extra,
        series=[TimeValue(time=t, value=v) for t, v in daily.items()],
    )


def _regime(
    e20: float | None,
    e50: float | None,
    e200: float | None,
    adx_v: float | None,
    bbw: list[float | None],
) -> Regime:
    widths = [w for w in bbw[-252:] if w is not None]
    current_w = bbw[-1]
    if None in (e20, e50, e200, adx_v, current_w) or len(widths) < 200:
        label, text = REGIME_TEXT["insufficient"]
        return Regime(
            kind="insufficient",
            label=label,
            explanation=text,
            ema20=e20,
            ema50=e50,
            ema200=e200,
            adx=adx_v,
            bb_width=current_w,
            bb_width_percentile=None,
            conditions=[],
        )
    assert e20 is not None and e50 is not None and e200 is not None
    assert adx_v is not None and current_w is not None
    pct = percentile_rank(widths, current_w)
    kind = classify(e20, e50, e200, adx_v, pct)
    label, text = REGIME_TEXT[kind]
    conditions = [
        Condition(
            name="EMA20 > EMA50 > EMA200",
            ok=e20 > e50 > e200,
            detail=f"{e20:.2f} / {e50:.2f} / {e200:.2f}",
        ),
        Condition(
            name="EMA20 < EMA50 < EMA200",
            ok=e20 < e50 < e200,
            detail=f"{e20:.2f} / {e50:.2f} / {e200:.2f}",
        ),
        Condition(name="ADX > 25 (hay tendencia)", ok=adx_v > ADX_TREND, detail=f"ADX {adx_v:.1f}"),
        Condition(
            name="Bollinger en compresión (≤ percentil 20 del año)",
            ok=pct <= SQUEEZE_PERCENTILE,
            detail=f"percentil {pct * 100:.0f}",
        ),
    ]
    return Regime(
        kind=kind,
        label=label,
        explanation=text,
        ema20=e20,
        ema50=e50,
        ema200=e200,
        adx=adx_v,
        bb_width=current_w,
        bb_width_percentile=pct,
        conditions=conditions,
    )
