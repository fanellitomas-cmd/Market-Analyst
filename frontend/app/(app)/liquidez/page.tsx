"use client";

import { useEffect, useMemo, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { LineChartCard } from "@/components/LineChartCard";
import { api, ApiError, qs } from "@/lib/api";
import {
  fmtDate,
  fmtLiqAxis,
  fmtLiqChange,
  fmtLiqValue,
  fmtMonthYear,
  monthTicks,
} from "@/lib/format";
import type { LiqIndicator, LiquidityDashboard, LiquiditySeries } from "@/lib/types";

const RANGES = [
  { key: "1y", label: "1A", years: 1 },
  { key: "3y", label: "3A", years: 3 },
  { key: "5y", label: "5A", years: 5 },
  { key: "max", label: "Máx.", years: null },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

const GROUP_INTRO: Record<string, string> = {
  "Bancos centrales":
    "Cuánto dinero inyectan los bancos centrales. Balances que crecen suelen acompañar subas de activos de riesgo; balances que se achican, lo contrario.",
  Dólar: "Un dólar fuerte endurece las condiciones financieras globales, sobre todo para emergentes.",
  Crédito: "Spreads que se ensanchan muestran que el mercado exige más prima por riesgo de impago.",
  Condiciones: "Resumen de tasas, crédito y volatilidad en un solo número.",
  "Costo de capital":
    "La tasa real es el piso del costo de capital: cuando sube, comprime los múltiplos, sobre todo de empresas de crecimiento. El cociente cíclicos/defensivos muestra hacia dónde rota el mercado.",
};

function startFor(range: RangeKey): string {
  const r = RANGES.find((x) => x.key === range);
  if (!r?.years) return "2000-01-01";
  const d = new Date();
  d.setFullYear(d.getFullYear() - r.years);
  return d.toISOString().slice(0, 10);
}

function changeClass(item: LiqIndicator, v: number | null): string | undefined {
  if (v === null || v === 0) return undefined;
  // Para spreads y condiciones, subir es endurecimiento (malo para el riesgo); se marca en rojo.
  const tightening = ["hy_spread", "ig_spread", "nfci", "real_10y", "dxy", "dollar_broad"].includes(
    item.key,
  );
  return (v > 0) !== tightening ? "pos" : "neg";
}

function LiqTile({ item, selected, onSelect }: { item: LiqIndicator; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      className="tile tile-button"
      aria-pressed={selected}
      onClick={onSelect}
      title={item.description}
    >
      <div className="tile-label">{item.name}</div>
      <div className="tile-value">{fmtLiqValue(item.latest?.value ?? null, item.unit)}</div>
      <div className="tile-sub">
        {item.latest ? fmtDate(item.latest.date) : item.status === "ERROR_FUENTE" ? "Fuente no disponible" : "Sin dato"}
      </div>
      {item.changes.length > 0 && (
        <div className="change-row">
          {item.changes.map((c) => (
            <span key={c.label} title={c.base ? `contra ${fmtDate(c.base.date)}` : "sin dato comparable"}>
              <span className="faint">{c.label}</span>{" "}
              <b className={changeClass(item, c.value)}>{fmtLiqChange(c.value, c.kind, item.unit)}</b>
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

export default function LiquidezPage() {
  const [dash, setDash] = useState<LiquidityDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState("net_liquidity");
  const [range, setRange] = useState<RangeKey>("3y");
  const [series, setSeries] = useState<LiquiditySeries | null>(null);
  const [seriesError, setSeriesError] = useState<string | null>(null);

  useEffect(() => {
    api<LiquidityDashboard>("/api/liquidity/dashboard")
      .then(setDash)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Error inesperado."));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setSeriesError(null);
    api<LiquiditySeries>(`/api/liquidity/series?${qs({ key: selected, start: startFor(range) })}`)
      .then((d) => !cancelled && setSeries(d))
      .catch((e) => {
        if (cancelled) return;
        setSeries(null);
        setSeriesError(e instanceof ApiError ? e.message : "Error inesperado.");
      });
    return () => {
      cancelled = true;
    };
  }, [selected, range]);

  const groups = useMemo(() => {
    const g = new Map<string, LiqIndicator[]>();
    dash?.indicators.forEach((i) => g.set(i.group, [...(g.get(i.group) ?? []), i]));
    return [...g.entries()];
  }, [dash]);

  const current = dash?.indicators.find((i) => i.key === selected);

  return (
    <>
      <div className="page-head">
        <h1>Liquidez y condiciones financieras</h1>
        <p className="muted" style={{ margin: 0 }}>
          El régimen de liquidez global: bancos centrales, dólar, crédito y costo de capital. Los
          cambios se miden contra el dato publicado hace 1 mes, 3 meses y 1 año. Tocá una tarjeta para
          ver su historia.
        </p>
      </div>

      {error && <ErrorNotice message={error} />}
      {!dash && !error && <Skeleton height={300} />}
      {groups.map(([group, items]) => (
        <section className="card" key={group}>
          <h2>{group}</h2>
          {GROUP_INTRO[group] && (
            <p className="muted" style={{ margin: 0 }}>
              {GROUP_INTRO[group]}
            </p>
          )}
          <div className="tiles tiles-wide">
            {items.map((item) => (
              <LiqTile
                key={item.key}
                item={item}
                selected={selected === item.key}
                onSelect={() => {
                  setSelected(item.key);
                  document.getElementById("historia-liq")?.scrollIntoView({ behavior: "smooth" });
                }}
              />
            ))}
          </div>
        </section>
      ))}

      <section className="card" id="historia-liq">
        <div className="card-head">
          <h2>{series?.name ?? current?.name ?? "Historia"}</h2>
          <div className="segmented" role="group" aria-label="Período">
            {RANGES.map((r) => (
              <button type="button" key={r.key} aria-pressed={range === r.key} onClick={() => setRange(r.key)}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
        {current && (
          <p className="muted" style={{ margin: 0 }}>
            {current.description} <span className="faint">({current.unit})</span>
          </p>
        )}
        {seriesError && <ErrorNotice message={seriesError} />}
        {!series && !seriesError && <Skeleton height={320} />}
        {series && series.points.length === 0 && <p className="muted">Sin datos en el período elegido.</p>}
        {series && series.points.length > 0 && (
          <>
            <LineChartCard
              data={series.points}
              xKey="date"
              series={[{ key: "value", label: series.name, color: "var(--series-1)" }]}
              yFormat={(v) => fmtLiqAxis(v, series.unit)}
              xFormat={fmtMonthYear}
              xTicks={monthTicks(series.points.map((p) => p.date))}
              tooltipTitle={fmtDate}
              reference={series.key === "nfci" ? 0 : series.key === "cyc_def" ? 100 : undefined}
              ariaLabel={`${series.name}, ${series.unit}`}
            />
            <SourceLine source={series.source} extra={series.note ?? undefined} />
          </>
        )}
      </section>
    </>
  );
}
