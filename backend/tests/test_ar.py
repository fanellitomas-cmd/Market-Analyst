from datetime import date

import httpx
import pytest

from app.services.ar_macro import BCRA_BY_KEY, match_variable
from app.sources.bcra import BcraClient, BcraVariable
from tests.conftest import Router

PV = "Principales Variables"
NA = "En porcentaje nominal anual"
EA = "En porcentaje efectivo anual"


def var(vid: int, desc: str, unit: str | None = None, cat: str | None = PV) -> BcraVariable:
    return BcraVariable(vid, desc, cat, unit, date(2026, 11, 4), 1.0)


def row(
    vid: int, desc: str, unit: str, value: float, day: str = "2026-11-04", cat: str = PV
) -> dict[str, object]:
    return {
        "idVariable": vid,
        "descripcion": desc,
        "categoria": cat,
        "unidadExpresion": unit,
        "ultFechaInformada": day,
        "ultValorInformado": value,
    }


# Filas con el formato y las descripciones reales del catálogo v4.0 del BCRA.
CATALOG_V4 = {
    "status": 200,
    "metadata": {"resultset": {"count": 9, "offset": 0, "limit": 3000}},
    "results": [
        row(1, "Reservas internacionales", "En millones de USD", 41234.0),
        row(
            5,
            "Tipo de cambio mayorista de referencia",
            "Pesos argentinos por dólar estadounidense",
            1425.5,
            "2026-11-05",
        ),
        row(7, "Tasa de interés BADLAR de bancos privados", NA, 31.4),
        row(35, "Tasa de interés BADLAR de bancos privados", EA, 36.2),
        row(27, "Inflación mensual.", "En porcentaje", 1.9, "2026-09-30"),
        row(
            31, "Unidad de valor adquisitivo (base 31.3.16=14.05)", "En ARS", 1650.12, "2026-11-06"
        ),
        row(
            87,
            "Depósitos a plazo no ajustables por CER/UVAS de los sectores público y privado",
            "En millones de ARS",
            9.0,
            cat="Depósitos por tipo de titular",
        ),
        row(
            160, "Tasas de interés de política monetaria", NA, 29.0, "2025-07-10", cat="Series.xlsm"
        ),
    ],
}

DOLARES = [
    {
        "moneda": "USD",
        "casa": "oficial",
        "nombre": "Oficial",
        "compra": 1400,
        "venta": 1450,
        "fechaActualizacion": "2026-11-06T14:00:00.000Z",
    },
    {
        "moneda": "USD",
        "casa": "blue",
        "nombre": "Blue",
        "compra": 1480,
        "venta": 1500,
        "fechaActualizacion": "2026-11-06T14:05:00.000Z",
    },
    {
        "moneda": "USD",
        "casa": "bolsa",
        "nombre": "Bolsa",
        "compra": 1460,
        "venta": 1479.5,
        "fechaActualizacion": "2026-11-06T14:05:00.000Z",
    },
    {
        "moneda": "USD",
        "casa": "tarjeta",
        "nombre": "Tarjeta",
        "compra": 0,
        "venta": 0,
        "fechaActualizacion": "2026-11-06T14:05:00.000Z",
    },
]


# ---------- Matching de variables ----------


def test_match_uses_unit_to_tell_nominal_from_effective() -> None:
    # Misma descripción: sólo la unidad distingue la tasa nominal de la efectiva.
    variables = [
        var(35, "Tasa de interés BADLAR de bancos privados", EA),
        var(7, "Tasa de interés BADLAR de bancos privados", NA),
    ]
    found = match_variable(BCRA_BY_KEY["badlar"], variables)
    assert found is not None and found.id == 7


def test_match_prefers_principal_variables() -> None:
    variables = [
        var(
            87,
            "Depósitos ajustables por coeficiente de estabilizacion de referencia",
            cat="Depósitos por tipo de titular",
        ),
        var(30, "Coeficiente de estabilización de referencia (base 2.2.02=1)"),
    ]
    found = match_variable(BCRA_BY_KEY["cer"], variables)
    assert found is not None and found.id == 30


def test_match_ignores_accents_and_case() -> None:
    found = match_variable(BCRA_BY_KEY["inflacion_mensual"], [var(27, "INFLACIÓN MENSUAL.")])
    assert found is not None and found.id == 27


def test_match_cer_by_full_name() -> None:
    assert match_variable(BCRA_BY_KEY["cer"], [var(9, "Tasa de concertación")]) is None
    found = match_variable(
        BCRA_BY_KEY["cer"], [var(30, "Coeficiente de estabilización de referencia (base 2.2.02=1)")]
    )
    assert found is not None


def test_no_match_returns_none() -> None:
    assert match_variable(BCRA_BY_KEY["tamar"], [var(1, "Reservas Internacionales")]) is None


# ---------- Cliente BCRA ----------


async def test_bcra_series_accepts_v4_detalle_and_paginates() -> None:
    router = Router()
    page1 = {
        "status": 200,
        "metadata": {"resultset": {"count": 3001, "offset": 0, "limit": 3000}},
        "results": [
            {
                "idVariable": 1,
                "detalle": [
                    {"fecha": "2026-11-04", "valor": 41000.0},
                    {"fecha": "2026-11-03", "valor": 40900.0},
                ],
            }
        ],
    }
    page2 = {
        "status": 200,
        "metadata": {"resultset": {"count": 3001, "offset": 3000, "limit": 3000}},
        "results": [{"idVariable": 1, "detalle": [{"fecha": "2026-11-02", "valor": 40800.0}]}],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page2 if req.url.params["offset"] == "3000" else page1)

    router.add("/monetarias/1", handler)
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        points = await BcraClient(http, "https://bcra.test").get_series(
            1, date(2026, 1, 1), date(2026, 11, 6)
        )
    assert points == [
        (date(2026, 11, 2), 40800.0),
        (date(2026, 11, 3), 40900.0),
        (date(2026, 11, 4), 41000.0),
    ]
    assert len(router.calls) == 2


async def test_bcra_series_accepts_v3_rows() -> None:
    router = Router()
    router.json(
        "/monetarias/5",
        {
            "status": 200,
            "results": [
                {"idVariable": 5, "fecha": "2024-06-12", "valor": 899.5},
                {"idVariable": 5, "fecha": "2024-06-13", "valor": None},
            ],
        },
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(router)) as http:
        points = await BcraClient(http, "https://bcra.test").get_series(
            5, date(2024, 1, 1), date(2024, 12, 31)
        )
    assert points == [(date(2024, 6, 12), 899.5)]  # el null se omite, no es 0


# ---------- API ----------


@pytest.fixture
def ar_sources(router: Router) -> None:
    router.json("bcra.test/estadisticas/v4.0/monetarias?", CATALOG_V4)
    router.json("dolarapi.test/v1/dolares", DOLARES)
    router.json(
        "argdatos.test/v1/finanzas/indices/riesgo-pais",
        [{"valor": 650, "fecha": "2026-11-04"}, {"valor": 640, "fecha": "2026-11-05"}],
    )


@pytest.mark.usefixtures("ar_sources")
async def test_dashboard(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/ar/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()

    rows = {row["house"]: row for row in body["dollars"]["rows"]}
    assert list(rows) == ["oficial", "bolsa", "blue", "tarjeta"]  # orden fijo, sin los ausentes
    assert rows["oficial"]["gap_vs_official"] is None
    assert rows["blue"]["gap_vs_official"] == pytest.approx(1500 / 1450 - 1)
    assert rows["bolsa"]["name"] == "MEP"
    assert rows["tarjeta"]["sell"] is None  # 0 en la fuente = sin dato
    assert rows["tarjeta"]["gap_vs_official"] is None

    assert body["country_risk"]["value"] == 640
    assert body["country_risk_change"] == -10

    bcra = {i["indicator"]["key"]: i for i in body["bcra"]}
    assert bcra["reservas"]["value"] == 41234.0
    assert bcra["reservas"]["source_description"].endswith("(id 1)")
    assert bcra["reservas"]["source_unit"] == "En millones de USD"
    assert bcra["badlar"]["value"] == 31.4  # la nominal, no la efectiva
    assert bcra["uva"]["status"] == "OK"
    assert bcra["tamar"]["status"] == "NO_DISPONIBLE"
    assert bcra["tamar"]["value"] is None
    assert bcra["cer"]["status"] == "NO_DISPONIBLE"  # no confunde CER con "depósitos ... CER"
    assert bcra["tasa_politica"]["note"].startswith("dato desactualizado")
    assert bcra["reservas"]["note"] is None


async def test_dashboard_survives_all_sources_down(
    auth_client: httpx.AsyncClient, router: Router
) -> None:
    router.add("bcra.test", lambda _r: httpx.Response(503))
    router.add("dolarapi.test", lambda _r: httpx.Response(500))
    router.add("argdatos.test", lambda _r: httpx.Response(500))
    r = await auth_client.get("/api/ar/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dollars"]["status"] == "ERROR_FUENTE"
    assert body["country_risk"]["status"] == "ERROR_FUENTE"
    assert {i["status"] for i in body["bcra"]} == {"ERROR_FUENTE"}


@pytest.mark.usefixtures("ar_sources")
async def test_bcra_series_resolves_id_by_description(
    auth_client: httpx.AsyncClient, router: Router
) -> None:
    router.json(
        "/monetarias/7?",
        {
            "status": 200,
            "results": [
                {
                    "idVariable": 7,
                    "detalle": [
                        {"fecha": "2025-01-02", "valor": 33.0},
                        {"fecha": "2026-11-04", "valor": 31.4},
                    ],
                }
            ],
        },
    )
    r = await auth_client.get(
        "/api/ar/series", params={"indicator": "badlar", "start": "2026-01-01"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["observations"] == [{"date": "2026-11-04", "value": 31.4}]
    assert "BADLAR" in body["source_description"]
    assert body["source"]["url"].endswith("/monetarias/7")


@pytest.mark.usefixtures("ar_sources")
async def test_dollar_series(auth_client: httpx.AsyncClient, router: Router) -> None:
    router.json(
        "/v1/cotizaciones/dolares/blue",
        [
            {"casa": "blue", "compra": 1200, "venta": 1220, "fecha": "2026-01-02"},
            {"casa": "blue", "compra": 1480, "venta": 1500, "fecha": "2026-11-05"},
        ],
    )
    r = await auth_client.get("/api/ar/series", params={"indicator": "dolar_blue"})
    assert r.status_code == 200, r.text
    assert [o["value"] for o in r.json()["observations"]] == [1220, 1500]


@pytest.mark.usefixtures("ar_sources")
async def test_series_unknown_bcra_variable(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/ar/series", params={"indicator": "tamar"})
    assert r.status_code == 404
    r = await auth_client.get("/api/ar/series", params={"indicator": "inventado"})
    assert r.status_code == 422
