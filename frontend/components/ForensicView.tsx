"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { LineChartCard } from "@/components/LineChartCard";
import { api, ApiError, qs } from "@/lib/api";
import {
  fmtDate,
  fmtFraction,
  fmtMonthYear,
  fmtNumber,
  fmtUsdCompact,
  monthTicks,
  NO_DATA,
} from "@/lib/format";
import type { ForensicResponse, ScoreResult } from "@/lib/types";

function zoneClass(zone: string | null): string | undefined {
  if (!zone) return undefined;
  if (zone.startsWith("segura") || zone.startsWith("sin señales")) return "pos";
  if (zone.startsWith("riesgo") || zone.startsWith("posible")) return "neg";
  return undefined;
}

function scoreTitle(s: ScoreResult): string | undefined {
  if (s.missing.length) return `Faltan: ${s.missing.join(", ")}`;
  return s.note ?? undefined;
}

function ScoreCell({ s }: { s: ScoreResult }) {
  return (
    <td title={scoreTitle(s)}>
      <span className={zoneClass(s.zone)}>{s.value === null ? NO_DATA : fmtNumber(s.value, 2)}</span>
    </td>
  );
}

export function ForensicView({ ticker }: { ticker: string }) {
  const [erp, setErp] = useState("5");
  const [data, setData] = useState<ForensicResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (t: string, e: string) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<ForensicResponse>(`/api/forensic/${encodeURIComponent(t)}?${qs({ erp: e || "5" })}`));
    } catch (err) {
      setData(null);
      setError(err instanceof ApiError ? err.message : "Error inesperado.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(ticker, erp);
    // La prima de riesgo se aplica con el botón "Recalcular", no en cada tecla.
  }, [ticker, load]);

  if (error) return <ErrorNotice message={error} />;
  if (!data) return <Skeleton height={400} />;

  const last = data.years[0];
  const w = data.wacc;

  return (
    <>
      <section className="card" aria-busy={loading}>
        <div className="card-head">
          <h2>
            {data.ticker} <span className="muted">· {data.name}</span>
          </h2>
          <span className="faint">
            {data.sic ? `SIC ${data.sic}${data.sic_description ? ` · ${data.sic_description}` : ""}` : ""}
          </span>
        </div>
        {data.financial_note && <div className="notice">{data.financial_note}</div>}
        {last ? (
          <div className="tiles tiles-wide">
            <div className="tile" title={scoreTitle(last.beneish)}>
              <div className="tile-label">Beneish M-Score</div>
              <div className={`tile-value ${zoneClass(last.beneish.zone) ?? ""}`}>
                {last.beneish.value === null ? NO_DATA : fmtNumber(last.beneish.value, 2)}
              </div>
              <div className="tile-sub">
                {last.beneish.zone ?? (last.beneish.missing.length ? `faltan ${last.beneish.missing.length} datos` : "")}
              </div>
            </div>
            <div className="tile" title={scoreTitle(last.altman)}>
              <div className="tile-label">Altman {data.altman_variant === "original" ? "Z (original)" : "Z''"}</div>
              <div className={`tile-value ${zoneClass(last.altman.zone) ?? ""}`}>
                {last.altman.value === null ? NO_DATA : fmtNumber(last.altman.value, 2)}
              </div>
              <div className="tile-sub">{last.altman.zone ? `zona ${last.altman.zone}` : ""}</div>
            </div>
            <div className="tile">
              <div className="tile-label">Quick ratio</div>
              <div className={`tile-value ${last.quick_ratio === null ? "" : last.quick_ratio >= 1.2 ? "pos" : "neg"}`}>
                {last.quick_ratio === null ? NO_DATA : fmtNumber(last.quick_ratio, 2)}
              </div>
              <div className="tile-sub">umbral de la tesis: &gt; 1,2</div>
            </div>
            <div className="tile">
              <div className="tile-label">ROIC − WACC</div>
              <div className={`tile-value ${w.spread === null ? "" : w.spread >= 0 ? "pos" : "neg"}`}>
                {w.spread === null ? NO_DATA : fmtFraction(w.spread, 1, true)}
              </div>
              <div className="tile-sub">
                ROIC {fmtFraction(w.roic, 1)} · WACC {fmtFraction(w.value, 1)}
              </div>
            </div>
            <div className="tile">
              <div className="tile-label">P/E actual</div>
              <div className="tile-value">{data.pe.current === null ? NO_DATA : `${fmtNumber(data.pe.current, 1)}×`}</div>
              <div className="tile-sub">
                {data.pe.percentile === null
                  ? data.pe.note ?? ""
                  : `percentil ${Math.round(data.pe.percentile * 100)} de 5 años`}
              </div>
            </div>
          </div>
        ) : (
          <p className="muted">La SEC no tiene balances anuales con ingresos para esta empresa.</p>
        )}
        <p className="faint" style={{ margin: 0 }}>
          Beneish: M &gt; −1,78 sugiere resultados inflados por devengamientos. Altman{" "}
          {data.altman_variant === "original"
            ? "original (empresa industrial): > 2,99 segura, < 1,81 riesgo"
            : "Z'' (no industrial): > 2,6 segura, < 1,1 riesgo"}
          . Pasá el mouse por un “—” para ver qué dato falta.
        </p>
      </section>

      {data.years.length > 0 && (
        <section className="card">
          <h2>Por año fiscal</h2>
          <div className="table-wrap">
            <table className="tabular">
              <thead>
                <tr>
                  <th scope="col">Cierre</th>
                  <th scope="col">Beneish M</th>
                  <th scope="col">Altman</th>
                  <th scope="col">Quick</th>
                  <th scope="col">ROIC</th>
                  <th scope="col">Margen op.</th>
                  <th scope="col">FCF</th>
                  <th scope="col">Comp. en acciones</th>
                  <th scope="col">FCF − comp.</th>
                  <th scope="col">Comp. / ingresos</th>
                </tr>
              </thead>
              <tbody>
                {data.years.map((y) => (
                  <tr key={y.fiscal_year_end}>
                    <td title={y.notes.join(" · ") || undefined}>
                      {fmtDate(y.fiscal_year_end)}
                      {y.notes.length > 0 ? " *" : ""}
                    </td>
                    <ScoreCell s={y.beneish} />
                    <ScoreCell s={y.altman} />
                    <td>{y.quick_ratio === null ? NO_DATA : fmtNumber(y.quick_ratio, 2)}</td>
                    <td>{fmtFraction(y.roic, 1)}</td>
                    <td>{fmtFraction(y.operating_margin, 1)}</td>
                    <td>{fmtUsdCompact(y.fcf)}</td>
                    <td>{fmtUsdCompact(y.sbc)}</td>
                    <td className={y.fcf_minus_sbc === null ? undefined : y.fcf_minus_sbc >= 0 ? undefined : "neg"}>
                      {fmtUsdCompact(y.fcf_minus_sbc)}
                    </td>
                    <td>{fmtFraction(y.sbc_to_revenue, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="faint" style={{ margin: 0 }}>
            FCF = flujo operativo − inversiones. La compensación en acciones (SBC) es un costo real para
            el accionista aunque no salga caja: restarla evita sobrevaluar empresas tecnológicas. * =
            hay notas sobre cómo se armó el año (pasá el mouse).
          </p>
        </section>
      )}

      <div className="grid-2">
        {last && Object.keys(last.beneish.components).length > 0 && (
          <section className="card">
            <h2>Componentes de Beneish ({fmtDate(last.fiscal_year_end)})</h2>
            <div className="table-wrap">
              <table className="tabular">
                <thead>
                  <tr>
                    <th scope="col">Índice</th>
                    <th scope="col">Valor</th>
                    <th scope="col" className="left">Qué mira</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(last.beneish.components).map(([k, v]) => (
                    <tr key={k}>
                      <td>{k}</td>
                      <td>{fmtNumber(v, 3)}</td>
                      <td className="left muted" style={{ whiteSpace: "normal" }}>
                        {BENEISH_HELP[k]}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        <section className="card">
          <h2>ROIC frente a WACC</h2>
          <form
            className="controls"
            onSubmit={(e) => {
              e.preventDefault();
              void load(ticker, erp);
            }}
          >
            <label className="field">
              Prima de riesgo del mercado (%)
              <input
                type="number"
                step="0.25"
                min="0"
                max="15"
                value={erp}
                onChange={(e) => setErp(e.target.value)}
                style={{ width: 120 }}
              />
            </label>
            <button type="submit" disabled={loading}>
              Recalcular
            </button>
          </form>
          {w.note && <div className="notice">WACC: {w.note}.</div>}
          {w.missing.length > 0 && <div className="notice warn">Falta: {w.missing.join(", ")}.</div>}
          <table className="tabular">
            <tbody>
              {Object.entries(w.inputs).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td>
                    {v === null
                      ? NO_DATA
                      : k === "capitalización" || k === "deuda"
                        ? fmtUsdCompact(v)
                        : fmtNumber(v, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="faint" style={{ margin: 0, paddingLeft: 18 }}>
            {w.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
            <li>ROIC = EBIT × (1 − tasa efectiva) / (deuda + patrimonio − caja); no incluye arrendamientos.</li>
          </ul>
        </section>
      </div>

      {data.pe.series.length > 0 && (
        <section className="card">
          <div className="card-head">
            <h2>P/E histórico</h2>
            <span className="faint">
              mediana {fmtNumber(data.pe.median, 1)}× · rango {fmtNumber(data.pe.low, 1)}× – {fmtNumber(data.pe.high, 1)}×
            </span>
          </div>
          <LineChartCard
            data={data.pe.series}
            xKey="date"
            series={[{ key: "pe", label: "P/E", color: "var(--series-1)" }]}
            yFormat={(v) => `${fmtNumber(v, 0)}×`}
            xFormat={fmtMonthYear}
            xTicks={monthTicks(data.pe.series.map((p) => p.date))}
            tooltipTitle={fmtDate}
            reference={data.pe.median ?? undefined}
            ariaLabel="P/E histórico"
          />
          <p className="faint" style={{ margin: 0 }}>
            Capitalización (precio × acciones en circulación, corregidas por splits) sobre la ganancia neta
            de los últimos 12 meses, usando en cada fecha sólo los balances ya presentados.
            {data.pe.excluded_days ? ` ${data.pe.excluded_days} días con ganancia negativa excluidos.` : ""} La
            línea punteada es la mediana.
          </p>
        </section>
      )}
      <SourceLine source={data.sources[0]} extra="10-K y 10-Q (XBRL); precios de Yahoo; tasa libre de FRED" />
    </>
  );
}

const BENEISH_HELP: Record<string, string> = {
  DSRI: "Cuentas por cobrar que crecen más que las ventas (ventas infladas o cobranzas flojas).",
  GMI: "Margen bruto que se deteriora: presión para maquillar resultados.",
  AQI: "Más activos de calidad dudosa (costos activados en vez de gastados).",
  SGI: "Crecimiento de ventas: las empresas que crecen rápido tienen más incentivos.",
  DEPI: "Depreciación más lenta: mejora el resultado sin mejorar el negocio.",
  SGAI: "Gastos de estructura que crecen más que las ventas.",
  TATA: "Ganancia que no se convierte en caja (devengamientos). Es el componente con más peso.",
  LVGI: "Más endeudamiento relativo.",
};
