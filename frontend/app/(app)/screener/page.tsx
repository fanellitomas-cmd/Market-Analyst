"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorNotice, Skeleton } from "@/components/Common";
import { api, ApiError } from "@/lib/api";
import { fmtArDateTime, NO_DATA } from "@/lib/format";
import type { JobRunOut, ScreenerResponse, ScreenerRow } from "@/lib/types";

const MARK = { cumple: "✓", "no cumple": "✗", "sin dato": "—" } as const;

function CriterionCell({ row, index }: { row: ScreenerRow; index: number }) {
  const c = row.criteria[index];
  if (!c) return <td className="muted">{NO_DATA}</td>;
  return (
    <td title={c.detail}>
      <span className={c.status === "cumple" ? "pos" : c.status === "no cumple" ? "neg" : "muted"}>
        {MARK[c.status]}
      </span>{" "}
      <span className="sub" style={{ display: "inline" }}>
        {c.detail}
      </span>
    </td>
  );
}

export default function ScreenerPage() {
  const [data, setData] = useState<ScreenerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [universe, setUniverse] = useState("");
  const [busy, setBusy] = useState<"save" | "run" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ScreenerResponse>("/api/screener")
      .then((d) => {
        setData(d);
        setUniverse(d.universe.join(", "));
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Error inesperado."));
  }, []);

  useEffect(load, [load]);

  async function save() {
    setBusy("save");
    setMessage(null);
    try {
      const saved = await api<string[]>("/api/screener/universe", {
        method: "PUT",
        body: JSON.stringify({ tickers: universe.split(/[\s,;]+/).filter(Boolean) }),
      });
      setUniverse(saved.join(", "));
      setMessage(`Universo guardado: ${saved.length} empresas. Se analiza en la próxima corrida.`);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Error inesperado.");
    } finally {
      setBusy(null);
    }
  }

  async function runNow() {
    setBusy("run");
    setMessage("Analizando el universo… puede tardar varios minutos (la SEC limita la velocidad).");
    try {
      const run = await api<JobRunOut>("/api/system/jobs/screener_garp/run", { method: "POST" });
      setMessage(run.detail ?? "Listo.");
      load();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Error inesperado.");
    } finally {
      setBusy(null);
    }
  }

  const passing = data?.rows.filter((r) => r.passes) ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Screener GARP</h1>
        <p className="muted" style={{ margin: 0 }}>
          Crecimiento a precio razonable: P/E menor a 20, ingresos creciendo más de 20 % anual en 5 años,
          margen operativo en expansión y ROIC mayor al WACC. El agente lo corre los días hábiles a las
          21:00 y te avisa por WhatsApp sólo cuando una empresa entra al filtro.
        </p>
      </div>

      {error && <ErrorNotice message={error} />}
      {!data && !error && <Skeleton height={300} />}

      {data && (
        <>
          <section className="card">
            <div className="card-head">
              <h2>Resultados</h2>
              <span className="faint">
                {data.run_at ? `última corrida: ${fmtArDateTime(data.run_at)}` : "todavía no corrió"}
              </span>
            </div>
            {data.rows.length === 0 ? (
              <p className="muted">
                Todavía no hay resultados. Corré el screener ahora o esperá la corrida de las 21:00.
              </p>
            ) : (
              <>
                <p style={{ margin: 0 }}>
                  {passing.length > 0 ? (
                    <>
                      <b>Cumplen los 4 criterios:</b> {passing.map((r) => r.ticker).join(", ")}
                    </>
                  ) : (
                    <span className="muted">Ninguna empresa cumple los 4 criterios en esta corrida.</span>
                  )}
                </p>
                <div className="table-wrap">
                  <table className="tabular">
                    <thead>
                      <tr>
                        <th scope="col">Empresa</th>
                        <th scope="col">Cumple</th>
                        <th scope="col">P/E &lt; 20</th>
                        <th scope="col">Ingresos +20 %/año</th>
                        <th scope="col">Margen en expansión</th>
                        <th scope="col">ROIC &gt; WACC</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.rows.map((r) => (
                        <tr key={r.ticker}>
                          <td>
                            <Link href={`/empresas?ticker=${r.ticker}&vista=forense`}>{r.ticker}</Link>
                            <span className="sub">{r.error ?? r.name}</span>
                          </td>
                          <td>
                            <b className={r.passes ? "pos" : undefined}>{r.error ? NO_DATA : `${r.met}/4`}</b>
                          </td>
                          {[0, 1, 2, 3].map((i) => (
                            <CriterionCell key={i} row={r} index={i} />
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="faint" style={{ margin: 0 }}>
                  ✓ cumple · ✗ no cumple · — sin dato (pasá el mouse para ver el motivo). Los datos y supuestos
                  de cada empresa están en Empresas → Forense y valuación.
                </p>
              </>
            )}
          </section>

          <section className="card">
            <h2>Universo</h2>
            <p className="muted" style={{ margin: 0 }}>
              Empresas que se analizan en cada corrida (máximo 150). Sólo sirven las que presentan balances
              ante la SEC; los ADRs con formulario 20-F no tienen estos datos.
            </p>
            <textarea
              value={universe}
              onChange={(e) => setUniverse(e.target.value.toUpperCase())}
              rows={4}
              aria-label="Tickers del universo, separados por coma"
              style={{ width: "100%", fontFamily: "var(--mono)", fontSize: "0.85rem" }}
            />
            <div className="controls">
              <button onClick={save} disabled={busy !== null}>
                {busy === "save" ? "Guardando…" : "Guardar universo"}
              </button>
              <button className="primary" onClick={runNow} disabled={busy !== null}>
                {busy === "run" ? "Corriendo…" : "Correr ahora"}
              </button>
            </div>
            {message && <div className="notice warn">{message}</div>}
          </section>
        </>
      )}
    </>
  );
}
