"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface SeriesDef {
  key: string;
  label: string;
  color: string;
}

type Row = object;

interface Props {
  data: Row[];
  xKey: string;
  series: SeriesDef[];
  yFormat: (v: number) => string;
  xFormat?: (v: string) => string;
  xTicks?: string[];
  tooltipTitle?: (x: string) => string;
  reference?: number;
  small?: boolean;
  ariaLabel: string;
}

/** Gráfico de líneas con un solo eje, crosshair y tooltip. Sin interpolación: un valor `null`
 *  se muestra como hueco en la línea (`connectNulls={false}`). */
export function LineChartCard({
  data,
  xKey,
  series,
  yFormat,
  xFormat,
  xTicks,
  tooltipTitle,
  reference,
  small,
  ariaLabel,
}: Props) {
  return (
    <>
      {series.length > 1 && (
        <div className="legend" aria-hidden="true">
          {series.map((s) => (
            <span key={s.key}>
              <i className="swatch" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
      <div className={small ? "chart-sm" : "chart"} role="img" aria-label={ariaLabel}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey={xKey}
              tickFormatter={xFormat}
              ticks={xTicks}
              interval={xTicks ? 0 : "preserveEnd"}
              stroke="var(--text-3)"
              tick={{ fill: "var(--text-3)", fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
              minTickGap={xTicks ? 0 : 32}
            />
            <YAxis
              tickFormatter={yFormat}
              tick={{ fill: "var(--text-3)", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              width={64}
              domain={["auto", "auto"]}
            />
            {reference !== undefined && (
              <ReferenceLine y={reference} stroke="var(--text-3)" strokeDasharray="3 3" />
            )}
            <Tooltip
              cursor={{ stroke: "var(--text-3)", strokeWidth: 1 }}
              content={({ active, label, payload }) => {
                if (!active || !payload?.length) return null;
                const x = String(label);
                return (
                  <div className="tooltip">
                    <div className="tooltip-title">{tooltipTitle ? tooltipTitle(x) : x}</div>
                    {series.map((s) => {
                      const item = payload.find((p) => p.dataKey === s.key);
                      const v = item?.value;
                      return (
                        <div className="tooltip-row" key={s.key}>
                          <i className="swatch" style={{ background: s.color }} />
                          {s.label}
                          <b>{typeof v === "number" ? yFormat(v) : "sin dato"}</b>
                        </div>
                      );
                    })}
                  </div>
                );
              }}
            />
            {series.map((s) => (
              <Line
                key={s.key}
                type="linear"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}
