"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { api, ApiError } from "@/lib/api";
import { fmtArDateTime, fmtDate, fmtFraction, fmtNumber, fmtUsdCompact, NO_DATA } from "@/lib/format";
import type { OptionsResponse } from "@/lib/types";

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Error inesperado.";
}

function fmtGex(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (abs >= 1e9) return `${sign}US$ ${fmtNumber(abs / 1e9, 2)} mil M`;
  if (abs >= 1e6) return `${sign}US$ ${fmtNumber(abs / 1e6, 1)} M`;
  return `${sign}US$ ${fmtNumber(abs, 0)}`;
}

function GexChart({ data }: { data: OptionsResponse }) {
  const rows = data.by_strike.map((s) => ({ strike: s.strike, calls: s.calls, puts: s.puts }));
  return (
    <>
      <div className="legend" aria-hidden="true">
        <span>
          <i className="swatch" style={{ background: "var(--series-1)" }} />
          Calls (+)
        </span>
        <span>
          <i className="swatch" style={{ background: "var(--series-2)" }} />
          Puts (−)
        </span>
        <span>— precio actual · - - zero gamma</span>
      </div>
      <div className="chart" role="img" aria-label={`GEX por strike de ${data.symbol}`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} stackOffset="sign" margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="strike"
              type="number"
              domain={["dataMin", "dataMax"]}
              tick={{ fill: "var(--text-3)", fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
              tickFormatter={(v: number) => fmtNumber(v, 0)}
            />
            <YAxis
              tickFormatter={(v: number) => fmtGex(v).replace("US$ ", "")}
              tick={{ fill: "var(--text-3)", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              width={72}
            />
            <ReferenceLine y={0} stroke="var(--text-3)" />
            <ReferenceLine x={data.spot} stroke="var(--text)" strokeWidth={1.5} />
            {data.zero_gamma !== null && (
              <ReferenceLine x={data.zero_gamma} stroke="var(--text-2)" strokeDasharray="4 3" />
            )}
            <Tooltip
              cursor={{ fill: "var(--surface-2)" }}
              content={({ active, payload }) => {
                const p = payload?.[0]?.payload as (typeof rows)[number] | undefined;
                if (!active || !p) return null;
                return (
                  <div className="tooltip">
                    <div className="tooltip-title">Strike {fmtNumber(p.strike, 2)}</div>
                    <div className="tooltip-row">
                      <i className="swatch" style={{ background: "var(--series-1)" }} />
                      Calls <b>{fmtGex(p.calls)}</b>
                    </div>
                    <div className="tooltip-row">
                      <i className="swatch" style={{ background: "var(--series-2)" }} />
                      Puts <b>{fmtGex(p.puts)}</b>
                    </div>
                  </div>
                );
              }}
            />
            <Bar dataKey="calls" stackId="g" fill="var(--series-1)" isAnimationActive={false} />
            <Bar dataKey="puts" stackId="g" fill="var(--series-2)" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

export default function OpcionesPage() {
  const [symbol, setSymbol] = useState("SPY");
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [watchText, setWatchText] = useState("");
  const [data, setData] = useState<OptionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async (sym: string) => {
    if (!sym.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api<OptionsResponse>(`/api/options/${encodeURIComponent(sym.trim())}`));
    } catch (e) {
      setData(null);
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    api<string[]>("/api/options/watchlist")
      .then((w) => {
        setWatchlist(w);
        setWatchText(w.join(", "));
      })
      .catch(() => undefined);
    void load("SPY");
  }, [load]);

  async function saveWatchlist() {
    setMessage(null);
    try {
      const w = await api<string[]>("/api/options/watchlist", {
        method: "PUT",
        body: JSON.stringify({ symbols: watchText.split(/[\s,;]+/).filter(Boolean) }),
      });
      setWatchlist(w);
      setWatchText(w.join(", "));
      setMessage("Lista guardada. El agente toma la foto diaria a las 18:30.");
    } catch (e) {
      setMessage(errMsg(e));
    }
  }

  const distance = data && data.zero_gamma !== null ? data.spot / data.zero_gamma - 1 : null;

  return (
    <>
      <div className="page-head">
        <h1>Opciones</h1>
        <p className="muted" style={{ margin: 0 }}>
          Cómo están posicionados los market makers (GEX) y cuánto movimiento descuenta el mercado de
          opciones. Cadenas demoradas de CBOE.
        </p>
      </div>

      <section className="card">
        <form
          className="controls"
          onSubmit={(e) => {
            e.preventDefault();
            void load(symbol);
          }}
        >
          <label className="field">
            Símbolo
            <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} style={{ width: 140 }} />
          </label>
          <button className="primary" type="submit" disabled={loading}>
            {loading ? "Calculando…" : "Analizar"}
          </button>
        </form>
        <div className="chips">
          {watchlist.map((w) => (
            <button
              key={w}
              className="chip"
              aria-pressed={data?.symbol === w}
              onClick={() => {
                setSymbol(w);
                void load(w);
              }}
            >
              {w}
            </button>
          ))}
        </div>
      </section>

      {error && <ErrorNotice message={error} />}
      {loading && !data && <Skeleton height={400} />}

      {data && (
        <>
          <section className="card" aria-busy={loading}>
            <div className="card-head">
              <h2>{data.symbol}</h2>
              <span className="regime-badge">{data.total_gex >= 0 ? "Gamma positiva" : "Gamma negativa"}</span>
            </div>
            <p className="muted" style={{ margin: 0 }}>
              {data.regime}.
            </p>
            <div className="tiles">
              <div className="tile">
                <div className="tile-label">Precio</div>
                <div className="tile-value">{fmtNumber(data.spot, 2)}</div>
                <div className="tile-sub">{data.as_of ? fmtArDateTime(data.as_of) : ""}</div>
              </div>
              <div className="tile">
                <div className="tile-label">GEX total</div>
                <div className={`tile-value ${data.total_gex >= 0 ? "pos" : "neg"}`}>{fmtGex(data.total_gex)}</div>
                <div className="tile-sub">por cada 1 % de movimiento</div>
              </div>
              <div className="tile">
                <div className="tile-label">Zero gamma</div>
                <div className="tile-value">{data.zero_gamma === null ? NO_DATA : fmtNumber(data.zero_gamma, 2)}</div>
                <div className="tile-sub">
                  {distance === null ? "sin cambio de signo cerca" : `precio ${fmtFraction(distance, 1, true)} del nivel`}
                </div>
              </div>
              <div className="tile">
                <div className="tile-label">Muro de calls / puts</div>
                <div className="tile-value" style={{ fontSize: "1.1rem" }}>
                  {data.call_wall === null ? NO_DATA : fmtNumber(data.call_wall, 0)} /{" "}
                  {data.put_wall === null ? NO_DATA : fmtNumber(data.put_wall, 0)}
                </div>
                <div className="tile-sub">resistencia / soporte por gamma</div>
              </div>
              <div className="tile">
                <div className="tile-label">Put / call (interés abierto)</div>
                <div className="tile-value">{data.put_call_oi === null ? NO_DATA : fmtNumber(data.put_call_oi, 2)}</div>
                <div className="tile-sub">
                  {fmtNumber(data.put_oi, 0)} puts · {fmtNumber(data.call_oi, 0)} calls
                </div>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>GEX por strike</h2>
              <span className="faint">strikes a ±15 % del precio · todos los vencimientos</span>
            </div>
            {data.by_strike.length > 0 ? <GexChart data={data} /> : <p className="muted">Sin contratos cerca del precio.</p>}
            <p className="faint" style={{ margin: 0 }}>
              Supuesto estándar: los market makers están comprados en calls y vendidos en puts. Gamma de
              Black-Scholes con la volatilidad implícita de cada contrato.
              {data.excluded_contracts
                ? ` ${data.excluded_contracts} contratos sin volatilidad implícita quedaron afuera.`
                : ""}
            </p>
          </section>

          <div className="grid-2">
            <section className="card">
              <h2>Movimiento implícito</h2>
              <p className="muted" style={{ margin: 0 }}>
                Lo que descuenta el mercado hasta cada vencimiento: precio del straddle ATM (call + put)
                sobre el precio.
              </p>
              {data.implied_moves.length === 0 ? (
                <p className="muted">No hay straddles con precio de compra y venta.</p>
              ) : (
                <div className="table-wrap">
                  <table className="tabular">
                    <thead>
                      <tr>
                        <th scope="col">Vencimiento</th>
                        <th scope="col">Días</th>
                        <th scope="col">Movimiento</th>
                        <th scope="col">Rango</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.implied_moves.map((m) => (
                        <tr key={m.expiry}>
                          <td>{fmtDate(m.expiry)}</td>
                          <td>{m.days}</td>
                          <td>±{fmtFraction(m.move, 1)}</td>
                          <td>
                            {fmtNumber(m.low, 2)} – {fmtNumber(m.high, 2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="card">
              <h2>Interés abierto día a día</h2>
              {data.oi_reading ? (
                <div className="notice warn">
                  Desde el {fmtDate(data.oi_reading.since)}: precio {fmtFraction(data.oi_reading.price_change, 1, true)},
                  calls {fmtFraction(data.oi_reading.call_oi_change, 1, true)}, puts{" "}
                  {fmtFraction(data.oi_reading.put_oi_change, 1, true)}. <b>{data.oi_reading.reading}</b>.
                </div>
              ) : (
                <p className="muted">
                  Hacen falta dos fotos diarias. El agente las toma a las 18:30 para los símbolos de la lista.
                </p>
              )}
              {data.history.length > 0 && (
                <div className="table-wrap">
                  <table className="tabular">
                    <thead>
                      <tr>
                        <th scope="col">Fecha</th>
                        <th scope="col">Precio</th>
                        <th scope="col">Calls</th>
                        <th scope="col">Puts</th>
                        <th scope="col">GEX</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...data.history].reverse().slice(0, 10).map((h) => (
                        <tr key={h.date}>
                          <td>{fmtDate(h.date)}</td>
                          <td>{fmtNumber(h.spot, 2)}</td>
                          <td>{fmtNumber(h.call_oi, 0)}</td>
                          <td>{fmtNumber(h.put_oi, 0)}</td>
                          <td className={h.total_gex >= 0 ? "pos" : "neg"}>{fmtUsdCompact(h.total_gex)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
          <SourceLine source={data.source} extra="datos con unos 15 minutos de demora" />
        </>
      )}

      <section className="card">
        <h2>Lista para la foto diaria</h2>
        <p className="muted" style={{ margin: 0 }}>
          Símbolos que el agente guarda cada día hábil a las 18:30 (hasta 30). Te avisa por WhatsApp sólo si
          alguno cambia de gamma positiva a negativa o al revés. Para índices usá ^SPX o ^NDX.
        </p>
        <input value={watchText} onChange={(e) => setWatchText(e.target.value.toUpperCase())} aria-label="Lista de símbolos" />
        <div className="controls">
          <button onClick={saveWatchlist}>Guardar lista</button>
        </div>
        {message && <div className="notice warn">{message}</div>}
      </section>
    </>
  );
}
