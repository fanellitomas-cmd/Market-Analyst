import type { Metric } from "./types";

const LOCALE = "es-AR";
export const NO_DATA = "—";

/** Valor ya expresado en % (ej. 2.5 → "2,5 %"), como los indicadores macro. */
export function fmtPercentValue(v: number | null, digits = 1): string {
  if (v === null) return NO_DATA;
  return `${v.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits })} %`;
}

/** Fracción (ej. 0.123 → "12,3 %"), como los rendimientos calculados. */
export function fmtFraction(v: number | null, digits = 1, signed = false): string {
  if (v === null) return NO_DATA;
  const s = fmtPercentValue(v * 100, digits);
  return signed && v > 0 ? `+${s}` : s;
}

export function fmtNumber(v: number | null, digits = 2): string {
  if (v === null) return NO_DATA;
  return v.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtUsdCompact(v: number | null): string {
  if (v === null) return NO_DATA;
  // Escala en castellano (billón = 10^12), no la notación compacta abreviada del navegador.
  const abs = Math.abs(v);
  const scales: [number, string][] = [
    [1e12, "billones"],
    [1e9, "mil millones"],
    [1e6, "millones"],
  ];
  for (const [size, word] of scales) {
    if (abs >= size) return `US$ ${fmtNumber(v / size, 1)} ${word}`;
  }
  return `US$ ${fmtNumber(v, 0)}`;
}

/** Formatea según la unidad del indicador macro. */
export function fmtByUnit(v: number | null, unit: string): string {
  if (unit.startsWith("%")) return fmtPercentValue(v);
  if (unit.startsWith("USD")) return fmtUsdCompact(v);
  return fmtNumber(v);
}

export function metricText(m: Metric, kind: "fraction" | "number", signed = false): string {
  if (m.status !== "OK" || m.value === null) return NO_DATA;
  return kind === "fraction" ? fmtFraction(m.value, 1, signed) : fmtNumber(m.value);
}

export function fmtDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(LOCALE, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString(LOCALE, { dateStyle: "short", timeStyle: "short" });
}

// Paleta categórica validada (orden fijo; el color sigue a la entidad, no al ranking).
export const SERIES_COLORS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
];

/** Etiqueta corta para ejes de tiempo: "nov 25". */
export function fmtMonthYear(iso: string): string {
  const [y, m] = iso.slice(0, 10).split("-").map(Number);
  const month = new Date(Date.UTC(y, m - 1, 1))
    .toLocaleDateString(LOCALE, { month: "short", timeZone: "UTC" })
    .replace(".", "");
  return `${month} ${String(y).slice(2)}`;
}

/** Un tick por mes (el primer día con dato de cada mes), raleado a `max` como mucho. */
export function monthTicks(dates: string[], max = 8): string[] {
  const firsts: string[] = [];
  let last = "";
  for (const d of dates) {
    const ym = d.slice(0, 7);
    if (ym !== last) {
      firsts.push(d);
      last = ym;
    }
  }
  const step = Math.ceil(firsts.length / max);
  return firsts.filter((_, i) => i % step === 0);
}
