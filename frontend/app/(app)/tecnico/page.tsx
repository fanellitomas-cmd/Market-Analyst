"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { AVWAP_COLORS, EMA_COLORS, TechnicalChart, VolumeProfile } from "@/components/TechnicalChart";
import { api, ApiError, qs } from "@/lib/api";
import { fmtDate, fmtFraction, fmtNumber, NO_DATA } from "@/lib/format";
import type { TechnicalResponse } from "@/lib/types";

const PRESETS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "MELI", "GGAL", "YPF", "BTC-USD"];

function price(v: number | null, currency: string | null): string {
  if (v === null) return NO_DATA;
  return `${currency === "USD" || !currency ? "US$ " : `${currency} `}${fmtNumber(v, 2)}`;
}

export default function TecnicoPage() {
  const [symbol, setSymbol] = useState("SPY");
  const [anchor, setAnchor] = useState("");
  const [data, setData] = useState<TechnicalResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (sym: string, anc: string) => {
    if (!sym.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setData(
        await api<TechnicalResponse>(
          `/api/technical/${encodeURIComponent(sym.trim())}?${qs({ anchor: anc || undefined })}`,
        ),
      );
    } catch (e) {
      setData(null);
      setError(e instanceof ApiError ? e.message : "Error inesperado.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load("SPY", "");
  }, [load]);

  const r = data?.regime;

  return (
    <>
      <div className="page-head">
        <h1>Análisis técnico</h1>
        <p className="muted" style={{ margin: 0 }}>
          Primero se clasifica el régimen (tendencia o rango) y recién después se leen niveles: AVWAPs
          anclados en eventos clave y el perfil de volumen de los últimos 60 días.
        </p>
      </div>

      <section className="card">
        <form
          className="controls"
          onSubmit={(e) => {
            e.preventDefault();
            void load(symbol, anchor);
          }}
        >
          <label className="field">
            Símbolo
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              maxLength={15}
              style={{ width: 140 }}
            />
          </label>
          <label className="field">
            Ancla extra para AVWAP (opcional)
            <input type="date" value={anchor} onChange={(e) => setAnchor(e.target.value)} />
          </label>
          <button className="primary" type="submit" disabled={loading}>
            {loading ? "Analizando…" : "Analizar"}
          </button>
        </form>
        <div className="chips">
          {PRESETS.map((p) => (
            <button
              key={p}
              className="chip"
              aria-pressed={data?.symbol === p}
              onClick={() => {
                setSymbol(p);
                void load(p, anchor);
              }}
            >
              {p}
            </button>
          ))}
        </div>
      </section>

      {error && <ErrorNotice message={error} />}
      {loading && !data && <Skeleton height={460} />}

      {data && r && (
        <>
          <div className="grid-2">
            <section className="card">
              <div className="card-head">
                <h2>
                  {data.symbol}
                  {data.name ? <span className="muted"> · {data.name}</span> : null}
                </h2>
                <span className="regime-badge">{r.label}</span>
              </div>
              <p className="muted" style={{ margin: 0 }}>
                {r.explanation}
              </p>
              {r.conditions.length > 0 && (
                <ul className="check-list">
                  {r.conditions.map((c) => (
                    <li key={c.name}>
                      <span className={c.ok ? "pos" : "muted"}>{c.ok ? "✓" : "✗"}</span> {c.name}{" "}
                      <span className="faint">({c.detail})</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="card">
              <h2>Precio y volumen</h2>
              <div className="tiles">
                <div className="tile">
                  <div className="tile-label">Cierre</div>
                  <div className="tile-value">{price(data.close, data.currency)}</div>
                  <div className="tile-sub">{fmtDate(data.as_of)}</div>
                </div>
                <div className="tile">
                  <div className="tile-label">Volumen relativo</div>
                  <div className="tile-value">
                    {data.volume.relative === null ? NO_DATA : `${fmtNumber(data.volume.relative, 2)}×`}
                  </div>
                  <div className="tile-sub">vs. promedio de 50 ruedas</div>
                </div>
                <div className="tile">
                  <div className="tile-label">Desvío del volumen</div>
                  <div className="tile-value">
                    {data.volume.zscore === null ? NO_DATA : `${fmtNumber(data.volume.zscore, 1)} σ`}
                  </div>
                  <div className="tile-sub">más de 2,5 σ = volumen anómalo</div>
                </div>
              </div>
              {data.notes.map((n) => (
                <p className="faint" key={n} style={{ margin: 0 }}>
                  {n}
                </p>
              ))}
            </section>
          </div>

          <section className="card">
            <div className="card-head">
              <h2>Gráfico diario</h2>
              <span className="faint">último año · líneas punteadas: POC, VAH y VAL de 60 días</span>
            </div>
            <div className="legend">
              {Object.entries(EMA_COLORS).map(([k, v]) => (
                <span key={k}>
                  <i className="swatch" style={{ background: `var(${v})` }} />
                  {k.toUpperCase().replace("EMA", "EMA ")}
                </span>
              ))}
              {data.avwaps
                .filter((a) => a.status === "OK")
                .map((a) => (
                  <span key={a.key}>
                    <i className="swatch" style={{ background: `var(${AVWAP_COLORS[a.key]})` }} />
                    AVWAP {a.label}
                  </span>
                ))}
            </div>
            <div className="chart-with-profile">
              <TechnicalChart data={data} />
              {data.profile ? (
                <div>
                  <div className="faint" style={{ fontSize: "0.8rem" }}>
                    Perfil 60 días (escala propia)
                  </div>
                  <VolumeProfile data={data.profile} />
                </div>
              ) : (
                <p className="faint">{data.profile_note}</p>
              )}
            </div>
          </section>

          <div className="grid-2">
            <section className="card">
              <h2>AVWAP anclados</h2>
              <p className="muted" style={{ margin: 0 }}>
                Precio promedio pagado desde cada evento, ponderado por volumen. Arriba del AVWAP, quien
                compró desde ese momento está en promedio ganando; perderlo con volumen suele invalidar
                la lectura alcista.
              </p>
              <div className="table-wrap">
                <table className="tabular">
                  <thead>
                    <tr>
                      <th scope="col">Ancla</th>
                      <th scope="col">AVWAP</th>
                      <th scope="col">Precio vs. AVWAP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.avwaps.map((a) => (
                      <tr key={a.key}>
                        <td style={{ whiteSpace: "normal" }}>
                          <i className="swatch" style={{ background: `var(${AVWAP_COLORS[a.key]})`, marginRight: 8 }} />
                          {a.label}
                          <span className="sub">
                            {a.status === "OK" && a.anchor ? `desde ${fmtDate(a.anchor)}` : a.note}
                          </span>
                        </td>
                        <td>{price(a.value, data.currency)}</td>
                        <td className={a.distance === null ? undefined : a.distance >= 0 ? "pos" : "neg"}>
                          {a.distance === null ? NO_DATA : fmtFraction(a.distance, 1, true)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="card">
              <h2>Perfil de volumen</h2>
              {data.profile ? (
                <>
                  <div className="tiles">
                    <div className="tile">
                      <div className="tile-label">POC</div>
                      <div className="tile-value">{price(data.profile.poc, data.currency)}</div>
                      <div className="tile-sub">precio con más volumen</div>
                    </div>
                    <div className="tile">
                      <div className="tile-label">Área de valor</div>
                      <div className="tile-value" style={{ fontSize: "1.05rem" }}>
                        {fmtNumber(data.profile.val, 2)} – {fmtNumber(data.profile.vah, 2)}
                      </div>
                      <div className="tile-sub">70 % del volumen (VAL – VAH)</div>
                    </div>
                  </div>
                  <p className="faint" style={{ margin: 0 }}>
                    Aproximación con {data.profile.bars_used} velas de 30 minutos: el volumen de cada vela
                    se reparte en partes iguales entre los precios que recorrió.
                    {data.profile.bars_skipped ? ` ${data.profile.bars_skipped} velas sin volumen excluidas.` : ""}
                  </p>
                </>
              ) : (
                <p className="muted">{data.profile_note}</p>
              )}
            </section>
          </div>
          <SourceLine
            source={data.sources[0]}
            extra="velas ajustadas; AVWAP con velas de 1 hora y precio típico (máx + mín + cierre) / 3"
          />
        </>
      )}
    </>
  );
}
