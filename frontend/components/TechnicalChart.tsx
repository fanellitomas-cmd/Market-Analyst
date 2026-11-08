"use client";

import { useEffect, useRef } from "react";
import type { TechnicalResponse } from "@/lib/types";

export const EMA_COLORS = { ema20: "--series-2", ema50: "--series-3", ema200: "--series-7" } as const;
export const AVWAP_COLORS: Record<string, string> = {
  earnings: "--series-1",
  high_52w: "--series-8",
  low_52w: "--series-6",
  ytd: "--series-4",
  custom: "--series-5",
};

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** Velas diarias con EMAs, AVWAPs, volumen (panel inferior) y POC/VAH/VAL como líneas. */
export function TechnicalChart({ data }: { data: TechnicalResponse }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let disposed = false;
    let cleanup: (() => void) | undefined;
    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed || !ref.current) return;
      const text = cssVar("--text-3");
      const grid = cssVar("--grid");
      const chart = lw.createChart(ref.current, {
        autoSize: true,
        layout: {
          background: { type: lw.ColorType.Solid, color: "transparent" },
          textColor: text,
          fontFamily: "Inter, system-ui, sans-serif",
          attributionLogo: true,
        },
        grid: { vertLines: { visible: false }, horzLines: { color: grid } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false },
        crosshair: { mode: lw.CrosshairMode.Normal },
        localization: { locale: "es-AR" },
      });

      const up = cssVar("--good");
      const down = cssVar("--bad");
      const candles = chart.addSeries(lw.CandlestickSeries, {
        upColor: up,
        downColor: down,
        borderVisible: false,
        wickUpColor: up,
        wickDownColor: down,
        priceLineVisible: false,
      });
      candles.setData(
        data.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })),
      );

      for (const [key, varName] of Object.entries(EMA_COLORS)) {
        const s = chart.addSeries(lw.LineSeries, {
          color: cssVar(varName),
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        s.setData(
          data.emas
            .filter((p) => p[key as keyof typeof EMA_COLORS] !== null)
            .map((p) => ({ time: p.time, value: p[key as keyof typeof EMA_COLORS] as number })),
        );
      }

      const first = data.candles[0]?.time ?? "";
      for (const a of data.avwaps) {
        if (a.status !== "OK") continue;
        const s = chart.addSeries(lw.LineSeries, {
          color: cssVar(AVWAP_COLORS[a.key] ?? "--series-1"),
          lineWidth: 2,
          lineStyle: lw.LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: true,
          title: "",
          crosshairMarkerVisible: false,
        });
        s.setData(a.series.filter((p) => p.time >= first).map((p) => ({ time: p.time, value: p.value })));
      }

      if (data.profile) {
        const neutral = cssVar("--text-2");
        for (const [price, title] of [
          [data.profile.poc, "POC"],
          [data.profile.vah, "VAH"],
          [data.profile.val, "VAL"],
        ] as const) {
          candles.createPriceLine({
            price,
            color: neutral,
            lineWidth: 1,
            lineStyle: lw.LineStyle.Dashed,
            axisLabelVisible: true,
            title,
          });
        }
      }

      if (data.candles.some((c) => c.volume !== null)) {
        const vol = chart.addSeries(
          lw.HistogramSeries,
          { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false },
          1,
        );
        const upVol = cssVar("--series-neutral");
        vol.setData(
          data.candles
            .filter((c) => c.volume !== null)
            .map((c) => ({ time: c.time, value: c.volume as number, color: upVol })),
        );
        chart.panes()[1]?.setHeight(90);
      }
      chart.timeScale().fitContent();
      cleanup = () => chart.remove();
    })();
    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [data]);

  return <div ref={ref} className="tv-chart" role="img" aria-label={`Velas diarias de ${data.symbol}`} />;
}

/** Perfil de volumen horizontal (últimos 60 días, velas de 30 minutos). */
export function VolumeProfile({ data }: { data: NonNullable<TechnicalResponse["profile"]> }) {
  const max = Math.max(...data.bins.map((b) => b.volume));
  const bins = [...data.bins].reverse(); // precios altos arriba
  const top = data.bins[data.bins.length - 1]?.high;
  const bottom = data.bins[0]?.low;
  return (
    <>
    <div className="faint vp-label">{top?.toFixed(2)}</div>
    <div className="vprofile" role="img" aria-label="Perfil de volumen">
      {bins.map((b) => {
        const inValue = b.high > data.val && b.low < data.vah;
        const isPoc = data.poc >= b.low && data.poc < b.high;
        return (
          <div className="vp-row" key={b.low} title={`${b.low.toFixed(2)} – ${b.high.toFixed(2)}`}>
            <span
              className={`vp-bar${inValue ? " vp-va" : ""}${isPoc ? " vp-poc" : ""}`}
              style={{ width: `${(b.volume / max) * 100}%` }}
            />
          </div>
        );
      })}
    </div>
    <div className="faint vp-label">{bottom?.toFixed(2)}</div>
    </>
  );
}
