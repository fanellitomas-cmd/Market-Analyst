"""SEC EDGAR: datos contables XBRL y presentaciones de empresas de EE.UU. (gratis, sin clave).

  - Tickers → CIK:  `https://www.sec.gov/files/company_tickers.json`
  - Presentaciones: `https://data.sec.gov/submissions/CIK##########.json`
  - Datos XBRL:     `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`

La SEC exige un User-Agent con nombre y email de contacto, y un máximo de 10 pedidos por
segundo (https://www.sec.gov/os/accessing-edgar-data). Sin User-Agent configurado, este cliente
no hace pedidos.
"""

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from app.sources.errors import NotFoundError, SourceError

SOURCE = "SEC EDGAR"


@dataclass(frozen=True)
class CompanyRef:
    cik: int
    ticker: str
    name: str


@dataclass(frozen=True)
class Fact:
    """Un valor reportado en XBRL para un período (`start` = None en datos instantáneos)."""

    start: date | None
    end: date
    value: float
    filed: date
    form: str


@dataclass(frozen=True)
class Filing:
    form: str
    filing_date: date
    accepted_at: datetime | None  # hora de aceptación de la SEC (UTC)
    items: tuple[str, ...]
    accession: str
    primary_document: str | None


@dataclass(frozen=True)
class CompanyFilings:
    filings: list[Filing]
    sic: int | None  # código de industria (SIC) declarado ante la SEC
    sic_description: str | None


def _date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


class SecClient:
    def __init__(
        self, client: httpx.AsyncClient, www_url: str, data_url: str, user_agent: str
    ) -> None:
        self._client = client
        self._www = www_url.rstrip("/")
        self._data = data_url.rstrip("/")
        self._user_agent = user_agent.strip()

    @property
    def configured(self) -> bool:
        return bool(self._user_agent)

    def filing_index_url(self, cik: int, accession: str) -> str:
        return f"{self._www}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"

    def company_url(self, cik: int) -> str:
        return f"{self._www}/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}"

    async def _get(self, url: str) -> Any:
        if not self.configured:
            raise SourceError(SOURCE, "falta configurar SEC_USER_AGENT (nombre y email)")
        try:
            response = await self._client.get(
                url, headers={"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}
            )
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code == 404:
            raise NotFoundError(SOURCE, "no hay datos para esta empresa")
        if response.status_code == 403:
            raise SourceError(SOURCE, "rechazó el pedido (revisá SEC_USER_AGENT)")
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(SOURCE, "respuesta no es JSON") from exc

    async def current_8k(self) -> list["CurrentFiling"]:
        """Las ~100 presentaciones 8-K más recientes de todas las empresas (feed en vivo)."""
        if not self.configured:
            raise SourceError(SOURCE, "falta configurar SEC_USER_AGENT (nombre y email)")
        try:
            response = await self._client.get(
                f"{self._www}{CURRENT_8K_PATH}", headers={"User-Agent": self._user_agent}
            )
        except httpx.HTTPError as exc:
            raise SourceError(SOURCE, f"no se pudo conectar ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise SourceError(SOURCE, f"respondió HTTP {response.status_code}")
        return parse_current_feed(response.text)

    async def ticker_map(self) -> dict[str, CompanyRef]:
        payload = await self._get(f"{self._www}/files/company_tickers.json")
        if not isinstance(payload, dict):
            raise SourceError(SOURCE, "formato inesperado del listado de tickers")
        out: dict[str, CompanyRef] = {}
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            cik, ticker, name = row.get("cik_str"), row.get("ticker"), row.get("title")
            if isinstance(cik, int) and isinstance(ticker, str) and isinstance(name, str):
                # El listado viene ordenado por relevancia: ante duplicados, gana el primero.
                out.setdefault(ticker.upper(), CompanyRef(cik, ticker.upper(), name))
        return out

    async def company_facts(self, cik: int) -> dict[str, Any]:
        payload = await self._get(f"{self._data}/api/xbrl/companyfacts/CIK{cik:010d}.json")
        if not isinstance(payload, dict):
            raise SourceError(SOURCE, "formato inesperado de companyfacts")
        return payload

    async def filings(self, cik: int) -> list[Filing]:
        """Presentaciones recientes (las ~1000 últimas que trae `submissions`)."""
        return (await self.submissions(cik)).filings

    async def submissions(self, cik: int) -> CompanyFilings:
        payload = await self._get(f"{self._data}/submissions/CIK{cik:010d}.json")
        recent = payload.get("filings", {}).get("recent") if isinstance(payload, dict) else None
        if not isinstance(recent, dict):
            raise SourceError(SOURCE, "formato inesperado de submissions")
        forms = recent.get("form") or []
        out: list[Filing] = []
        for i, form in enumerate(forms):
            filed = _date(_at(recent, "filingDate", i))
            if not isinstance(form, str) or filed is None:
                continue
            accepted: datetime | None = None
            raw_acc = _at(recent, "acceptanceDateTime", i)
            if isinstance(raw_acc, str):
                try:
                    accepted = datetime.fromisoformat(raw_acc.replace("Z", "+00:00"))
                except ValueError:
                    accepted = None
            items_raw = _at(recent, "items", i)
            items = (
                tuple(s.strip() for s in items_raw.split(",") if s.strip())
                if isinstance(items_raw, str)
                else ()
            )
            accession = _at(recent, "accessionNumber", i)
            doc = _at(recent, "primaryDocument", i)
            out.append(
                Filing(
                    form=form,
                    filing_date=filed,
                    accepted_at=accepted,
                    items=items,
                    accession=accession if isinstance(accession, str) else "",
                    primary_document=doc if isinstance(doc, str) and doc else None,
                )
            )
        raw_sic = payload.get("sic")
        try:
            sic = int(raw_sic) if raw_sic not in (None, "") else None
        except (TypeError, ValueError):
            sic = None
        sic_desc = payload.get("sicDescription")
        return CompanyFilings(
            filings=out, sic=sic, sic_description=sic_desc if isinstance(sic_desc, str) else None
        )


def _at(columns: dict[str, Any], key: str, i: int) -> Any:
    col = columns.get(key)
    return col[i] if isinstance(col, list) and i < len(col) else None


def extract_facts(
    company_facts: dict[str, Any], concept: str, unit: str, namespace: str = "us-gaap"
) -> list[Fact]:
    """Todos los valores de un concepto en una unidad ("USD", "USD/shares", "shares")."""
    node = company_facts.get("facts", {}).get(namespace, {}).get(concept, {})
    rows = node.get("units", {}).get(unit, []) if isinstance(node, dict) else []
    out: list[Fact] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        end, filed, val = _date(row.get("end")), _date(row.get("filed")), row.get("val")
        if (
            end is None
            or filed is None
            or isinstance(val, bool)
            or not isinstance(val, int | float)
        ):
            continue
        form = row.get("form")
        out.append(
            Fact(
                start=_date(row.get("start")),
                end=end,
                value=float(val),
                filed=filed,
                form=form if isinstance(form, str) else "",
            )
        )
    return out


# ---------- Feed de presentaciones recientes ----------

CURRENT_8K_PATH = (
    "/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include"
    "&start=0&count=100&output=atom"
)
_ATOM = "{http://www.w3.org/2005/Atom}"
_ITEM_RE = re.compile(r"Item\s+(\d\.\d\d)")
_CIK_RE = re.compile(r"\((\d{4,10})\)")
_ACC_RE = re.compile(r"AccNo:\s*(?:</b>)?\s*([\d-]{18,25})")


@dataclass(frozen=True)
class CurrentFiling:
    cik: int
    company: str
    form: str
    items: tuple[str, ...]
    accession: str
    updated: datetime | None
    link: str | None


def parse_current_feed(xml_text: str) -> list[CurrentFiling]:
    """Feed Atom de las últimas presentaciones de EDGAR (todas las empresas)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(SOURCE, "el feed de presentaciones no es XML válido") from exc
    out: list[CurrentFiling] = []
    for entry in root.iter(f"{_ATOM}entry"):
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        summary = html.unescape(entry.findtext(f"{_ATOM}summary") or "")
        cik_m = _CIK_RE.search(title)
        acc_m = _ACC_RE.search(summary)
        if not cik_m or not acc_m:
            continue
        form, sep, _ = title.partition(" - ")
        # Título: "8-K - ACME CORP (0000000009) (Filer)": el nombre va entre " - " y el CIK.
        company = title[len(form) + len(sep) : cik_m.start()].strip() if sep else ""
        updated_raw = entry.findtext(f"{_ATOM}updated")
        updated = None
        if updated_raw:
            try:
                updated = datetime.fromisoformat(updated_raw.strip())
            except ValueError:
                updated = None
        link_el = entry.find(f"{_ATOM}link")
        out.append(
            CurrentFiling(
                cik=int(cik_m.group(1)),
                company=company,
                form=form.strip(),
                items=tuple(dict.fromkeys(_ITEM_RE.findall(summary))),
                accession=acc_m.group(1),
                updated=updated,
                link=link_el.get("href") if link_el is not None else None,
            )
        )
    return out
