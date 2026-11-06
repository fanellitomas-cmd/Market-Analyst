"use client";

import { useEffect, useMemo, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { LineChartCard } from "@/components/LineChartCard";
import { api, ApiError, qs } from "@/lib/api";
import {
  AR_DEFAULT_UNIT,
  fmtArValue,
  fmtDate,
  fmtDateTime,
  fmtFraction,
  fmtMonthYear,
  fmtNumber,
  monthTicks,
  NO_DATA,
  SERIES_COLORS,
} from "@/lib/format";
import type { ArDashboardResponse, ArLatest, ArSeriesResponse } from "@/lib/types";

const RANGES = [
  { key: "6m", label: "6M", days: 182 },
  { key: "1y", label: "1A", days: 365 },
  { key: "3y", label: "3A", days: 365 * 3 },
  { key: "max", label: "Desde 2010", days: null },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

const DOLLAR_CHART = [
  { house: "oficial", label: "Oficial" },
  { house: "bolsa", label: "MEP" },
  { house: "contadoconliqui", label: "CCL" },
  { house: "blue", label: "Blue" },
];

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Error inesperado.";
}

function startFor(range: RangeKey): string {
  const r = RANGES.find((x) => x.key === range);
  if (!r?.days) return "2010-01-01";
  return new Date(Date.now() - r.days * 86_400_000).toISOString().slice(0, 10);
}

function RangeButtons({ value, onChange }: { value: RangeKey; onChange: (r: RangeKey) => void }) {
  return (
    <div className="segmented" role="group" aria-label="Período">
      {RANGES.map((r) => (
        <button type="button" key={r.key} aria-pressed={value === r.key} onClick={() => onChange(r.key)}>
          {r.label}
        </button>
      ))}
    </div>
  );
}

function ArTile({
  item,
  selected,
  onSelect,
  sub,
}: {
  item: ArLatest;
  selected: boolean;
  onSelect: () => void;
  sub?: string;
}) {
  const unit = item.source_unit ?? AR_DEFAULT_UNIT[item.indicator.kind];
  const showUnit = !["fx", "pct", "bps"].includes(item.indicator.kind);
  return (
    <button
      type="button"
      className="tile tile-button"
      aria-pressed={selected}
      onClick={onSelect}
      title={item.source_description ?? item.indicator.description}
    >
      <div className="tile-label">{item.indicator.name}</div>
      <div className="tile-value">{fmtArValue(item.value, item.indicator.kind)}</div>
      <div className="tile-sub">
        {item.status === "OK" && item.date
          ? fmtDate(item.date)
          : item.status === "ERROR_FUENTE"
            ? "Fuente no disponible"
            : "Sin dato"}
        {showUnit && item.status === "OK" ? ` · ${unit}` : ""}
        {sub ? (
          <>
            <br />
            {sub}
          </>
        ) : null}
      </div>
    </button>
  );
}

export default function ArgentinaPage() {
  const [dash, setDash] = useState<ArDashboardResponse | null>(null);
  const [dashError, setDashError] = useState<string | null>(null);

  const [dollarRange, setDollarRange] = useState<RangeKey>("1y");
  const [dollarSeries, setDollarSeries] = useState<(ArSeriesResponse | null)[] | null>(null);

  const [selected, setSelected] = useState("riesgo_pais");
  const [range, setRange] = useState<RangeKey>("1y");
  const [series, setSeries] = useState<ArSeriesResponse | null>(null);
  const [seriesError, setSeriesError] = useState<string | null>(null);

  useEffect(() => {
    api<ArDashboardResponse>("/api/ar/dashboard")
      .then(setDash)
      .catch((e) => setDashError(errMsg(e)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setDollarSeries(null);
    const start = startFor(dollarRange);
    Promise.all(
      DOLLAR_CHART.map((d) =>
        api<ArSeriesResponse>(`/api/ar/series?${qs({ indicator: `dolar_${d.house}`, start })}`).catch(
          () => null,
        ),
      ),
    ).then((res) => !cancelled && setDollarSeries(res));
    return () => {
      cancelled = true;
    };
  }, [dollarRange]);

  useEffect(() => {
    let cancelled = false;
    setSeriesError(null);
    api<ArSeriesResponse>(`/api/ar/series?${qs({ indicator: selected, start: startFor(range) })}`)
      .then((d) => !cancelled && setSeries(d))
      .catch((e) => {
        if (cancelled) return;
        setSeries(null);
        setSeriesError(errMsg(e));
      });
    return () => {
      cancelled = true;
    };
  }, [selected, range]);

  const dollarRows = useMemo(() => {
    if (!dollarSeries) return [];
    const byDate = new Map<string, Record<string, string | number | null>>();
    dollarSeries.forEach((s, i) => {
      s?.observations.forEach((o) => {
        const row = byDate.get(o.date) ?? { date: o.date };
        row[DOLLAR_CHART[i].house] = o.value;
        byDate.set(o.date, row);
      });
    });
    return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [dollarSeries]);

  const dollarFailed = dollarSeries
    ? DOLLAR_CHART.filter((_, i) => dollarSeries[i] === null).map((d) => d.label)
    : [];

  const groups = useMemo(() => {
    const g = new Map<string, ArLatest[]>();
    if (dash) g.set("Riesgo", [dash.country_risk]);
    dash?.bcra.forEach((i) => g.set(i.indicator.group, [...(g.get(i.indicator.group) ?? []), i]));
    return [...g.entries()];
  }, [dash]);

  function select(key: string) {
    setSelected(key);
    document.getElementById("historia-ar")?.scrollIntoView({ behavior: "smooth" });
  }

  const kind = series?.indicator.kind ?? "index";

  return (
    <>
      <div className="page-head">
        <h1>Argentina</h1>
        <p className="muted" style={{ margin: 0 }}>
          Dólar, riesgo país y las principales variables del BCRA: reservas, tasas, inflación e índices
          de ajuste.
        </p>
      </div>

      {dashError && <ErrorNotice message={dashError} />}

      <section className="card">
        <div className="card-head">
          <h2>Dólar</h2>
          <span className="faint">pesos por dólar</span>
        </div>
        {!dash && !dashError && <Skeleton height={220} />}
        {dash && dash.dollars.status !== "OK" && (
          <div className="notice warn">
            No se pudieron obtener las cotizaciones{dash.dollars.note ? `: ${dash.dollars.note}` : "."}
          </div>
        )}
        {dash && dash.dollars.rows.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="tabular">
                <thead>
                  <tr>
                    <th scope="col">Tipo</th>
                    <th scope="col">Compra</th>
                    <th scope="col">Venta</th>
                    <th scope="col">Brecha vs. oficial</th>
                    <th scope="col">Actualizado</th>
                  </tr>
                </thead>
                <tbody>
                  {dash.dollars.rows.map((r) => (
                    <tr key={r.house}>
                      <td>{r.name}</td>
                      <td>{fmtArValue(r.buy, "fx")}</td>
                      <td>
                        <b>{fmtArValue(r.sell, "fx")}</b>
                      </td>
                      <td>{r.gap_vs_official === null ? NO_DATA : fmtFraction(r.gap_vs_official, 1, true)}</td>
                      <td className="muted">{r.updated_at ? fmtDateTime(r.updated_at) : NO_DATA}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <SourceLine
              source={dash.dollars.source}
              extra="fuente comunitaria; el blue no tiene cotización oficial"
            />
          </>
        )}

        <div className="card-head" style={{ marginTop: 8 }}>
          <h3>Evolución</h3>
          <RangeButtons value={dollarRange} onChange={setDollarRange} />
        </div>
        {!dollarSeries && <Skeleton height={320} />}
        {dollarSeries && dollarFailed.length > 0 && (
          <div className="notice warn">Sin historia para: {dollarFailed.join(", ")}.</div>
        )}
        {dollarSeries && dollarRows.length > 0 && (
          <LineChartCard
            data={dollarRows}
            xKey="date"
            series={DOLLAR_CHART.filter((_, i) => dollarSeries[i] !== null).map((d) => ({
              key: d.house,
              label: d.label,
              color: SERIES_COLORS[DOLLAR_CHART.indexOf(d)],
            }))}
            yFormat={(v) => `$ ${fmtNumber(v, 0)}`}
            xFormat={fmtMonthYear}
            xTicks={monthTicks(dollarRows.map((r) => String(r.date)))}
            tooltipTitle={fmtDate}
            ariaLabel="Dólar oficial, MEP, contado con liqui y blue en el tiempo"
          />
        )}
        {dollarSeries?.find(Boolean) && (
          <SourceLine source={dollarSeries.find(Boolean)!.source} extra="precio de venta, diario" />
        )}
      </section>

      {!dash && !dashError && <Skeleton height={300} />}
      {groups.map(([group, items]) => (
        <section className="card" key={group}>
          <h2>{group === "BCRA" ? "Reservas, dinero y tipo de cambio" : group}</h2>
          <div className="tiles">
            {items.map((item) => (
              <ArTile
                key={item.indicator.key}
                item={item}
                selected={selected === item.indicator.key}
                onSelect={() => select(item.indicator.key)}
                sub={
                  item.indicator.key === "riesgo_pais" && dash?.country_risk_change != null
                    ? `${dash.country_risk_change > 0 ? "+" : ""}${fmtNumber(dash.country_risk_change, 0)} pb vs. dato anterior`
                    : undefined
                }
              />
            ))}
          </div>
        </section>
      ))}
      {dash && <SourceLine source={dash.bcra_source} extra="riesgo país: ArgentinaDatos (EMBI de JP Morgan)" />}

      <section className="card" id="historia-ar">
        <div className="card-head">
          <h2>{series?.indicator.name ?? "Historia"}</h2>
          <RangeButtons value={range} onChange={setRange} />
        </div>
        {series && (
          <p className="muted" style={{ margin: 0 }}>
            {series.indicator.description}
          </p>
        )}
        {seriesError && <ErrorNotice message={seriesError} />}
        {!series && !seriesError && <Skeleton height={320} />}
        {series && series.observations.length === 0 && (
          <p className="muted">La serie no tiene datos en el período elegido.</p>
        )}
        {series && series.observations.length > 0 && (
          <>
            <LineChartCard
              data={series.observations}
              xKey="date"
              series={[{ key: "value", label: series.indicator.name, color: "var(--series-1)" }]}
              yFormat={(v) => fmtArValue(v, kind)}
              xFormat={fmtMonthYear}
              xTicks={monthTicks(series.observations.map((o) => o.date))}
              tooltipTitle={fmtDate}
              ariaLabel={series.indicator.name}
            />
            <SourceLine
              source={series.source}
              extra={series.source_description ? `serie: ${series.source_description}` : undefined}
            />
          </>
        )}
      </section>
    </>
  );
}
