"""Columna 2: forense contable."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.services.forensic import (
    Year,
    altman,
    beneish,
    build_years,
    is_manufacturer,
    pe_stats,
    roic,
    split_factor_after,
    ttm_net_income,
    wacc,
)
from app.sources.sec import CompanyFilings
from app.sources.yahoo import PriceSeries
from tests.conftest import Router

BASE: dict[str, float | None] = {
    "revenue": 1000,
    "cogs": 600,
    "receivables": 100,
    "current_assets": 400,
    "ppe": 300,
    "total_assets": 1000,
    "depreciation": 50,
    "sga": 150,
    "current_liabilities": 200,
    "net_income": 80,
    "cfo": 120,
    "lt_debt_noncurrent": 200,
    "retained_earnings": 300,
    "ebit": 120,
    "total_liabilities": 500,
    "equity": 500,
    "tax": 21,
    "pretax": 100,
    "cash": 50,
    "debt": 250,
    "interest": 10,
    "inventory": 80,
}


def year(scale: float = 1.0, **overrides: float | None) -> Year:
    v = {k: (x * scale if x is not None else None) for k, x in BASE.items()}
    v.update(overrides)
    return Year(end=date(2025, 12, 31), v=v)


# ---------- Beneish ----------


def test_beneish_with_uniform_growth() -> None:
    prev, cur = year(1.0), year(1.1)
    r = beneish(cur, prev)
    # Todo creció 10 %: todos los índices valen 1 salvo SGI = 1,1. TATA = (88 − 132) / 1100.
    tata = (88 - 132) / 1100
    expected = -4.84 + 0.920 + 0.528 + 0.404 + 0.892 * 1.1 + 0.115 - 0.172 + 4.679 * tata - 0.327
    assert r.value == pytest.approx(expected)
    assert r.components["SGI"] == pytest.approx(1.1)
    assert r.components["DSRI"] == pytest.approx(1.0)
    assert r.zone is not None and r.zone.startswith("sin señales")


def test_beneish_flags_aggressive_accruals() -> None:
    prev = year(1.0)
    # Cuentas por cobrar que se triplican y ganancia sin respaldo de caja.
    cur = year(1.1, receivables=330, net_income=200, cfo=20)
    r = beneish(cur, prev)
    assert r.value is not None and r.value > -1.78
    assert r.zone is not None and "posible manipulación" in r.zone


def test_beneish_lists_missing_inputs_instead_of_guessing() -> None:
    r = beneish(year(1.1, sga=None), year(1.0))
    assert r.value is None
    assert r.missing == ["gastos de comercialización y administración"]
    assert beneish(year(), None).missing == ["año anterior"]


# ---------- Altman ----------


def test_altman_original_and_z_double_prime() -> None:
    y = year()
    z = altman(y, market_cap=2000, manufacturer=True)
    expected = 1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.12 + 0.6 * (2000 / 500) + 1.0 * 1.0
    assert z.value == pytest.approx(expected)
    assert z.zone == "segura"
    z2 = altman(y, market_cap=None, manufacturer=False)
    assert z2.value == pytest.approx(6.56 * 0.2 + 3.26 * 0.3 + 6.72 * 0.12 + 1.05 * 1.0)
    missing = altman(y, market_cap=None, manufacturer=True)
    assert missing.value is None and "capitalización de mercado" in missing.missing


def test_manufacturer_by_sic() -> None:
    assert is_manufacturer(3571) and not is_manufacturer(7372) and not is_manufacturer(None)


# ---------- ROIC y WACC ----------


def test_roic_and_wacc() -> None:
    y = year()
    # EBIT 120 × (1 − 0,21) / (250 + 500 − 50)
    assert roic(y) == pytest.approx(120 * 0.79 / 700)
    w = wacc(y, market_cap=2000, beta=1.2, rf=4.0, erp=5.0)
    ke, kd = (4.0 + 1.2 * 5.0) / 100, 10 / 250
    expected = 2000 / 2250 * ke + 250 / 2250 * kd * 0.79
    assert w.value == pytest.approx(expected)
    assert w.spread == pytest.approx(120 * 0.79 / 700 - expected)
    assert any("5.0 %" in a for a in w.assumptions)
    no_beta = wacc(y, 2000, None, 4.0, 5.0)
    assert no_beta.value is None and no_beta.missing == ["beta contra el S&P 500"]


def test_roic_needs_positive_pretax() -> None:
    assert roic(year(pretax=-5)) is None


# ---------- Estados anuales desde XBRL ----------


def fact(start: str | None, end: str, val: float, filed: str = "2026-02-01") -> dict[str, Any]:
    row: dict[str, Any] = {"end": end, "val": val, "filed": filed, "form": "10-K"}
    if start:
        row["start"] = start
    return row


def test_build_years_and_debt_note() -> None:
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            fact("2025-01-01", "2025-12-31", 1000),
                            fact("2025-07-01", "2025-09-30", 260),
                        ]
                    }
                },
                "Assets": {"units": {"USD": [fact(None, "2025-12-31", 900)]}},
                "StockholdersEquity": {"units": {"USD": [fact(None, "2025-12-31", 400)]}},
                "LongTermDebtNoncurrent": {"units": {"USD": [fact(None, "2025-12-31", 150)]}},
            }
        }
    }
    [y] = build_years(facts)
    assert y.end == date(2025, 12, 31)
    assert y.get("revenue") == 1000  # el trimestral no se mezcla con el anual
    assert y.get("total_liabilities") == 500  # activo − patrimonio
    assert y.get("debt") == 150
    assert any("activo − patrimonio" in n for n in y.notes)
    assert any("deuda de corto plazo" in n for n in y.notes)


def test_ttm_net_income_with_implied_q4() -> None:
    rows = [
        fact("2025-01-01", "2025-03-31", 10, "2025-05-01"),
        fact("2025-04-01", "2025-06-30", 20, "2025-08-01"),
        fact("2025-07-01", "2025-09-30", 30, "2025-11-01"),
        fact("2025-01-01", "2025-12-31", 100, "2026-02-15"),
    ]
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": rows}}}}}
    [ttm] = ttm_net_income(facts)
    assert ttm.value == 100 and ttm.period_end == date(2025, 12, 31)
    assert ttm.available == date(2026, 2, 15)  # recién se conoce con el 10-K


# ---------- P/E con splits ----------


def test_split_factor() -> None:
    splits = [(date(2024, 6, 10), 10.0), (date(2026, 1, 5), 2.0)]
    assert split_factor_after(splits, date(2024, 1, 1)) == 20.0
    assert split_factor_after(splits, date(2025, 1, 1)) == 2.0
    assert split_factor_after(splits, date(2026, 6, 1)) == 1.0


def test_pe_uses_split_adjusted_shares_and_no_lookahead() -> None:
    from app.services.forensic import SharesPoint, TtmPoint

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    # Precio ajustado por un split 2:1 del día 5: la serie entera queda en la base nueva (50).
    prices = PriceSeries(
        "X",
        "USD",
        None,
        days,
        [50.0] * 10,
        True,
        0,
        split_closes=[50.0] * 10,
        splits=[(date(2026, 1, 5), 2.0)],
    )
    shares = [SharesPoint(as_of=date(2025, 12, 1), available=date(2026, 1, 1), shares=100.0)]
    ttm = [
        TtmPoint(available=date(2026, 1, 3), period_end=date(2025, 12, 31), value=1000.0),
        TtmPoint(available=date(2026, 1, 8), period_end=date(2026, 3, 31), value=-5.0),
    ]
    stats = pe_stats(prices, shares, ttm)
    # Antes del 3/1 no se conocía la ganancia: no hay P/E. Del 8/1 en adelante es negativa.
    assert stats.observations == 5
    assert stats.excluded_days == 3
    # 50 × 100 acciones × 2 (split posterior al dato de acciones) / 1000
    assert stats.current == pytest.approx(10.0)


# ---------- API ----------


def yahoo_with_splits(closes: list[float], start: datetime) -> dict[str, Any]:
    ts = [int((start + timedelta(days=i)).timestamp()) for i in range(len(closes))]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "gmtoffset": -18000},
                    "timestamp": ts,
                    "indicators": {
                        "quote": [{"close": closes}],
                        "adjclose": [{"adjclose": closes}],
                    },
                    "events": {"splits": {}},
                }
            ],
            "error": None,
        }
    }


async def test_forensic_endpoint(auth_client: httpx.AsyncClient, router: Router) -> None:
    def annual(v2024: float, v2025: float) -> dict[str, Any]:
        return {
            "units": {
                "USD": [
                    fact("2024-01-01", "2024-12-31", v2024),
                    fact("2025-01-01", "2025-12-31", v2025),
                ]
            }
        }

    def instant(v2024: float, v2025: float) -> dict[str, Any]:
        return {
            "units": {"USD": [fact(None, "2024-12-31", v2024), fact(None, "2025-12-31", v2025)]}
        }

    gaap = {
        "Revenues": annual(1000, 1100),
        "CostOfRevenue": annual(600, 660),
        "SellingGeneralAndAdministrativeExpense": annual(150, 165),
        "DepreciationDepletionAndAmortization": annual(50, 55),
        "NetIncomeLoss": annual(80, 88),
        "OperatingIncomeLoss": annual(120, 132),
        "NetCashProvidedByUsedInOperatingActivities": annual(120, 132),
        "PaymentsToAcquirePropertyPlantAndEquipment": annual(40, 44),
        "ShareBasedCompensation": annual(30, 33),
        "AccountsReceivableNetCurrent": instant(100, 110),
        "AssetsCurrent": instant(400, 440),
        "LiabilitiesCurrent": instant(200, 220),
        "PropertyPlantAndEquipmentNet": instant(300, 330),
        "Assets": instant(1000, 1100),
        "Liabilities": instant(500, 550),
        "StockholdersEquity": instant(500, 550),
        "RetainedEarningsAccumulatedDeficit": instant(300, 330),
    }
    router.json(
        "sec.test/files/company_tickers.json",
        {"0": {"cik_str": 9, "ticker": "ACME", "title": "Acme Corp"}},
    )
    router.json(
        "companyfacts/CIK0000000009.json",
        {
            "facts": {
                "us-gaap": gaap,
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {"shares": [fact(None, "2025-01-20", 10.0, "2025-02-01")]}
                    }
                },
            }
        },
    )
    router.json(
        "submissions/CIK0000000009.json",
        {
            "sic": "7372",
            "sicDescription": "Services-Prepackaged Software",
            "filings": {"recent": {"form": []}},
        },
    )
    start = datetime(2024, 11, 1, 14, 30, tzinfo=UTC)
    router.json("/chart/ACME", yahoo_with_splits([100.0 + (i % 5) for i in range(500)], start))
    router.json("/chart/SPY", yahoo_with_splits([400.0 + (i % 7) for i in range(500)], start))

    r = await auth_client.get("/api/forensic/acme", params={"erp": 4.5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Acme Corp" and body["sic"] == 7372
    assert body["altman_variant"] == "Z''"
    latest = body["years"][0]
    assert latest["fiscal_year_end"] == "2025-12-31"
    assert latest["beneish"]["value"] is not None
    assert latest["altman"]["zone"] in ("segura", "gris", "riesgo de quiebra")
    assert latest["fcf"] == 132 - 44 and latest["fcf_minus_sbc"] == 132 - 44 - 33
    assert latest["sbc_to_revenue"] == pytest.approx(33 / 1100)
    assert body["years"][1]["beneish"]["missing"] == ["año anterior"]
    assert body["wacc"]["missing"] == ["tasa del Tesoro a 10 años"]  # FRED no responde en el test
    assert any("4.5 %" in a for a in body["wacc"]["assumptions"])
    assert body["pe"]["current"] is None  # sin trimestres no hay ganancia de 12 meses


def test_company_filings_dataclass() -> None:
    cf = CompanyFilings(filings=[], sic=None, sic_description=None)
    assert cf.sic is None
