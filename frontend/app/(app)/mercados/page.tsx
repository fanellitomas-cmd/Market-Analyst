"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { LineChartCard } from "@/components/LineChartCard";
import { api, ApiError, qs } from "@/lib/api";
import { fmtDate, fmtFraction, fmtMonthYear, fmtNumber, monthTicks, metricText, NO_DATA } from "@/lib/format";
import type { CompareResponse, Metric, PriceRange, RelativeAnalysis } from "@/lib/types";

const RANGES: { value: PriceRange; label: string }[] = [
  { value: "6mo", label: "6M" },
  { value: "1y", label: "1A" },
  { value: "2y", label: "2A" },
  { value: "5y", label: "5A" },
];

const BENCHMARKS = [
  { value: "SPY", label: "S&P 500 (SPY)" },
  { value: "QQQ", label: "Nasdaq 100 (QQQ)" },
  { value: "IWM", label: "Russell 2000 (IWM)" },
  { value: "EEM", label: "Emergentes (EEM)" },
  { value: "ACWI", label: "Mundo (ACWI)" },
];

const PRESETS: { label: string; symbols: string }[] = [
  { label: "Crecimiento", symbols: "NVDA, MSFT, AMZN, META, TSLA, GOOGL, AVGO, CRM" },
  { label: "Valor", symbols: "KO, PG, JNJ, XOM, JPM, WMT, PEP, CVX" },
  { label: "ADRs argentinos", symbols: "GGAL, YPF, PAM, BMA, VIST, TEO, LOMA, CEPU" },
];

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Error inesperado.";
}

function signClass(m: Metric | undefined) {
  if (!m || m.status !== "OK" || m.value === null) return undefined;
  return m.value >= 0 ? "pos" : "neg";
}

function Tile({ label, value, sub, cls }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className={`tile-value ${cls ?? ""}`}>{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  );
}

export default function MercadosPage() {
  const [symbol, setSymbol] = useState("NVDA");
  const [benchmark, setBenchmark] = useState("SPY");
  const [range, setRange] = useState<PriceRange>("1y");
  const [analysis, setAnalysis] = useState<RelativeAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [compareInput, setCompareInput] = useState(PRESETS[0].symbols);
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);

  const run = useCallback(
    async (sym: string) => {
      if (!sym.trim()) return;
      setLoading(true);
      setError(null);
      try {
        setAnalysis(
          await api<RelativeAnalysis>(
            `/api/markets/relative?${qs({ symbol: sym.trim(), benchmark, range })}`,
          ),
        );
      } catch (e) {
        setError(errMsg(e));
        setAnalysis(null);
      } finally {
        setLoading(false);
      }
    },
    [benchmark, range],
  );

  const runCompare = useCallback(async () => {
    const symbols = compareInput
      .split(/[\s,;]+/)
      .filter(Boolean)
      .join(",");
    if (!symbols) return;
    setCompareLoading(true);
    setCompareError(null);
    try {
      setCompare(
        await api<CompareResponse>(`/api/markets/compare?${qs({ symbols, benchmark, range })}`),
      );
    } catch (e) {
      setCompareError(errMsg(e));
      setCompare(null);
    } finally {
      setCompareLoading(false);
    }
  }, [compareInput, benchmark, range]);

  // Recalcula al cambiar benchmark o período.
  useEffect(() => {
    void run(symbol);
  }, [benchmark, range]);

  const m = analysis?.metrics;

  const rows = useMemo(
    () =>
      (compare?.rows ?? []).slice().sort((a, b) => {
        const ba = a.metrics?.beta.value ?? -Infinity;
        const bb = b.metrics?.beta.value ?? -Infinity;
        return bb - ba;
      }),
    [compare],
  );

  return (
    <>
      <div className="page-head">
        <h1>Mercados</h1>
        <p className="muted" style={{ margin: 0 }}>
          Cómo se mueve un activo frente a un índice de referencia. Precios diarios ajustados por
          dividendos y splits.
        </p>
      </div>

      <section className="card">
        <form
          className="controls"
          onSubmit={(e) => {
            e.preventDefault();
            void run(symbol);
          }}
        >
          <label className="field">
            Símbolo
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="ej. AAPL, GGAL, BTC-USD"
              style={{ width: 160 }}
              maxLength={15}
            />
          </label>
          <label className="field">
            Benchmark
            <select value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>
              {BENCHMARKS.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            Período
            <div className="segmented" role="group" aria-label="Período">
              {RANGES.map((r) => (
                <button
                  type="button"
                  key={r.value}
                  aria-pressed={range === r.value}
                  onClick={() => setRange(r.value)}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>
          <button className="primary" type="submit" disabled={loading}>
            {loading ? "Calculando…" : "Analizar"}
          </button>
        </form>
      </section>

      {error && <ErrorNotice message={error} />}
      {loading && !analysis && <Skeleton height={360} />}

      {analysis && m && (
        <>
          <section className="card" aria-busy={loading}>
            <div className="card-head">
              <h2>
                {analysis.symbol}
                {analysis.name ? <span className="muted"> · {analysis.name}</span> : null}
              </h2>
              <span className="faint">
                vs {analysis.benchmark}
                {analysis.start_date && analysis.end_date
                  ? ` · ${fmtDate(analysis.start_date)} – ${fmtDate(analysis.end_date)}`
                  : ""}{" "}
                · {analysis.observations} ruedas
              </span>
            </div>
            {m.beta.status !== "OK" && m.beta.note && (
              <div className="notice warn">{m.beta.note}</div>
            )}
            <div className="tiles">
              <Tile
                label="Beta"
                value={metricText(m.beta, "number")}
                sub={
                  m.beta.value !== null
                    ? `se mueve ${fmtNumber(Math.abs(m.beta.value))}× ${m.beta.value < 0 ? "en contra de" : "con"} el índice`
                    : undefined
                }
              />
              <Tile
                label="Correlación"
                value={metricText(m.correlation, "number")}
                sub={m.r_squared.value !== null ? `R² ${fmtNumber(m.r_squared.value)}` : undefined}
              />
              <Tile
                label={`Rendimiento ${analysis.symbol}`}
                value={metricText(m.asset_return, "fraction", true)}
                cls={signClass(m.asset_return)}
                sub={`${analysis.benchmark}: ${metricText(m.benchmark_return, "fraction", true)}`}
              />
              <Tile
                label="Volatilidad anualizada"
                value={metricText(m.asset_volatility, "fraction")}
                sub={`${analysis.benchmark}: ${metricText(m.benchmark_volatility, "fraction")}`}
              />
              <Tile
                label="Caída máxima"
                value={metricText(m.asset_max_drawdown, "fraction")}
                cls={signClass(m.asset_max_drawdown)}
                sub={`${analysis.benchmark}: ${metricText(m.benchmark_max_drawdown, "fraction")}`}
              />
            </div>

            <h3>Evolución, base 100</h3>
            <LineChartCard
              data={analysis.rebased_prices}
              xKey="date"
              xTicks={monthTicks(analysis.rebased_prices.map((p) => p.date))}
              series={[
                { key: "asset", label: analysis.symbol, color: "var(--series-1)" },
                { key: "benchmark", label: analysis.benchmark, color: "var(--series-neutral)" },
              ]}
              yFormat={(v) => fmtNumber(v, 0)}
              xFormat={fmtMonthYear}
              tooltipTitle={fmtDate}
              reference={100}
              ariaLabel={`Precio de ${analysis.symbol} y ${analysis.benchmark} con base 100`}
            />
            <SourceLine
              source={analysis.source}
              extra={
                (analysis.adjusted_prices ? "cierres ajustados" : "cierres SIN ajustar") +
                (analysis.dropped_dates
                  ? ` · ${analysis.dropped_dates} días descartados por cotizar sólo uno de los dos`
                  : "")
              }
            />
          </section>

          {analysis.rolling.length > 0 && (
            <div className="grid-2">
              <section className="card">
                <div className="card-head">
                  <h2>Correlación móvil</h2>
                  <span className="faint">ventana de {analysis.rolling_window} ruedas</span>
                </div>
                <LineChartCard
                  small
                  data={analysis.rolling}
                  xKey="date"
                  xTicks={monthTicks(analysis.rolling.map((p) => p.date), 6)}
                  series={[{ key: "correlation", label: "Correlación", color: "var(--series-1)" }]}
                  yFormat={(v) => fmtNumber(v)}
                  xFormat={fmtMonthYear}
                  tooltipTitle={fmtDate}
                  reference={0}
                  ariaLabel={`Correlación móvil de ${analysis.symbol} con ${analysis.benchmark}`}
                />
              </section>
              <section className="card">
                <div className="card-head">
                  <h2>Beta móvil</h2>
                  <span className="faint">ventana de {analysis.rolling_window} ruedas</span>
                </div>
                <LineChartCard
                  small
                  data={analysis.rolling}
                  xKey="date"
                  xTicks={monthTicks(analysis.rolling.map((p) => p.date), 6)}
                  series={[{ key: "beta", label: "Beta", color: "var(--series-1)" }]}
                  yFormat={(v) => fmtNumber(v)}
                  xFormat={fmtMonthYear}
                  tooltipTitle={fmtDate}
                  reference={1}
                  ariaLabel={`Beta móvil de ${analysis.symbol} contra ${analysis.benchmark}`}
                />
              </section>
            </div>
          )}
        </>
      )}

      <section className="card">
        <div className="card-head">
          <h2>Comparar varios activos</h2>
          <span className="faint">
            contra {benchmark} · {RANGES.find((r) => r.value === range)?.label} · hasta 15 símbolos
          </span>
        </div>
        <div className="chips">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              className="chip"
              aria-pressed={compareInput === p.symbols}
              onClick={() => setCompareInput(p.symbols)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <form
          className="controls"
          onSubmit={(e) => {
            e.preventDefault();
            void runCompare();
          }}
        >
          <label className="field" style={{ flex: 1, minWidth: 240 }}>
            Símbolos (separados por coma)
            <input value={compareInput} onChange={(e) => setCompareInput(e.target.value.toUpperCase())} />
          </label>
          <button className="primary" type="submit" disabled={compareLoading}>
            {compareLoading ? "Calculando…" : "Comparar"}
          </button>
        </form>
        {compareError && <ErrorNotice message={compareError} />}
        {compare && (
          <>
            <div className="table-wrap">
              <table className="tabular">
                <thead>
                  <tr>
                    <th scope="col">Activo</th>
                    <th scope="col">Beta</th>
                    <th scope="col">Correlación</th>
                    <th scope="col">R²</th>
                    <th scope="col">Rendimiento</th>
                    <th scope="col">Volatilidad</th>
                    <th scope="col">Caída máx.</th>
                    <th scope="col">Ruedas</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.symbol}>
                      <td>
                        <button
                          className="link"
                          style={{ padding: 0, color: "var(--accent)" }}
                          onClick={() => {
                            setSymbol(r.symbol);
                            void run(r.symbol);
                            window.scrollTo({ top: 0, behavior: "smooth" });
                          }}
                        >
                          {r.symbol}
                        </button>
                        <span className="sub">{r.name ?? r.note ?? ""}</span>
                      </td>
                      {r.metrics ? (
                        <>
                          <td>{metricText(r.metrics.beta, "number")}</td>
                          <td>{metricText(r.metrics.correlation, "number")}</td>
                          <td>{metricText(r.metrics.r_squared, "number")}</td>
                          <td className={signClass(r.metrics.asset_return)}>
                            {metricText(r.metrics.asset_return, "fraction", true)}
                          </td>
                          <td>{metricText(r.metrics.asset_volatility, "fraction")}</td>
                          <td>{metricText(r.metrics.asset_max_drawdown, "fraction")}</td>
                        </>
                      ) : (
                        <td colSpan={6} className="muted">
                          {r.status === "ERROR_FUENTE" ? "Error de la fuente" : "Sin datos"}
                        </td>
                      )}
                      <td>{r.observations || NO_DATA}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <SourceLine
              source={compare.source}
              extra={`ordenado por beta · rendimiento del ${compare.benchmark} en el período: ${
                fmtFraction(
                  compare.rows.find((r) => r.metrics?.benchmark_return.value != null)?.metrics
                    ?.benchmark_return.value ?? null,
                  1,
                  true,
                )
              }`}
            />
          </>
        )}
      </section>
    </>
  );
}
