"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { fmtNumber } from "@/lib/format";
import { fmtClock, useAutoRefresh } from "@/lib/live";

interface LiveItem {
  key: string;
  label: string;
  value: number | null;
  change: number | null;
  change_kind: "pct" | "pp";
  format: "price" | "pct" | "index" | "ars";
  time: string | null;
  source: string;
  error: string | null;
  stale: boolean;
}

function fmtValue(i: LiveItem): string {
  if (i.value === null) return "—";
  switch (i.format) {
    case "pct":
      return `${fmtNumber(i.value, 2)} %`;
    case "ars":
      return `$ ${fmtNumber(i.value, 0)}`;
    case "price":
      return fmtNumber(i.value, i.value >= 1000 ? 0 : 2);
    default:
      return fmtNumber(i.value, i.value >= 1000 ? 0 : 2);
  }
}

function fmtChange(i: LiveItem): string | null {
  if (i.change === null) return null;
  const sign = i.change > 0 ? "+" : i.change < 0 ? "−" : "";
  if (i.change_kind === "pp") return `${sign}${fmtNumber(Math.abs(i.change), 2)} p.p.`;
  return `${sign}${fmtNumber(Math.abs(i.change) * 100, 2)} %`;
}

function tooltip(i: LiveItem): string {
  const when = i.time
    ? new Date(i.time).toLocaleString("es-AR", { timeZone: "America/Argentina/Buenos_Aires" })
    : "sin hora";
  const parts = [`${i.source} · dato de ${when}`];
  if (i.stale) parts.push("la fuente no actualiza este dato hace días");
  if (i.error) parts.push(i.error);
  return parts.join("\n");
}

/** Cinta de precios clave, actualizada cada 30 segundos mientras la pestaña está visible. */
export function LiveStrip() {
  const [items, setItems] = useState<LiveItem[]>([]);
  const [offline, setOffline] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api<{ items: LiveItem[] }>("/api/live/strip");
      setItems(r.items);
      setOffline(false);
    } catch {
      setOffline(true); // se conservan los últimos valores
    }
  }, []);
  const updated = useAutoRefresh(load, 30_000);

  return (
    <div className="live-strip" aria-label="Precios en vivo">
      <div className="live-strip-inner">
        <span className={`live-dot ${offline ? "off" : ""}`} title={offline ? "sin conexión" : "en vivo"}>
          ● {offline ? "sin conexión" : fmtClock(updated)}
        </span>
        {items.map((i) => {
          const chg = fmtChange(i);
          return (
            <span key={i.key} className={`live-item ${i.stale || i.value === null ? "live-stale" : ""}`} title={tooltip(i)}>
              <span className="live-label">{i.label}</span> <b className="tabular">{fmtValue(i)}</b>
              {chg && !i.stale && (
                <span className={`tabular ${i.change! >= 0 ? "pos" : "neg"}`}> {chg}</span>
              )}
              {i.stale && <span className="faint"> (viejo)</span>}
            </span>
          );
        })}
      </div>
    </div>
  );
}
