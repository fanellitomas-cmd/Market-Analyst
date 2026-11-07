"""Forense contable y valuación (Columna 2): Beneish, Altman, FCF − SBC, percentil del P/E,
quick ratio y ROIC frente a WACC.

Todo sale de los estados contables XBRL presentados a la SEC (10-K anuales) y de precios de Yahoo.
Reglas:
  - Cada indicador muestra los datos que usó. Si falta uno, el indicador queda "no disponible" y se
    listan los faltantes: no se completa con supuestos (salvo los casos marcados explícitamente,
    como tomar 0 de deuda corriente cuando la empresa sólo reporta la de largo plazo).
  - Los supuestos que el modelo necesita (la prima de riesgo del mercado en el WACC) son parámetros
    visibles y editables.
"""

import asyncio
import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from app.domain import SourceInfo, StrictModel
from app.services.earnings import REVENUE, EarningsService, latest_by_period
from app.services.markets import MarketsService
from app.services.us_macro import UsMacroService
from app.sources import sec
from app.sources.errors import SourceError
from app.sources.sec import Fact, extract_facts
from app.sources.yahoo import PriceSeries

logger = logging.getLogger(__name__)

YEARS = 6

FLOWS: dict[str, tuple[str, ...]] = {
    "revenue": REVENUE,
    "cogs": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    "sga": ("SellingGeneralAndAdministrativeExpense",),
    "depreciation": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ),
    "net_income": ("NetIncomeLoss",),
    "ebit": ("OperatingIncomeLoss",),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "sbc": ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    "tax": ("IncomeTaxExpenseBenefit",),
    "pretax": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "interest": ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"),
}
STOCKS: dict[str, tuple[str, ...]] = {
    "receivables": ("AccountsReceivableNetCurrent",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "ppe": ("PropertyPlantAndEquipmentNet",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "lt_debt_noncurrent": ("LongTermDebtNoncurrent",),
    "lt_debt_current": ("LongTermDebtCurrent", "DebtCurrent"),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "inventory": ("InventoryNet",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
}
LABELS = {
    "revenue": "ingresos",
    "cogs": "costo de ventas",
    "sga": "gastos de comercialización y administración",
    "depreciation": "depreciación",
    "net_income": "ganancia neta",
    "ebit": "resultado operativo",
    "cfo": "flujo operativo",
    "capex": "inversiones (capex)",
    "sbc": "compensación en acciones",
    "tax": "impuesto a las ganancias",
    "pretax": "ganancia antes de impuestos",
    "interest": "intereses pagados",
    "receivables": "cuentas por cobrar",
    "current_assets": "activo corriente",
    "current_liabilities": "pasivo corriente",
    "ppe": "propiedad, planta y equipo",
    "total_assets": "activo total",
    "total_liabilities": "pasivo total",
    "equity": "patrimonio",
    "lt_debt_noncurrent": "deuda de largo plazo",
    "lt_debt_current": "deuda de corto plazo",
    "retained_earnings": "resultados acumulados",
    "inventory": "inventario",
    "cash": "caja",
    "market_cap": "capitalización de mercado",
}
STATUTORY_TAX = 0.21  # tasa federal de EE.UU. desde 2018


# ---------- Estados anuales ----------


@dataclass
class Year:
    end: date
    v: dict[str, float | None]
    notes: list[str] = field(default_factory=list)

    def get(self, key: str) -> float | None:
        return self.v.get(key)


def _annual_flows(facts: dict[str, Any], concepts: tuple[str, ...]) -> dict[date, float]:
    chosen = latest_by_period([extract_facts(facts, c, "USD") for c in concepts])
    return {e: f.value for (s, e), f in chosen.items() if 350 <= (e - s).days <= 380}


def _instants(facts: dict[str, Any], concepts: tuple[str, ...]) -> dict[date, float]:
    out: dict[date, float] = {}
    for c in concepts:
        best: dict[date, Fact] = {}
        for f in extract_facts(facts, c, "USD"):
            if f.start is None and (f.end not in best or f.filed > best[f.end].filed):
                best[f.end] = f
        for d, f in best.items():
            out.setdefault(d, f.value)
    return out


def build_years(facts: dict[str, Any]) -> list[Year]:
    flows = {k: _annual_flows(facts, c) for k, c in FLOWS.items()}
    stocks = {k: _instants(facts, c) for k, c in STOCKS.items()}
    ends = sorted(flows["revenue"], reverse=True)[:YEARS]
    years: list[Year] = []
    for end in ends:
        v: dict[str, float | None] = {k: flows[k].get(end) for k in FLOWS}
        v |= {k: stocks[k].get(end) for k in STOCKS}
        y = Year(end=end, v=v)
        if (
            v["total_liabilities"] is None
            and v["total_assets"] is not None
            and v["equity"] is not None
        ):
            v["total_liabilities"] = v["total_assets"] - v["equity"]
            y.notes.append("pasivo total calculado como activo − patrimonio")
        nc, cur = v["lt_debt_noncurrent"], v["lt_debt_current"]
        if nc is None and cur is None:
            v["debt"] = None
        else:
            if nc is None or cur is None:
                which = "deuda de largo plazo" if nc is None else "deuda de corto plazo"
                y.notes.append(f"se tomó 0 de {which} porque la empresa no la reporta")
            v["debt"] = (nc or 0.0) + (cur or 0.0)
        years.append(y)
    return years


def _missing(year: Year, keys: list[str]) -> list[str]:
    return [LABELS.get(k, k) for k in keys if year.get(k) is None]


# ---------- Modelos de respuesta ----------


class ScoreResult(StrictModel):
    value: float | None
    zone: str | None  # lectura en palabras
    components: dict[str, float]
    missing: list[str]
    note: str | None = None


class YearScores(StrictModel):
    fiscal_year_end: date
    beneish: ScoreResult
    altman: ScoreResult
    quick_ratio: float | None
    roic: float | None
    fcf: float | None
    sbc: float | None
    fcf_minus_sbc: float | None
    sbc_to_revenue: float | None
    sbc_to_fcf: float | None
    revenue: float | None
    operating_margin: float | None
    notes: list[str]


class PeStats(StrictModel):
    current: float | None
    percentile: float | None  # 0-1: fracción de los días con un P/E menor o igual al actual
    median: float | None
    low: float | None
    high: float | None
    observations: int
    excluded_days: int  # días con ganancia de 12 meses ≤ 0 (P/E sin sentido)
    series: list[dict[str, float | str]]  # semanal: {"date", "pe"}
    note: str | None


class Wacc(StrictModel):
    value: float | None
    roic: float | None
    spread: float | None  # ROIC − WACC
    inputs: dict[str, float | None]
    assumptions: list[str]
    missing: list[str]


class ForensicResponse(StrictModel):
    ticker: str
    name: str
    sic: int | None
    sic_description: str | None
    altman_variant: Literal["original", "Z''"]
    years: list[YearScores]  # el más reciente primero
    pe: PeStats
    wacc: Wacc
    sources: list[SourceInfo]


# ---------- Cálculos ----------


def beneish(cur: Year, prev: Year | None) -> ScoreResult:
    """Modelo de 8 variables de Beneish (1999). M > −1,78 sugiere manipulación de resultados.
    AQI usa (activo corriente + PP&E) / activo total, sin inversiones financieras."""
    keys = [
        "revenue",
        "cogs",
        "receivables",
        "current_assets",
        "ppe",
        "total_assets",
        "depreciation",
        "sga",
        "current_liabilities",
        "net_income",
        "cfo",
    ]
    if prev is None:
        return ScoreResult(value=None, zone=None, components={}, missing=["año anterior"])
    missing = sorted(set(_missing(cur, keys) + _missing(prev, keys)))
    if missing:
        return ScoreResult(value=None, zone=None, components={}, missing=missing)

    def c(k: str) -> float:
        v = cur.get(k)
        assert v is not None
        return v

    def p(k: str) -> float:
        v = prev.get(k) if prev else None
        assert v is not None
        return v

    try:
        dsri = (c("receivables") / c("revenue")) / (p("receivables") / p("revenue"))
        gmi = ((p("revenue") - p("cogs")) / p("revenue")) / (
            (c("revenue") - c("cogs")) / c("revenue")
        )
        aqi = (1 - (c("current_assets") + c("ppe")) / c("total_assets")) / (
            1 - (p("current_assets") + p("ppe")) / p("total_assets")
        )
        sgi = c("revenue") / p("revenue")
        depi = (p("depreciation") / (p("depreciation") + p("ppe"))) / (
            c("depreciation") / (c("depreciation") + c("ppe"))
        )
        sgai = (c("sga") / c("revenue")) / (p("sga") / p("revenue"))
        lev_c = (c("current_liabilities") + (cur.get("lt_debt_noncurrent") or 0.0)) / c(
            "total_assets"
        )
        lev_p = (p("current_liabilities") + (prev.get("lt_debt_noncurrent") or 0.0)) / p(
            "total_assets"
        )
        lvgi = lev_c / lev_p
        tata = (c("net_income") - c("cfo")) / c("total_assets")
    except ZeroDivisionError:
        return ScoreResult(
            value=None,
            zone=None,
            components={},
            missing=[],
            note="algún componente divide por cero",
        )
    m = (
        -4.84
        + 0.920 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )
    comps = {
        "DSRI": dsri,
        "GMI": gmi,
        "AQI": aqi,
        "SGI": sgi,
        "DEPI": depi,
        "SGAI": sgai,
        "TATA": tata,
        "LVGI": lvgi,
    }
    zone = "posible manipulación (M > −1,78)" if m > -1.78 else "sin señales (M ≤ −1,78)"
    return ScoreResult(value=m, zone=zone, components=comps, missing=[])


def is_manufacturer(sic: int | None) -> bool:
    return sic is not None and 2000 <= sic <= 3999


def altman(year: Year, market_cap: float | None, manufacturer: bool) -> ScoreResult:
    """Original (industriales que cotizan): zonas 1,81 / 2,99. Z'' (resto): zonas 1,1 / 2,6."""
    base = [
        "current_assets",
        "current_liabilities",
        "total_assets",
        "retained_earnings",
        "ebit",
        "total_liabilities",
    ]
    keys = base + (["revenue"] if manufacturer else ["equity"])
    missing = _missing(year, keys)
    if manufacturer and market_cap is None:
        missing.append(LABELS["market_cap"])
    if missing:
        return ScoreResult(value=None, zone=None, components={}, missing=missing)
    ta = year.get("total_assets")
    tl = year.get("total_liabilities")
    assert ta and tl is not None
    if tl == 0:
        return ScoreResult(
            value=None, zone=None, components={}, missing=[], note="pasivo total en cero"
        )
    wc = (year.get("current_assets") or 0) - (year.get("current_liabilities") or 0)
    x1, x2, x3 = wc / ta, (year.get("retained_earnings") or 0) / ta, (year.get("ebit") or 0) / ta
    if manufacturer:
        assert market_cap is not None
        x4, x5 = market_cap / tl, (year.get("revenue") or 0) / ta
        z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
        zone = "segura" if z > 2.99 else "gris" if z >= 1.81 else "riesgo de quiebra"
        comps = {
            "X1 capital de trabajo/activo": x1,
            "X2 resultados acumulados/activo": x2,
            "X3 EBIT/activo": x3,
            "X4 capitalización/pasivo": x4,
            "X5 ventas/activo": x5,
        }
    else:
        x4 = (year.get("equity") or 0) / tl
        z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
        zone = "segura" if z > 2.6 else "gris" if z >= 1.1 else "riesgo de quiebra"
        comps = {
            "X1 capital de trabajo/activo": x1,
            "X2 resultados acumulados/activo": x2,
            "X3 EBIT/activo": x3,
            "X4 patrimonio/pasivo": x4,
        }
    return ScoreResult(value=z, zone=zone, components=comps, missing=[])


def tax_rate(year: Year) -> float | None:
    tax, pre = year.get("tax"), year.get("pretax")
    if tax is None or pre is None or pre <= 0:
        return None
    return tax / pre


def roic(year: Year) -> float | None:
    """EBIT × (1 − tasa efectiva) / (deuda + patrimonio − caja)."""
    ebit, equity, debt, cash = (
        year.get("ebit"),
        year.get("equity"),
        year.get("debt"),
        year.get("cash"),
    )
    t = tax_rate(year)
    if None in (ebit, equity, t) or debt is None:
        return None
    assert ebit is not None and equity is not None and t is not None
    invested = debt + equity - (cash or 0.0)
    if invested <= 0:
        return None
    return ebit * (1 - t) / invested


def year_scores(
    cur: Year, prev: Year | None, market_cap: float | None, manufacturer: bool
) -> YearScores:
    ca, cl, inv = cur.get("current_assets"), cur.get("current_liabilities"), cur.get("inventory")
    notes = list(cur.notes)
    quick = None
    if ca is not None and cl:
        quick = (ca - (inv or 0.0)) / cl
        if inv is None:
            notes.append("quick ratio sin inventario reportado (se tomó 0)")
    cfo, capex, sbc, rev = cur.get("cfo"), cur.get("capex"), cur.get("sbc"), cur.get("revenue")
    fcf = cfo - capex if cfo is not None and capex is not None else None
    ebit = cur.get("ebit")
    return YearScores(
        fiscal_year_end=cur.end,
        beneish=beneish(cur, prev),
        altman=altman(cur, market_cap, manufacturer),
        quick_ratio=quick,
        roic=roic(cur),
        fcf=fcf,
        sbc=sbc,
        fcf_minus_sbc=fcf - sbc if fcf is not None and sbc is not None else None,
        sbc_to_revenue=sbc / rev if sbc is not None and rev else None,
        sbc_to_fcf=sbc / fcf if sbc is not None and fcf and fcf > 0 else None,
        revenue=rev,
        operating_margin=ebit / rev if ebit is not None and rev else None,
        notes=notes,
    )


# ---------- Precio, acciones y P/E ----------


@dataclass(frozen=True)
class SharesPoint:
    as_of: date  # fecha del dato
    available: date  # fecha en que se presentó (para no usar datos del futuro)
    shares: float


def shares_history(facts: dict[str, Any]) -> list[SharesPoint]:
    rows = extract_facts(facts, "EntityCommonStockSharesOutstanding", "shares", "dei")
    best: dict[date, Fact] = {}
    for f in rows:
        if f.end not in best or f.filed < best[f.end].filed:
            best[f.end] = f  # la primera vez que se informó
    return sorted(
        (SharesPoint(d, f.filed, f.value) for d, f in best.items()), key=lambda s: s.available
    )


def split_factor_after(splits: list[tuple[date, float]], d: date) -> float:
    """Producto de los splits posteriores a `d`: lleva una cantidad de acciones de esa fecha a la
    base actual, la misma que usan los precios de Yahoo."""
    return math.prod(r for sd, r in splits if sd > d)


def market_cap_on(prices: PriceSeries, shares: list[SharesPoint], d: date) -> float | None:
    if not prices.split_closes:
        return None
    idx = max((i for i, pd in enumerate(prices.dates) if pd <= d), default=None)
    known = [s for s in shares if s.available <= d]
    if idx is None or not known or (d - prices.dates[idx]).days > 7:
        return None
    s = known[-1]
    return prices.split_closes[idx] * s.shares * split_factor_after(prices.splits, s.as_of)


@dataclass(frozen=True)
class TtmPoint:
    available: date
    period_end: date
    value: float


def ttm_net_income(facts: dict[str, Any]) -> list[TtmPoint]:
    """Ganancia neta de los últimos 12 meses, disponible desde que se presentó su último dato."""
    chosen = latest_by_period([extract_facts(facts, c, "USD") for c in FLOWS["net_income"]])
    quarters = {k: f for k, f in chosen.items() if 80 <= (k[1] - k[0]).days <= 100}
    annuals = {k: f for k, f in chosen.items() if 350 <= (k[1] - k[0]).days <= 380}
    q: list[tuple[date, date, float, date]] = [
        (s, e, f.value, f.filed) for (s, e), f in quarters.items()
    ]
    # 4.º trimestre implícito: anual − 3 trimestres (presentado junto con el anual).
    ends = {e for _, e, _, _ in q}
    for (a_s, a_e), af in annuals.items():
        if a_e in ends:
            continue
        inside = sorted(x for x in q if x[0] >= a_s - timedelta(days=5) and x[1] < a_e)
        if len(inside) == 3:
            q.append(
                (
                    inside[-1][1] + timedelta(days=1),
                    a_e,
                    af.value - math.fsum(x[2] for x in inside),
                    af.filed,
                )
            )
    q.sort(key=lambda x: x[1])
    out: list[TtmPoint] = []
    for i in range(3, len(q)):
        window = q[i - 3 : i + 1]
        contiguous = all(abs((window[j + 1][0] - window[j][1]).days) <= 5 for j in range(3))
        if contiguous:
            out.append(
                TtmPoint(max(x[3] for x in window), window[-1][1], math.fsum(x[2] for x in window))
            )
    return out


def pe_stats(prices: PriceSeries, shares: list[SharesPoint], ttm: list[TtmPoint]) -> PeStats:
    def empty(note: str) -> PeStats:
        return PeStats(
            current=None,
            percentile=None,
            median=None,
            low=None,
            high=None,
            observations=0,
            excluded_days=0,
            series=[],
            note=note,
        )

    if not prices.split_closes:
        return empty("la fuente no trae el cierre sin ajustar por dividendos")
    if not shares:
        return empty("la empresa no informa la cantidad de acciones en circulación")
    if not ttm:
        return empty("no hay 4 trimestres consecutivos de ganancia neta")
    by_avail = sorted(ttm, key=lambda t: t.available)
    pes: list[tuple[date, float]] = []
    excluded = 0
    si = ti = 0
    for d, close in zip(prices.dates, prices.split_closes, strict=True):
        while si + 1 < len(shares) and shares[si + 1].available <= d:
            si += 1
        while ti + 1 < len(by_avail) and by_avail[ti + 1].available <= d:
            ti += 1
        if shares[si].available > d or by_avail[ti].available > d:
            continue
        earnings = by_avail[ti].value
        if earnings <= 0:
            excluded += 1
            continue
        mcap = close * shares[si].shares * split_factor_after(prices.splits, shares[si].as_of)
        pes.append((d, mcap / earnings))
    if not pes:
        return empty("sin días con ganancia positiva en el período")
    values = [p for _, p in pes]
    current = values[-1]
    return PeStats(
        current=current,
        percentile=sum(1 for v in values if v <= current) / len(values),
        median=statistics.median(values),
        low=min(values),
        high=max(values),
        observations=len(values),
        excluded_days=excluded,
        series=[{"date": d.isoformat(), "pe": round(p, 2)} for d, p in pes[::5]]
        + (
            [{"date": pes[-1][0].isoformat(), "pe": round(pes[-1][1], 2)}]
            if (len(pes) - 1) % 5
            else []
        ),
        note=None if pes[-1][0] == prices.dates[-1] else "el P/E actual no usa el último precio",
    )


def wacc(
    year: Year | None, market_cap: float | None, beta: float | None, rf: float | None, erp: float
) -> Wacc:
    """Ke = tasa libre + beta × prima de riesgo; Kd = intereses / deuda; pesos a valor de mercado
    para el patrimonio y contable para la deuda."""
    assumptions = [f"prima de riesgo del mercado: {erp:.1f} % (editable)"]
    missing: list[str] = []
    if beta is None:
        missing.append("beta contra el S&P 500")
    if rf is None:
        missing.append("tasa del Tesoro a 10 años")
    if market_cap is None:
        missing.append(LABELS["market_cap"])
    if year is None:
        missing.append("último balance anual")
    inputs: dict[str, float | None] = {
        "beta": beta,
        "tasa libre de riesgo %": rf,
        "prima de riesgo %": erp,
        "capitalización": market_cap,
    }
    if missing or year is None or beta is None or rf is None or market_cap is None:
        return Wacc(
            value=None,
            roic=roic(year) if year else None,
            spread=None,
            inputs=inputs,
            assumptions=assumptions,
            missing=missing,
        )
    t = tax_rate(year)
    if t is None:
        t = STATUTORY_TAX
        assumptions.append("tasa de impuesto: 21 % (federal; la efectiva no se pudo calcular)")
    debt = year.get("debt") or 0.0
    if year.get("debt") is None:
        assumptions.append("deuda financiera no reportada: se tomó 0")
    interest = year.get("interest")
    ke = (rf + beta * erp) / 100
    kd = interest / debt if interest is not None and debt > 0 else None
    if debt > 0 and kd is None:
        return Wacc(
            value=None,
            roic=roic(year),
            spread=None,
            inputs=inputs,
            assumptions=assumptions,
            missing=["intereses pagados"],
        )
    total = market_cap + debt
    value = market_cap / total * ke + (debt / total * (kd or 0) * (1 - t))
    inputs |= {
        "costo del patrimonio %": ke * 100,
        "costo de la deuda %": (kd or 0) * 100,
        "tasa de impuesto %": t * 100,
        "deuda": debt,
    }
    r = roic(year)
    return Wacc(
        value=value,
        roic=r,
        spread=r - value if r is not None else None,
        inputs=inputs,
        assumptions=assumptions,
        missing=[],
    )


# ---------- Servicio ----------


class ForensicService:
    def __init__(
        self, earnings: EarningsService, markets: MarketsService, us: UsMacroService
    ) -> None:
        self._earnings = earnings
        self._markets = markets
        self._us = us

    @property
    def configured(self) -> bool:
        return self._earnings.configured

    async def analyze(self, ticker: str, erp: float = 5.0) -> ForensicResponse:
        ref = await self._earnings.resolve(ticker)
        facts, company = await asyncio.gather(
            self._earnings.company_facts(ref), self._earnings.company_filings(ref)
        )
        prices, beta, rf = await asyncio.gather(
            self._safe(self._markets.get_prices(ref.ticker, "5y")),
            self._beta(ref.ticker),
            self._rf(),
        )
        years = build_years(facts)
        shares = shares_history(facts)
        manufacturer = is_manufacturer(company.sic)

        scored: list[YearScores] = []
        for i, y in enumerate(years):
            prev = years[i + 1] if i + 1 < len(years) else None
            mcap = market_cap_on(prices, shares, y.end) if prices else None
            scored.append(year_scores(y, prev, mcap, manufacturer))

        current_mcap = market_cap_on(prices, shares, prices.dates[-1]) if prices else None
        pe = (
            pe_stats(prices, shares, ttm_net_income(facts))
            if prices
            else PeStats(
                current=None,
                percentile=None,
                median=None,
                low=None,
                high=None,
                observations=0,
                excluded_days=0,
                series=[],
                note="sin precios",
            )
        )
        now = datetime.now(UTC)
        return ForensicResponse(
            ticker=ref.ticker,
            name=ref.name,
            sic=company.sic,
            sic_description=company.sic_description,
            altman_variant="original" if manufacturer else "Z''",
            years=scored,
            pe=pe,
            wacc=wacc(years[0] if years else None, current_mcap, beta, rf, erp),
            sources=[
                SourceInfo(
                    name=sec.SOURCE,
                    url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ref.cik:010d}",
                    fetched_at=now,
                ),
                SourceInfo(
                    name="Yahoo Finance",
                    url=f"https://finance.yahoo.com/quote/{ref.ticker}",
                    fetched_at=now,
                ),
                SourceInfo(
                    name="FRED DGS10",
                    url="https://fred.stlouisfed.org/series/DGS10",
                    fetched_at=now,
                ),
            ],
        )

    async def _safe(self, coro: Any) -> PriceSeries | None:
        try:
            result: PriceSeries = await coro
            return result
        except SourceError as exc:
            logger.warning("forense: sin precios: %s", exc)
            return None

    async def _beta(self, ticker: str) -> float | None:
        try:
            rel = await self._markets.relative(ticker, "SPY", "5y")
        except SourceError:
            return None
        return rel.metrics.beta.value

    async def _rf(self) -> float | None:
        try:
            s = await self._us.raw("DGS10")
        except SourceError:
            return None
        return s.values[-1] if s.values else None
