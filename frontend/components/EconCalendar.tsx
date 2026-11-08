"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNotice, Skeleton } from "@/components/Common";
import { api, ApiError } from "@/lib/api";
import { fmtArDateTime, fmtNumber, NO_DATA } from "@/lib/format";
import type { CalendarResponse, EconEventOut } from "@/lib/types";

const COUNTRY: Record<string, string> = {
  USD: "EE.UU.",
  EUR: "Zona Euro",
  GBP: "Reino Unido",
  JPY: "Japón",
  CNY: "China",
  CAD: "Canadá",
  AUD: "Australia",
  NZD: "N. Zelanda",
  CHF: "Suiza",
};

function refMonth(iso: string | null): string {
  if (!iso) return "";
  const [y, m] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("es-AR", {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

function fmtActual(e: EconEventOut): string {
  if (e.actual === null) return NO_DATA;
  const digits = e.actual_unit === "mil" ? 0 : 1;
  return `${fmtNumber(e.actual, digits)}${e.actual_unit === "mil" ? " mil" : " %"}`;
}

/** Pronóstico con coma decimal si el evento tiene seguimiento; si no, como lo publica la fuente. */
function fmtForecast(e: EconEventOut): string {
  if (e.forecast_value !== null && e.tracked) {
    const digits = e.actual_unit === "mil" ? 0 : 1;
    return `${fmtNumber(e.forecast_value, digits)}${e.actual_unit === "mil" ? " mil" : " %"}`;
  }
  return e.forecast ? decimalComma(e.forecast) : NO_DATA;
}

/** "0.3%" → "0,3%" (la fuente publica con punto decimal). */
function decimalComma(raw: string): string {
  return raw.replace(/(\d)\.(\d)/g, "$1,$2");
}

function fmtSurprise(e: EconEventOut): string {
  if (e.surprise === null) return NO_DATA;
  const digits = e.actual_unit === "mil" ? 0 : 1;
  const sign = e.surprise > 0 ? "+" : e.surprise < 0 ? "−" : "";
  return `${sign}${fmtNumber(Math.abs(e.surprise), digits)}${e.actual_unit === "mil" ? " mil" : " p.p."}`;
}

export function EconCalendar() {
  const [data, setData] = useState<CalendarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<CalendarResponse>("/api/calendar")
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Error inesperado."));
  }, []);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Calendario y sorpresas</h2>
        <span className="faint">horarios de Buenos Aires · pronósticos de ForexFactory</span>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        La sorpresa es el dato real menos el pronóstico del mercado. El agente toma el dato real de
        FRED apenas se publica y te avisa por WhatsApp si el desvío supera el umbral de cada
        indicador.
      </p>
      {error && <ErrorNotice message={error} />}
      {!data && !error && <Skeleton height={200} />}
      {data && data.upcoming.length === 0 && data.recent.length === 0 && (
        <div className="notice warn">
          El agente todavía no cargó el calendario. Se actualiza todos los días a las 06:30; también
          podés ejecutar la tarea “Calendario económico” desde <Link href="/sistema">Sistema</Link>.
        </div>
      )}
      {data && data.upcoming.length > 0 && (
        <>
          <h3>Próximos 8 días</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Cuándo</th>
                  <th scope="col" className="left">País</th>
                  <th scope="col" className="left">Dato</th>
                  <th scope="col" className="left">Impacto</th>
                  <th scope="col">Pronóstico</th>
                  <th scope="col">Anterior</th>
                </tr>
              </thead>
              <tbody>
                {data.upcoming.map((e) => (
                  <tr key={e.id}>
                    <td>{fmtArDateTime(e.release_at)}</td>
                    <td className="left">{COUNTRY[e.country] ?? e.country}</td>
                    <td className="left" style={{ whiteSpace: "normal", minWidth: 180 }}>
                      {e.name}
                      {e.tracked && <span className="sub">con seguimiento de sorpresa</span>}
                    </td>
                    <td className="left">{e.impact === "High" ? "Alto" : e.impact === "Medium" ? "Medio" : e.impact}</td>
                    <td>{fmtForecast(e)}</td>
                    <td>{e.previous ? decimalComma(e.previous) : NO_DATA}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {data && data.recent.length > 0 && (
        <>
          <h3>Publicados (últimos 60 días)</h3>
          <div className="table-wrap">
            <table className="tabular">
              <thead>
                <tr>
                  <th scope="col">Dato</th>
                  <th scope="col">Publicado</th>
                  <th scope="col">Real</th>
                  <th scope="col">Pronóstico</th>
                  <th scope="col">Sorpresa</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map((e) => (
                  <tr key={e.id}>
                    <td>
                      {e.name}
                      <span className="sub">{refMonth(e.reference_month)}</span>
                    </td>
                    <td>{fmtArDateTime(e.release_at)}</td>
                    <td>{e.actual === null ? <span className="muted">pendiente</span> : fmtActual(e)}</td>
                    <td>{fmtForecast(e)}</td>
                    <td style={{ whiteSpace: "normal", minWidth: 160 }}>
                      {fmtSurprise(e)}
                      {e.surprise_note && <span className="sub">{e.surprise_note}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="faint" style={{ margin: 0 }}>
            El dato real se calcula desde la serie oficial en FRED y puede diferir en ±0,1 del redondeo
            que publica el organismo.
          </p>
        </>
      )}
    </section>
  );
}
