import httpx
import pytest

from app.sources.errors import NotFoundError, SourceError
from app.sources.worldbank import WorldBankClient
from app.sources.yahoo import YahooClient, normalize_symbol
from tests.conftest import Router, yahoo_payload

WB = "https://wb.test/v2"


def wb_row(code: str, name: str, year: str, value: float | None) -> dict[str, object]:
    return {
        "indicator": {"id": "NY.GDP.MKTP.KD.ZG", "value": "GDP growth"},
        "country": {"id": code[:2], "value": name},
        "countryiso3code": code,
        "date": year,
        "value": value,
    }


async def test_worldbank_skips_nulls_and_sorts() -> None:
    router = Router()
    router.json(
        "/indicator/",
        [
            {"page": 1, "pages": 1, "total": 3},
            [
                wb_row("ARG", "Argentina", "2023", -1.6),
                wb_row("ARG", "Argentina", "2024", None),
                wb_row("ARG", "Argentina", "2022", 5.3),
                wb_row("USA", "United States", "2023", 2.9),
            ],
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        result = await WorldBankClient(http, "https://wb.test/v2").get_indicator(
            "NY.GDP.MKTP.KD.ZG", ["ARG", "USA"], 2020, 2024
        )
    arg = result["ARG"]
    assert [o.period for o in arg.observations] == ["2022", "2023"]
    assert arg.observations[1].value == -1.6
    assert arg.missing_periods == 1
    assert result["USA"].observations[0].value == 2.9
    assert "country/ARG;USA/indicator" in str(router.calls[0].url)


async def test_worldbank_error_payload() -> None:
    router = Router()
    router.json("/indicator/", [{"message": [{"id": "120", "value": "Invalid value"}]}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        with pytest.raises(NotFoundError, match="Invalid value"):
            await WorldBankClient(http, WB).get_indicator("X", ["ARG"], 2020, 2024)


async def test_worldbank_http_error() -> None:
    router = Router()
    router.json("/indicator/", {}, status=500)
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        with pytest.raises(SourceError, match="HTTP 500"):
            await WorldBankClient(http, WB).get_indicator("X", ["ARG"], 2020, 2024)


async def test_yahoo_parses_adjusted_closes_and_counts_gaps() -> None:
    router = Router()
    router.json("/v8/finance/chart/AAPL", yahoo_payload([100.0, None, 102.0, 103.5]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        series = await YahooClient(http, "https://yahoo.test").get_daily_prices("AAPL", "1y")
    assert series.closes == [100.0, 102.0, 103.5]
    assert series.missing_points == 1
    assert series.adjusted is True
    assert series.currency == "USD"
    # 14:30 UTC con offset de NY (-5 h) sigue siendo el mismo día de rueda
    assert series.dates[0].isoformat() == "2024-01-02"
    assert router.calls[0].headers["user-agent"].startswith("Mozilla/5.0")


async def test_yahoo_falls_back_to_unadjusted_close() -> None:
    router = Router()
    router.json("/v8/finance/chart/X", yahoo_payload([1.0, 2.0], adj=False))
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        series = await YahooClient(http, "https://yahoo.test").get_daily_prices("X", "1y")
    assert series.adjusted is False
    assert series.closes == [1.0, 2.0]


async def test_yahoo_not_found() -> None:
    router = Router()
    router.json(
        "/v8/finance/chart/NOPE",
        {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data"}}},
        status=404,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        with pytest.raises(NotFoundError):
            await YahooClient(http, "https://yahoo.test").get_daily_prices("NOPE", "1y")


def test_normalize_symbol() -> None:
    assert normalize_symbol(" brk-b ") == "BRK-B"
    assert normalize_symbol("^gspc") == "^GSPC"
    assert normalize_symbol("ggal.ba") == "GGAL.BA"
    with pytest.raises(ValueError):
        normalize_symbol("AAPL/../x")
    with pytest.raises(ValueError):
        normalize_symbol("")
