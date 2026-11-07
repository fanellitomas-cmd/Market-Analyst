"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorNotice, Skeleton } from "@/components/Common";
import { api, ApiError } from "@/lib/api";
import { fmtArDateTime, fmtDate, fmtFraction, fmtNumber, NO_DATA } from "@/lib/format";
import type { JobRunOut, LedgerReport, PremarketReport, SectorRow, TodayResponse } from "@/lib/types";

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Error inesperado.";
}

function Chg({ v }: { v: number | null }) {
  return <span className={v === null ? "muted" : v >= 0 ? "pos" : "neg"}>{fmtFraction(v, 1, true)}</span>;
}

function num(v: number | null, d = 2) {
  return v === null ? NO_DATA : fmtNumber(v, d);
}

/** Fondo del mapa de calor: verde o rojo con intensidad según el movimiento (tope ±3 %). */
function heat(v: number | null): React.CSSProperties {
  if (v === null) return { background: "var(--surface-2)" };
  const pct = Math.min(Math.abs(v) / 0.03, 1) * 45 + 8;
  const color = v >= 0 ? "var(--good)" : "var(--bad)";
  return { background: `color-mix(in srgb, ${color} ${pct.toFixed(0)}%, var(--surface))` };
}

function Premarket({ r }: { r: PremarketReport }) {
  const groups = [...new Set(r.markets.map((m) => m.group))];
  return (
    <>
      <div className="grid-3">
        {groups.map((g) => (
          <div className="tile" key={g}>
            <div className="tile-label">{g}</div>
            <dl className="kv">
              {r.markets
                .filter((m) => m.group === g)
                .map((m) => (
                  <div key={m.symbol} style={{ display: "contents" }}>
                    <dt title={m.error ?? undefined}>{m.name}</dt>
                    <dd>
                      <Chg v={m.change} />
                    </dd>
                  </div>
                ))}
            </dl>
          </div>
        ))}
        {r.crypto_funding.length > 0 && (
          <div className="tile">
            <div className="tile-label">Financiamiento cripto (anualizado)</div>
            <dl className="kv">
              {r.crypto_funding.map((c) => (
                <div key={c.coin} style={{ display: "contents" }}>
                  <dt title={c.reading ?? undefined}>{c.coin}</dt>
                  <dd>
                    <Chg v={c.annualized} />
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}
      </div>

      <div className="grid-2">
        <div>
          <h3>Datos de hoy</h3>
          {r.events_today.length === 0 ? (
            <p className="muted">Sin datos de impacto alto o medio cargados para hoy.</p>
          ) : (
            <ul className="plain-list">
              {r.events_today.map((e) => (
                <li key={e.id}>
                  <b>{new Date(e.release_at).toLocaleTimeString("es-AR", { timeZone: "America/Argentina/Buenos_Aires", hour: "2-digit", minute: "2-digit" })}</b>{" "}
                  {e.name} <span className="faint">({e.country}{e.forecast ? ` · pronóstico ${e.forecast}` : ""})</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3>Resultados publicados</h3>
          {r.earnings_overnight.length === 0 ? (
            <p className="muted">{r.earnings_note}</p>
          ) : (
            <ul className="plain-list">
              {r.earnings_overnight.map((f) => (
                <li key={`${f.ticker}-${f.time}`}>
                  <b>{f.ticker}</b> {f.company}{" "}
                  {f.link && (
                    <a href={f.link} target="_blank" rel="noreferrer">
                      8-K
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
          <p className="faint" style={{ margin: 0 }}>
            Sin sorpresa frente al consenso: no hay una fuente gratuita de estimados.
          </p>
        </div>
      </div>

      <h3>Matriz de niveles</h3>
      <div className="table-wrap">
        <table className="tabular">
          <thead>
            <tr>
              <th scope="col">Activo</th>
              <th scope="col">Cierre</th>
              <th scope="col">AVWAP balance</th>
              <th scope="col">AVWAP máx./mín. 52 s</th>
              <th scope="col">Área de valor (POC)</th>
              <th scope="col">Zero gamma</th>
              <th scope="col">Mov. implícito</th>
            </tr>
          </thead>
          <tbody>
            {r.levels.map((l) => (
              <tr key={l.ticker}>
                <td title={l.errors.join("\n") || undefined}>
                  <Link href={`/tecnico`}>{l.ticker}</Link>
                  <span className="sub">{l.regime ?? ""}</span>
                </td>
                <td>{num(l.close)}</td>
                <td>{num(l.avwap_earnings)}</td>
                <td>
                  {num(l.avwap_high)} / {num(l.avwap_low)}
                </td>
                <td>
                  {num(l.val)} – {num(l.vah)}
                  <span className="sub">POC {num(l.poc)}</span>
                </td>
                <td>{num(l.zero_gamma)}</td>
                <td>
                  {l.implied_move === null ? NO_DATA : `±${fmtFraction(l.implied_move, 1)}`}
                  <span className="sub">{l.implied_expiry ? `al ${fmtDate(l.implied_expiry)}` : ""}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SectorTile({ s }: { s: SectorRow }) {
  const emas = [s.above_ema20, s.above_ema50, s.above_ema200];
  return (
    <div className="heat-tile" style={heat(s.change_1d)}>
      <div className="heat-name">
        {s.name} <span className="faint">{s.symbol}</span>
      </div>
      <div className="heat-value">{fmtFraction(s.change_1d, 1, true)}</div>
      <div className="heat-sub">
        semana {fmtFraction(s.change_1w, 1, true)} · mes {fmtFraction(s.change_1m, 1, true)}
      </div>
      <div className="heat-sub">
        vs SPY mes {fmtFraction(s.vs_spy_1m, 1, true)} · EMAs{" "}
        {emas.map((e, i) => (
          <span key={i} title={`EMA ${[20, 50, 200][i]}`}>
            {e === null ? "·" : e ? "▲" : "▼"}
          </span>
        ))}
      </div>
    </div>
  );
}

function Ledger({ r }: { r: LedgerReport }) {
  return (
    <>
      <div className="tiles">
        {r.benchmarks.map((b) => (
          <div className="tile" key={b.symbol}>
            <div className="tile-label">{b.name}</div>
            <div className="tile-value">
              <Chg v={b.change_1d} />
            </div>
            <div className="tile-sub">
              EMA 20/50/200: {[b.above_ema20, b.above_ema50, b.above_ema200].map((e) => (e === null ? "·" : e ? "arriba" : "abajo")).join(" / ")}
            </div>
          </div>
        ))}
      </div>
      <h3>Mapa de calor sectorial</h3>
      <div className="heat-grid">
        {r.sectors.map((s) => (
          <SectorTile key={s.symbol} s={s} />
        ))}
      </div>
      {r.rotation && <p style={{ margin: 0 }}>{r.rotation}</p>}
      {r.invalidated_today.length > 0 && (
        <div className="notice error">
          <b>Kill switch:</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {r.invalidated_today.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="faint" style={{ margin: 0 }}>
        ▲ / ▼ = cierre arriba o abajo de la EMA de 20, 50 y 200 ruedas. Tesis:{" "}
        {r.theses.filter((t) => t.status === "activa").length} activas. <Link href="/tesis">Ver tesis →</Link>
      </p>
    </>
  );
}

export default function HoyPage() {
  const [data, setData] = useState<TodayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    api<TodayResponse>("/api/cadence/today").then(setData).catch((e) => setError(errMsg(e)));
  }, []);
  useEffect(load, [load]);

  async function run(job: string) {
    setBusy(job);
    setMessage(null);
    try {
      const r = await api<JobRunOut>(`/api/system/jobs/${job}/run`, { method: "POST" });
      setMessage(r.detail ?? "Listo.");
      load();
    } catch (e) {
      setMessage(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>Hoy</h1>
        <p className="muted" style={{ margin: 0 }}>
          La cadencia del agente en hora de Buenos Aires: pre-market a las 07:30, alertas sólo cuando
          pasa algo y cierre a las 18:20. Lista de seguimiento:{" "}
          {data ? data.watchlist.join(", ") || "vacía" : "…"} (tesis activas + lista de opciones).
        </p>
      </div>
      {error && <ErrorNotice message={error} />}
      {message && <div className="notice warn">{message}</div>}
      {!data && !error && <Skeleton height={400} />}

      {data && (
        <>
          <section className="card">
            <div className="card-head">
              <h2>Pre-market</h2>
              <div className="controls">
                <span className="faint">
                  {data.premarket ? `generado ${fmtArDateTime(data.premarket.generated_at)}` : "todavía no se generó"}
                </span>
                <button onClick={() => run("premarket")} disabled={busy !== null}>
                  {busy === "premarket" ? "Generando…" : "Generar ahora"}
                </button>
              </div>
            </div>
            {data.premarket ? <Premarket r={data.premarket} /> : <p className="muted">Se genera los días hábiles a las 07:30.</p>}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Alertas de asimetría</h2>
              <button onClick={() => run("alertas_intradia")} disabled={busy !== null}>
                {busy === "alertas_intradia" ? "Revisando…" : "Revisar ahora"}
              </button>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Volumen más de 2,5σ arriba de lo normal a esa hora rompiendo el máximo o mínimo de ayer (o de
              52 semanas), o un 8-K relevante. Cada alerta se manda una sola vez por día.
            </p>
            {data.triggers.length === 0 ? (
              <p className="muted">Sin alertas en los últimos 5 días.</p>
            ) : (
              <div className="grid-2">
                {data.triggers.map((t, i) => (
                  <div className="tile" key={i}>
                    <div className="card-head">
                      <b>{t.ticker}</b>
                      <span className="faint">
                        {t.kind} · {fmtArDateTime(t.created_at)}
                      </span>
                    </div>
                    <dl className="kv kv-text">
                      <dt>Hecho</dt>
                      <dd>{t.fact}</dd>
                      <dt>Causa</dt>
                      <dd>{t.cause.startsWith("http") ? <a href={t.cause} target="_blank" rel="noreferrer">ver 8-K</a> : t.cause}</dd>
                      <dt>Stop técnico</dt>
                      <dd>{t.stop}</dd>
                    </dl>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Cierre de rueda</h2>
              <div className="controls">
                <span className="faint">
                  {data.ledger
                    ? `rueda del ${data.ledger.session_date ? fmtDate(data.ledger.session_date) : "—"} · generado ${fmtArDateTime(data.ledger.generated_at)}`
                    : "todavía no se generó"}
                </span>
                <button onClick={() => run("cierre")} disabled={busy !== null}>
                  {busy === "cierre" ? "Generando…" : "Generar ahora"}
                </button>
              </div>
            </div>
            {data.ledger ? <Ledger r={data.ledger} /> : <p className="muted">Se genera los días hábiles a las 18:20.</p>}
          </section>
        </>
      )}
    </>
  );
}
