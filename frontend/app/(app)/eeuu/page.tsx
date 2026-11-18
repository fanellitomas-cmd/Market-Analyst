"use client";

import { useEffect, useMemo, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { EconCalendar } from "@/components/EconCalendar";
import { LineChartCard } from "@/components/LineChartCard";
import { UsTile } from "@/components/UsTile";
import { api, ApiError, qs } from "@/lib/api";
import { fmtDate, fmtMonthYear, fmtPercentValue, fmtUsValue, monthTicks } from "@/lib/format";
import type { UsDashboardResponse, UsSeriesResponse, YieldCurveResponse } from "@/lib/types";

const RANGES = [
  { key: "1y", label: "1A", years: 1 },
  { key: "5y", label: "5A", years: 5 },
  { key: "10y", label: "10A", years: 10 },
  { key: "max", label: "Desde 2000", years: null },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

const CURVE_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-neutral)"];

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Error inesperado.";
}

function startFor(range: RangeKey): string {
  const r = RANGES.find((x) => x.key === range);
  if (!r?.years) return "2000-01-01";
  const d = new Date();
  d.setFullYear(d.getFullYear() - r.years);
  return d.toISOString().slice(0, 10);
}

export default function EeuuPage() {
  const [dash, setDash] = useState<UsDashboardResponse | null>(null);
  const [dashError, setDashError] = useState<string | null>(null);
  const [curve, setCurve] = useState<YieldCurveResponse | null>(null);
  const [curveError, setCurveError] = useState<string | null>(null);

  const [selected, setSelected] = useState("t10y");
  const [range, setRange] = useState<RangeKey>("5y");
  const [series, setSeries] = useState<UsSeriesResponse | null>(null);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [seriesLoading, setSeriesLoading] = useState(false);

  useEffect(() => {
    api<UsDashboardResponse>("/api/us/dashboard")
      .then(setDash)
      .catch((e) => setDashError(errMsg(e)));
    api<YieldCurveResponse>("/api/us/yield-curve")
      .then(setCurve)
      .catch((e) => setCurveError(errMsg(e)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setSeriesLoading(true);
    setSeriesError(null);
    api<UsSeriesResponse>(`/api/us/series?${qs({ indicator: selected, start: startFor(range) })}`)
      .then((d) => !cancelled && setSeries(d))
      .catch((e) => !cancelled && setSeriesError(errMsg(e)))
      .finally(() => !cancelled && setSeriesLoading(false));
    return () => {
      cancelled = true;
    };
  }, [selected, range]);

  const groups = useMemo(() => {
    const g = new Map<string, UsDashboardResponse["items"]>();
    dash?.items.forEach((i) =>
      g.set(i.indicator.category, [...(g.get(i.indicator.category) ?? []), i]),
    );
    return [...g.entries()];
  }, [dash]);

  const curveRows = useMemo(() => {
    if (!curve) return [];
    return curve.curves[0].points.map((p, i) => {
      const row: Record<string, string | number | null> = { maturity: p.maturity };
      curve.curves.forEach((c, ci) => {
        row[`c${ci}`] = c.points[i]?.value ?? null;
      });
      return row;
    });
  }, [curve]);

  const curveMissing = curve?.curves.flatMap((c) =>
    c.points.filter((p) => p.status !== "OK").map((p) => `${p.maturity} (${c.label.toLowerCase()})`),
  );

  const unit = series?.indicator.unit ?? "";
  const dates = series?.observations.map((o) => o.date) ?? [];
  const showZero = unit.startsWith("%") || unit === "p.p." || unit === "miles";

  return (
    <>
      <div className="page-head">
        <h1>Estados Unidos</h1>
        <p className="muted" style={{ margin: 0 }}>
          Tasas, inflación, empleo, actividad y riesgo, con las series oficiales de la Reserva Federal
          de St. Louis. Tocá una tarjeta para ver su historia.
        </p>
      </div>

      {dashError && <ErrorNotice message={dashError} />}
      {!dash && !dashError && <Skeleton height={300} />}
      {groups.map(([category, items]) => (
        <section className="card" key={category}>
          <h2>{category}</h2>
          <div className="tiles">
            {items.map((item) => (
              <UsTile
                key={item.indicator.key}
                item={item}
                selected={selected === item.indicator.key}
                onSelect={() => {
                  setSelected(item.indicator.key);
                  document.getElementById("historia")?.scrollIntoView({ behavior: "smooth" });
                }}
              />
            ))}
          </div>
        </section>
      ))}
      {dash && <SourceLine source={dash.source} />}

      <EconCalendar />

      <section className="card" id="historia" aria-busy={seriesLoading}>
        <div className="card-head">
          <h2>{series?.indicator.name ?? "Historia"}</h2>
          <div className="segmented" role="group" aria-label="Período">
            {RANGES.map((r) => (
              <button
                type="button"
                key={r.key}
                aria-pressed={range === r.key}
                onClick={() => setRange(r.key)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        {series && (
          <p className="muted" style={{ margin: 0 }}>
            {series.indicator.description}{" "}
            <span className="faint">
              ({series.indicator.unit} · {series.indicator.frequency.toLowerCase()})
            </span>
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
              yFormat={(v) => fmtUsValue(v, unit)}
              xFormat={fmtMonthYear}
              xTicks={monthTicks(dates)}
              tooltipTitle={fmtDate}
              reference={showZero ? 0 : undefined}
              ariaLabel={`${series.indicator.name}, ${unit}`}
            />
            <SourceLine
              source={series.source}
              extra={[
                `serie ${series.indicator.series_id}`,
                series.indicator.transform === "yoy_pct"
                  ? "variación interanual calculada sobre el índice publicado"
                  : series.indicator.transform === "diff"
                    ? "variación mensual calculada sobre el total publicado"
                    : null,
                series.dropped_points
                  ? `${series.dropped_points} meses sin dato base, omitidos`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            />
          </>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Curva de rendimientos del Tesoro</h2>
          <span className="faint">rendimiento por plazo, en %</span>
        </div>
        <p className="muted" style={{ margin: 0 }}>
          Normalmente los plazos largos pagan más que los cortos. Si la curva baja hacia la derecha
          (está invertida), el mercado espera recortes de tasas, algo que suele anticipar una
          desaceleración.
        </p>
        {curveError && <ErrorNotice message={curveError} />}
        {!curve && !curveError && <Skeleton height={320} />}
        {curve && (
          <>
            {curveMissing && curveMissing.length > 0 && (
              <div className="notice warn">Sin dato para: {curveMissing.join(", ")}.</div>
            )}
            <LineChartCard
              data={curveRows}
              xKey="maturity"
              series={curve.curves.map((c, i) => ({
                key: `c${i}`,
                label: `${c.label} (${fmtDate(c.target_date)})`,
                color: CURVE_COLORS[i],
              }))}
              yFormat={(v) => fmtPercentValue(v, 2)}
              tooltipTitle={(m) => `Plazo ${m}`}
              ariaLabel="Curva de rendimientos del Tesoro de EE.UU. hoy, hace un mes y hace un año"
            />
            <div className="table-wrap">
              <table className="tabular">
                <thead>
                  <tr>
                    <th scope="col">Curva</th>
                    {curve.curves[0].points.map((p) => (
                      <th scope="col" key={p.maturity}>
                        {p.maturity}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {curve.curves.map((c, i) => (
                    <tr key={c.label}>
                      <td>
                        <i
                          className="swatch"
                          style={{ background: CURVE_COLORS[i], marginRight: 8 }}
                        />
                        {c.label}
                        <span className="sub">{fmtDate(c.target_date)}</span>
                      </td>
                      {c.points.map((p) => (
                        <td key={p.maturity} title={p.date ? `dato del ${fmtDate(p.date)}` : undefined}>
                          {fmtPercentValue(p.value, 2)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <SourceLine
              source={curve.source}
              extra="si un plazo no publicó ese día se usa el dato anterior más cercano (máx. 7 días)"
            />
          </>
        )}
      </section>
    </>
  );
}
