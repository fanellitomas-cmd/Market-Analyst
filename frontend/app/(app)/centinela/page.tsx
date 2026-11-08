"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { fmtClock, useAutoRefresh } from "@/lib/live";
import { ErrorNotice, Skeleton } from "@/components/Common";
import { LineChartCard } from "@/components/LineChartCard";
import { api, ApiError } from "@/lib/api";
import {
  fmtArDateTime,
  fmtDate,
  fmtFraction,
  fmtMonthYear,
  fmtNumber,
  fmtUsdCompact,
  monthTicks,
  NO_DATA,
  SERIES_COLORS,
} from "@/lib/format";
import type { ArbitrageResponse, CommoditiesResponse, CryptoResponse } from "@/lib/types";

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Error inesperado.";
}

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const w = 90;
  const h = 24;
  const pts = values
    .map((v, i) => `${(i / (values.length - 1)) * w},${max === min ? h / 2 : h - ((v - min) / (max - min)) * h}`)
    .join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={up ? "var(--good)" : "var(--bad)"} strokeWidth="1.5" />
    </svg>
  );
}

function signed(v: number | null, digits = 1) {
  return <span className={v === null ? undefined : v >= 0 ? "pos" : "neg"}>{fmtFraction(v, digits, true)}</span>;
}

function CryptoSection() {
  const [data, setData] = useState<CryptoResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(
    () =>
      api<CryptoResponse>("/api/sentinel/crypto")
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e) => setError(errMsg(e))),
    [],
  );
  const updated = useAutoRefresh(load, 30_000);

  const fundingRows = useMemo(() => {
    if (!data) return [];
    const byT = new Map<string, Record<string, string | number | null>>();
    data.assets.forEach((a) =>
      a.funding_history.forEach((p) => {
        const row = byT.get(p.t) ?? { t: p.t };
        row[a.coin] = p.v;
        byT.set(p.t, row);
      }),
    );
    return [...byT.values()].sort((a, b) => String(a.t).localeCompare(String(b.t)));
  }, [data]);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Derivados cripto</h2>
        <span className="updated-at">perpetuos en OKX · cada 30 s · {fmtClock(updated)}</span>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        La tasa de financiamiento la pagan los comprados a los vendidos (o al revés) cada 8 horas: muestra
        cuánto apalancamiento hay de cada lado. Las liquidaciones son posiciones cerradas a la fuerza por
        falta de garantía. Son datos de un exchange, no del mercado entero.
      </p>
      {error && <ErrorNotice message={error} />}
      {!data && !error && <Skeleton height={220} />}
      {data && (
        <>
          <div className="grid-3">
            {data.assets.map((a) => (
              <div className="tile" key={a.coin}>
                <div className="card-head">
                  <b>{a.coin}</b>
                  <span className="tabular">{a.price === null ? NO_DATA : `US$ ${fmtNumber(a.price, a.price > 1000 ? 0 : 2)}`}</span>
                </div>
                <dl className="kv">
                  <dt>Financiamiento (anualizado)</dt>
                  <dd>{signed(a.funding_annualized)}</dd>
                  <dt>Promedio 7 días</dt>
                  <dd>{signed(a.funding_avg_7d)}</dd>
                  <dt>Interés abierto</dt>
                  <dd>
                    {fmtUsdCompact(a.open_interest_usd)}{" "}
                    <span className="faint">({fmtFraction(a.open_interest_change_7d, 1, true)} 7 d)</span>
                  </dd>
                  <dt>Cuentas largas / cortas</dt>
                  <dd>{a.long_short_ratio === null ? NO_DATA : fmtNumber(a.long_short_ratio, 2)}</dd>
                  <dt
                    title={
                      a.liq_capped
                        ? "OKX devuelve sólo las últimas 100 liquidaciones: el total puede cubrir menos de 24 horas"
                        : undefined
                    }
                  >
                    Liquidados {a.liq_capped && a.liq_since ? `desde las ${new Date(a.liq_since).toLocaleTimeString("es-AR", { timeZone: "America/Argentina/Buenos_Aires", hour: "2-digit", minute: "2-digit" })}` : "24 h"} (largos / cortos)
                  </dt>
                  <dd>
                    {fmtUsdCompact(a.liq_long_usd)} / {fmtUsdCompact(a.liq_short_usd)}
                  </dd>
                </dl>
                {a.funding_reading && <div className="tile-sub">{a.funding_reading}</div>}
                {a.errors.length > 0 && (
                  <div className="tile-sub" title={a.errors.join("\n")}>
                    {a.errors.length} dato(s) no disponibles
                  </div>
                )}
              </div>
            ))}
          </div>
          {fundingRows.length > 0 && (
            <>
              <h3>Financiamiento anualizado, últimos 30 días</h3>
              <LineChartCard
                small
                data={fundingRows}
                xKey="t"
                series={data.assets.map((a, i) => ({ key: a.coin, label: a.coin, color: SERIES_COLORS[i] }))}
                yFormat={(v) => fmtFraction(v, 0)}
                xFormat={(t) => fmtDate(t).slice(0, 6)}
                tooltipTitle={(t) => fmtArDateTime(t)}
                reference={0}
                ariaLabel="Tasa de financiamiento anualizada de BTC, ETH y SOL"
              />
            </>
          )}
          <p className="faint" style={{ margin: 0 }}>
            Fuente: OKX · consultado {fmtArDateTime(data.computed_at)}. El mapa completo de liquidaciones de
            todo el mercado requiere servicios pagos.
          </p>
        </>
      )}
    </section>
  );
}

function CommoditiesSection() {
  const [data, setData] = useState<CommoditiesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(
    () =>
      api<CommoditiesResponse>("/api/sentinel/commodities")
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e) => setError(errMsg(e))),
    [],
  );
  const updated = useAutoRefresh(load, 120_000);
  const labels = data?.rows.find((r) => r.changes.length)?.changes.map((c) => c.label) ?? [];

  return (
    <section className="card">
      <div className="card-head">
        <h2>Materias primas y monedas</h2>
        <span className="updated-at">cada 2 min · {fmtClock(updated)}</span>
      </div>
      {error && <ErrorNotice message={error} />}
      {!data && !error && <Skeleton height={300} />}
      {data && (
        <>
          <div className="table-wrap">
            <table className="tabular">
              <thead>
                <tr>
                  <th scope="col">Activo</th>
                  <th scope="col">Último</th>
                  {labels.map((l) => (
                    <th scope="col" key={l}>
                      {l}
                    </th>
                  ))}
                  <th scope="col">60 ruedas</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.symbol}>
                    <td>
                      {r.name}
                      <span className="sub">
                        {r.group} · {r.symbol}
                      </span>
                    </td>
                    <td>
                      {r.last === null ? NO_DATA : fmtNumber(r.last, r.last < 10 ? 4 : 2)}
                      <span className="sub">{r.error ?? (r.date ? fmtDate(r.date) : "")}</span>
                    </td>
                    {labels.map((l) => (
                      <td key={l}>{signed(r.changes.find((c) => c.label === l)?.value ?? null)}</td>
                    ))}
                    <td>
                      <Spark values={r.spark} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.copper_gold.length > 0 && (
            <>
              <div className="card-head">
                <h3>Cobre / oro</h3>
                <span className="faint">3 meses: {fmtFraction(data.copper_gold_change_3m, 1, true)}</span>
              </div>
              <p className="muted" style={{ margin: 0 }}>
                El cobre acompaña la actividad industrial y el oro la búsqueda de refugio: el cociente sube
                cuando el mercado espera más crecimiento.
              </p>
              <LineChartCard
                small
                data={data.copper_gold}
                xKey="t"
                series={[{ key: "v", label: "Cobre / oro (× 1000)", color: "var(--series-1)" }]}
                yFormat={(v) => fmtNumber(v, 2)}
                xFormat={fmtMonthYear}
                xTicks={monthTicks(data.copper_gold.map((p) => p.t))}
                tooltipTitle={fmtDate}
                ariaLabel="Cociente cobre / oro"
              />
            </>
          )}
          <p className="faint" style={{ margin: 0 }}>
            Fuente: Yahoo Finance · {data.note}
          </p>
        </>
      )}
    </section>
  );
}

function ArbitrageSection() {
  const [data, setData] = useState<ArbitrageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const textLoaded = useRef(false);
  const load = useCallback(
    () =>
      api<ArbitrageResponse>("/api/sentinel/arbitrage")
        .then((d) => {
          setData(d);
          setError(null);
          // El cuadro de CEDEARs se completa una sola vez: no pisar lo que el usuario edita.
          if (!textLoaded.current) {
            setText(d.cedear_ratios.map((c) => `${c.local} ${c.foreign} ${c.ratio}`).join("\n"));
            textLoaded.current = true;
          }
        })
        .catch((e) => setError(errMsg(e))),
    [],
  );
  const updated = useAutoRefresh(load, 60_000);

  async function saveCedears() {
    setMessage(null);
    const items = text
      .split("\n")
      .map((l) => l.trim().split(/[\s,;]+/))
      .filter((p) => p.length === 3)
      .map(([local, foreign, ratio]) => ({ local, foreign, ratio: Number(ratio.replace(",", ".")) }));
    try {
      await api("/api/sentinel/cedears", { method: "PUT", body: JSON.stringify(items) });
      setMessage(`Guardados ${items.length} CEDEARs.`);
      textLoaded.current = false;
      void load();
    } catch (e) {
      setMessage(errMsg(e));
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Arbitraje ADR / CEDEAR / CCL</h2>
        <span className="updated-at">cada 60 s · {fmtClock(updated)}</span>
        {data && (
          <span className="faint">
            CCL de mercado: <b>{data.ccl_market === null ? NO_DATA : `$ ${fmtNumber(data.ccl_market, 2)}`}</b> ·
            mediana implícita: <b>{data.ccl_median_implied === null ? NO_DATA : `$ ${fmtNumber(data.ccl_median_implied, 2)}`}</b>
          </span>
        )}
      </div>
      <p className="muted" style={{ margin: 0 }}>
        CCL implícito = precio local en pesos × acciones (o CEDEARs) por unidad / precio en dólares. Si un
        papel se aleja de la mediana, uno de los dos mercados está caro o barato frente al otro. Sólo se
        comparan precios tomados con menos de 2 horas de diferencia; Yahoo publica BYMA con demora.
      </p>
      {error && <ErrorNotice message={error} />}
      {!data && !error && <Skeleton height={300} />}
      {data && (
        <>
          <div className="table-wrap">
            <table className="tabular">
              <thead>
                <tr>
                  <th scope="col">Papel</th>
                  <th scope="col">Ratio</th>
                  <th scope="col">Exterior (US$)</th>
                  <th scope="col">Local ($)</th>
                  <th scope="col">CCL implícito</th>
                  <th scope="col">vs. CCL</th>
                  <th scope="col">vs. mediana</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={`${r.kind}-${r.foreign}`}>
                    <td>
                      {r.foreign} <span className="faint">/ {r.local}</span>
                      <span className="sub">
                        {r.kind}
                        {r.name ? ` · ${r.name}` : ""}
                      </span>
                    </td>
                    <td>{fmtNumber(r.ratio, r.ratio % 1 ? 2 : 0)}</td>
                    <td>
                      {r.foreign_price === null ? NO_DATA : fmtNumber(r.foreign_price, 2)}
                      <span className="sub">{r.foreign_time ? fmtArDateTime(r.foreign_time) : ""}</span>
                    </td>
                    <td>
                      {r.local_price === null ? NO_DATA : fmtNumber(r.local_price, 2)}
                      <span className="sub">{r.local_time ? fmtArDateTime(r.local_time) : ""}</span>
                    </td>
                    <td>
                      {r.implied_fx === null ? NO_DATA : `$ ${fmtNumber(r.implied_fx, 2)}`}
                      {r.note && <span className="sub">{r.note}</span>}
                    </td>
                    <td className={r.comparable ? undefined : "muted"}>{signed(r.vs_ccl)}</td>
                    <td className={r.comparable ? undefined : "muted"}>{signed(r.vs_median)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>CEDEARs a seguir</h3>
          <p className="muted" style={{ margin: 0 }}>
            Un CEDEAR por línea: símbolo local, símbolo en EE.UU. y cuántos CEDEARs equivalen a una acción
            (por ejemplo <code>AAPL.BA AAPL 20</code>). Los ratios cambian cada tanto: tomalos del listado
            vigente de la Caja de Valores o de tu broker.
          </p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value.toUpperCase())}
            rows={4}
            style={{ width: "100%", fontFamily: "var(--mono)", fontSize: "0.85rem" }}
            aria-label="CEDEARs: símbolo local, símbolo en EE.UU. y ratio"
          />
          <div className="controls">
            <button onClick={saveCedears}>Guardar CEDEARs</button>
          </div>
          {message && <div className="notice warn">{message}</div>}
        </>
      )}
    </section>
  );
}

export default function CentinelaPage() {
  return (
    <>
      <div className="page-head">
        <h1>Centinela 24/7</h1>
        <p className="muted" style={{ margin: 0 }}>
          Los activos que se mueven cuando la bolsa está cerrada o que anticipan presiones: apalancamiento
          en cripto, materias primas, monedas y las brechas entre el mercado local y el exterior.
        </p>
      </div>
      <CryptoSection />
      <CommoditiesSection />
      <ArbitrageSection />
    </>
  );
}
