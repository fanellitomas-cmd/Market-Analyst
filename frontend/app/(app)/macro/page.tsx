"use client";

import { useEffect, useMemo, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { LineChartCard, type SeriesDef } from "@/components/LineChartCard";
import { api, ApiError, qs } from "@/lib/api";
import { fmtByUnit, SERIES_COLORS } from "@/lib/format";
import type { CountryInfo, IndicatorInfo, MacroSeriesResponse } from "@/lib/types";

const MAX_COUNTRIES = SERIES_COLORS.length;
const START_OPTIONS = [1990, 2000, 2010, 2015];

export default function MacroPage() {
  const [indicators, setIndicators] = useState<IndicatorInfo[]>([]);
  const [countries, setCountries] = useState<CountryInfo[]>([]);
  const [indicator, setIndicator] = useState("gdp_growth");
  // código de país → índice de color. El color queda fijo mientras el país esté seleccionado.
  const [slots, setSlots] = useState<Record<string, number>>({ USA: 0, ARG: 1, BRA: 2 });
  const [start, setStart] = useState(2000);
  const [data, setData] = useState<MacroSeriesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const selected = useMemo(
    () => Object.keys(slots).sort((a, b) => slots[a] - slots[b]),
    [slots],
  );

  useEffect(() => {
    Promise.all([
      api<IndicatorInfo[]>("/api/macro/indicators"),
      api<CountryInfo[]>("/api/macro/countries"),
    ])
      .then(([i, c]) => {
        setIndicators(i);
        setCountries(c);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Error inesperado."));
  }, []);

  useEffect(() => {
    if (selected.length === 0) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api<MacroSeriesResponse>(
      `/api/macro/series?${qs({ indicator, countries: selected.join(","), start })}`,
    )
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : "Error inesperado."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [indicator, selected, start]);

  function toggle(code: string) {
    setSlots((prev) => {
      if (code in prev) {
        const next = { ...prev };
        delete next[code];
        return next;
      }
      const used = new Set(Object.values(prev));
      const free = SERIES_COLORS.findIndex((_, i) => !used.has(i));
      if (free === -1) return prev;
      return { ...prev, [code]: free };
    });
  }

  const unit = data?.indicator.unit ?? "";
  const yFormat = (v: number) => fmtByUnit(v, unit);

  const series: SeriesDef[] = (data?.series ?? [])
    .filter((s) => s.status === "OK")
    .map((s) => ({
      key: s.country_code,
      label: s.country_name,
      color: SERIES_COLORS[slots[s.country_code] ?? 0],
    }));

  const rows = useMemo(() => {
    if (!data) return [];
    const periods = new Set<string>();
    data.series.forEach((s) => s.observations.forEach((o) => periods.add(o.period)));
    return [...periods].sort().map((period) => {
      const row: Record<string, string | number | null> = { period };
      for (const s of data.series) {
        row[s.country_code] = s.observations.find((o) => o.period === period)?.value ?? null;
      }
      return row;
    });
  }, [data]);

  const groups = useMemo(() => {
    const g = new Map<string, CountryInfo[]>();
    countries.forEach((c) => g.set(c.group, [...(g.get(c.group) ?? []), c]));
    return [...g.entries()];
  }, [countries]);

  const current = indicators.find((i) => i.key === indicator);

  return (
    <>
      <div className="page-head">
        <h1>Macro por país</h1>
        <p className="muted" style={{ margin: 0 }}>
          Series anuales del Banco Mundial. Elegí hasta {MAX_COUNTRIES} países para comparar.
        </p>
      </div>

      <section className="card">
        <div className="controls">
          <label className="field">
            Indicador
            <select value={indicator} onChange={(e) => setIndicator(e.target.value)}>
              {indicators.map((i) => (
                <option key={i.key} value={i.key}>
                  {i.name} ({i.unit})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Desde
            <select value={start} onChange={(e) => setStart(Number(e.target.value))}>
              {START_OPTIONS.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
        </div>
        {groups.map(([group, list]) => (
          <div key={group} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="faint">{group}</span>
            <div className="chips">
              {list.map((c) => {
                const on = c.code in slots;
                const full = !on && selected.length >= MAX_COUNTRIES;
                return (
                  <button
                    key={c.code}
                    className="chip"
                    aria-pressed={on}
                    disabled={full}
                    onClick={() => toggle(c.code)}
                  >
                    {on && (
                      <i className="swatch" style={{ background: SERIES_COLORS[slots[c.code]] }} />
                    )}
                    {c.name}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      <section className="card" aria-busy={loading}>
        <div className="card-head">
          <h2>{current?.name ?? "Indicador"}</h2>
          {current && <span className="faint">{current.unit}</span>}
        </div>
        {current && (
          <p className="muted" style={{ margin: 0 }}>
            {current.description}
          </p>
        )}
        {error && <ErrorNotice message={error} />}
        {selected.length === 0 && <p className="muted">Elegí al menos un país.</p>}
        {!data && !error && selected.length > 0 && <Skeleton height={320} />}
        {data && (
          <>
            {data.series.some((s) => s.status !== "OK") && (
              <div className="notice warn">
                Sin datos en el período para:{" "}
                {data.series
                  .filter((s) => s.status !== "OK")
                  .map((s) => s.country_name)
                  .join(", ")}
                .
              </div>
            )}
            {series.length > 0 && (
              <LineChartCard
                data={rows}
                xKey="period"
                series={series}
                yFormat={yFormat}
                reference={unit.startsWith("%") ? 0 : undefined}
                ariaLabel={`${data.indicator.name} por país, ${data.start_year}-${data.end_year}`}
              />
            )}
            <div className="table-wrap">
              <table className="tabular">
                <thead>
                  <tr>
                    <th scope="col">País</th>
                    <th scope="col">Último dato</th>
                    <th scope="col">Año</th>
                    <th scope="col">Años sin dato</th>
                  </tr>
                </thead>
                <tbody>
                  {data.series.map((s) => {
                    const last = s.observations.at(-1);
                    return (
                      <tr key={s.country_code}>
                        <td>
                          <i
                            className="swatch"
                            style={{
                              background: SERIES_COLORS[slots[s.country_code] ?? 0],
                              marginRight: 8,
                            }}
                          />
                          {s.country_name}
                        </td>
                        <td>{fmtByUnit(last?.value ?? null, unit)}</td>
                        <td>{last?.period ?? "—"}</td>
                        <td>{s.missing_periods}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <SourceLine
              source={data.source}
              extra={`indicador ${data.indicator.source_id}. Los años sin dato se muestran como huecos, no se interpolan.`}
            />
          </>
        )}
      </section>
    </>
  );
}
