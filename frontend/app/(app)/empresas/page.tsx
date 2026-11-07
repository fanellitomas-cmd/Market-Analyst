"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { ForensicView } from "@/components/ForensicView";
import { LineChartCard } from "@/components/LineChartCard";
import { api, ApiError } from "@/lib/api";
import {
  fmtDate,
  fmtFraction,
  fmtNumber,
  fmtUsdAxis,
  fmtUsdCompact,
  metricText,
  NO_DATA,
} from "@/lib/format";
import type { EarningsResponse, Metric, QuarterRow, Reaction } from "@/lib/types";

const PRESETS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "MELI", "JPM", "KO"];

function quarterLabel(end: string): string {
  const [y, m] = end.split("-").map(Number);
  return `T${Math.ceil(m / 3)} ${String(y).slice(2)}`;
}

function signCls(m: Metric | number | null | undefined): string | undefined {
  const v = typeof m === "number" ? m : m && m.status === "OK" ? m.value : null;
  if (v === null || v === undefined) return undefined;
  return v >= 0 ? "pos" : "neg";
}

function MetricCell({ m, kind, signed }: { m: Metric; kind: "usd" | "fraction" | "eps"; signed?: boolean }) {
  const text =
    m.status !== "OK" || m.value === null
      ? NO_DATA
      : kind === "usd"
        ? fmtUsdCompact(m.value)
        : kind === "eps"
          ? `US$ ${fmtNumber(m.value, 2)}`
          : fmtFraction(m.value, 1, signed);
  return (
    <td className={signed ? signCls(m) : undefined} title={m.note ?? undefined}>
      {text}
      {m.note?.startsWith("calculado") ? "*" : ""}
    </td>
  );
}

function ReactionCell({ r }: { r: Reaction | undefined }) {
  if (!r || r.status !== "OK" || r.stock === null) {
    return (
      <td className="muted" title={r?.note ?? undefined}>
        {NO_DATA}
      </td>
    );
  }
  return (
    <td>
      <span className={signCls(r.stock)}>{fmtFraction(r.stock, 1, true)}</span>
      <span className="sub">
        SPY {fmtFraction(r.benchmark, 1, true)} · exceso{" "}
        <span className={signCls(r.excess)}>{fmtFraction(r.excess, 1, true)}</span>
      </span>
    </td>
  );
}

export default function EmpresasPage() {
  const [ticker, setTicker] = useState("AAPL");
  const [data, setData] = useState<EarningsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"resultados" | "forense">("resultados");

  const load = useCallback(async (t: string) => {
    if (!t.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api<EarningsResponse>(`/api/earnings/${encodeURIComponent(t.trim())}`));
    } catch (e) {
      setData(null);
      setError(e instanceof ApiError ? e.message : "Error inesperado.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Permite llegar desde el screener: /empresas?ticker=MSFT&vista=forense
    const params = new URLSearchParams(window.location.search);
    const initial = (params.get("ticker") ?? "AAPL").toUpperCase();
    if (params.get("vista") === "forense") setView("forense");
    setTicker(initial);
    void load(initial);
  }, [load]);

  const chronological: QuarterRow[] = useMemo(
    () => (data ? [...data.quarters].reverse() : []),
    [data],
  );
  const revenueBars = chronological
    .filter((q) => q.revenue.value !== null)
    .map((q) => ({ label: quarterLabel(q.period_end), end: q.period_end, value: q.revenue.value, derived: q.derived }));
  const margins = chronological.map((q) => ({
    label: quarterLabel(q.period_end),
    gross: q.gross_margin.value,
    operating: q.operating_margin.value,
    net: q.net_margin.value,
  }));
  const marginSeries = [
    { key: "gross", label: "Bruto", color: "var(--series-1)" },
    { key: "operating", label: "Operativo", color: "var(--series-2)" },
    { key: "net", label: "Neto", color: "var(--series-3)" },
  ].filter((s) => margins.some((m) => m[s.key as "gross" | "operating" | "net"] !== null));
  const anyDerived = data?.quarters.some((q) => q.derived);

  return (
    <>
      <div className="page-head">
        <h1>Resultados de empresas</h1>
        <p className="muted" style={{ margin: 0 }}>
          Balances trimestrales tal como se presentan ante la SEC, y cómo reaccionó la acción cada vez
          que la empresa publicó resultados. Sólo empresas que presentan balances en EE.UU.
        </p>
      </div>

      <section className="card">
        <form
          className="controls"
          onSubmit={(e) => {
            e.preventDefault();
            void load(ticker);
          }}
        >
          <label className="field">
            Ticker
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              maxLength={10}
              style={{ width: 140 }}
            />
          </label>
          <button className="primary" type="submit" disabled={loading}>
            {loading ? "Buscando…" : "Ver resultados"}
          </button>
        </form>
        <div className="chips">
          {PRESETS.map((p) => (
            <button
              key={p}
              className="chip"
              aria-pressed={data?.company.ticker === p}
              onClick={() => {
                setTicker(p);
                void load(p);
              }}
            >
              {p}
            </button>
          ))}
        </div>
        <div className="segmented" role="group" aria-label="Vista">
          <button type="button" aria-pressed={view === "resultados"} onClick={() => setView("resultados")}>
            Resultados y reacción
          </button>
          <button type="button" aria-pressed={view === "forense"} onClick={() => setView("forense")}>
            Forense y valuación
          </button>
        </div>
      </section>

      {error && <ErrorNotice message={error} />}
      {loading && !data && <Skeleton height={360} />}

      {view === "forense" && data && <ForensicView ticker={data.company.ticker} />}

      {view === "resultados" && data && (
        <>
          <section className="card" aria-busy={loading}>
            <div className="card-head">
              <h2>
                {data.company.ticker} <span className="muted">· {data.company.name}</span>
              </h2>
              <span className="faint">CIK {data.company.cik}</span>
            </div>
            {data.quarters.length === 0 ? (
              <p className="muted">La SEC no tiene datos trimestrales de ingresos para esta empresa.</p>
            ) : (
              <div className="grid-2">
                <div>
                  <h3>Ingresos por trimestre (US$)</h3>
                  <div className="chart" role="img" aria-label="Ingresos trimestrales">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={revenueBars} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid stroke="var(--grid)" vertical={false} />
                        <XAxis
                          dataKey="label"
                          tick={{ fill: "var(--text-3)", fontSize: 12 }}
                          tickLine={false}
                          axisLine={{ stroke: "var(--border)" }}
                        />
                        <YAxis
                          tickFormatter={fmtUsdAxis}
                          tick={{ fill: "var(--text-3)", fontSize: 12 }}
                          tickLine={false}
                          axisLine={false}
                          width={72}
                        />
                        <Tooltip
                          cursor={{ fill: "var(--surface-2)" }}
                          content={({ active, payload }) => {
                            const p = payload?.[0]?.payload as (typeof revenueBars)[number] | undefined;
                            if (!active || !p) return null;
                            return (
                              <div className="tooltip">
                                <div className="tooltip-title">Trimestre al {fmtDate(p.end)}</div>
                                <div className="tooltip-row">
                                  Ingresos <b>{fmtUsdCompact(p.value)}</b>
                                </div>
                                {p.derived && <div className="faint">calculado: anual − 3 trimestres</div>}
                              </div>
                            );
                          }}
                        />
                        <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false}>
                          {revenueBars.map((b) => (
                            <Cell key={b.end} fill="var(--series-1)" fillOpacity={b.derived ? 0.5 : 1} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
                <div>
                  <h3>Márgenes</h3>
                  {marginSeries.length > 0 ? (
                    <LineChartCard
                      data={margins}
                      xKey="label"
                      series={marginSeries}
                      yFormat={(v) => fmtFraction(v, 0)}
                      reference={0}
                      ariaLabel="Márgenes bruto, operativo y neto por trimestre"
                    />
                  ) : (
                    <p className="muted">La empresa no reporta los conceptos para calcular márgenes.</p>
                  )}
                </div>
              </div>
            )}

            {data.quarters.length > 0 && (
              <div className="table-wrap">
                <table className="tabular">
                  <thead>
                    <tr>
                      <th scope="col">Trimestre</th>
                      <th scope="col">Ingresos</th>
                      <th scope="col">Var. anual</th>
                      <th scope="col">Margen bruto</th>
                      <th scope="col">Margen operativo</th>
                      <th scope="col">Margen neto</th>
                      <th scope="col">Ganancia neta</th>
                      <th scope="col">Ganancia por acción</th>
                      <th scope="col">Var. anual</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.quarters.map((q) => (
                      <tr key={q.period_end}>
                        <td>
                          {quarterLabel(q.period_end)}
                          <span className="sub">al {fmtDate(q.period_end)}</span>
                        </td>
                        <MetricCell m={q.revenue} kind="usd" />
                        <MetricCell m={q.revenue_yoy} kind="fraction" signed />
                        <MetricCell m={q.gross_margin} kind="fraction" />
                        <MetricCell m={q.operating_margin} kind="fraction" />
                        <MetricCell m={q.net_margin} kind="fraction" />
                        <MetricCell m={q.net_income} kind="usd" />
                        <MetricCell m={q.eps_diluted} kind="eps" />
                        <MetricCell m={q.eps_yoy} kind="fraction" signed />
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="faint" style={{ margin: 0 }}>
              Trimestres calendario según la fecha de cierre del período.
              {anyDerived
                ? " * Calculado: la empresa sólo reporta el año completo; el 4.º trimestre es el anual menos los otros tres (la ganancia por acción no se puede calcular así)."
                : ""}{" "}
              Pasá el mouse por un “—” para ver el motivo.
            </p>
            <SourceLine source={data.sources[0]} extra="datos XBRL de 10-Q y 10-K" />
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Reacción del precio a cada publicación</h2>
              <span className="faint">
                movimiento promedio a 1 rueda:{" "}
                <b>{metricText(data.avg_abs_move_1d, "fraction")}</b> (en cualquier dirección)
              </span>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Desde el último cierre anterior a la publicación. Si salió después del cierre, la base es
              el cierre de ese día. “Exceso” es la diferencia contra el S&amp;P 500 (SPY) en el mismo
              lapso.
            </p>
            {data.events.length === 0 ? (
              <p className="muted">No hay presentaciones 8-K de resultados (ítem 2.02) recientes.</p>
            ) : (
              <div className="table-wrap">
                <table className="tabular">
                  <thead>
                    <tr>
                      <th scope="col">Publicación</th>
                      <th scope="col">1 rueda</th>
                      <th scope="col">5 ruedas</th>
                      <th scope="col">20 ruedas</th>
                      <th scope="col">8-K</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.events.map((ev) => (
                      <tr key={ev.filing_date}>
                        <td>
                          {fmtDate(ev.filing_date)}
                          <span className="sub">
                            {ev.session}
                            {ev.accepted_at_ny ? ` (${ev.accepted_at_ny.slice(11, 16)} NY)` : ""}
                          </span>
                        </td>
                        {[1, 5, 20].map((h) => (
                          <ReactionCell key={h} r={ev.reactions.find((r) => r.horizon === h)} />
                        ))}
                        <td>
                          <a href={ev.filing_url} target="_blank" rel="noreferrer">
                            ver
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <SourceLine source={data.sources[1]} extra="precios ajustados; fechas y horas de la SEC" />
          </section>
        </>
      )}
    </>
  );
}
