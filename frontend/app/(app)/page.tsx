"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { UsTile } from "@/components/UsTile";
import { api, ApiError } from "@/lib/api";
import { fmtArValue, fmtByUnit, fmtDate, fmtFraction } from "@/lib/format";
import type { ArDashboardResponse, MacroSnapshotResponse, UsDashboardResponse } from "@/lib/types";

// Los indicadores de EE.UU. que se ven de un vistazo en el Panorama.
const US_HEADLINE = ["fed_funds", "t10y", "spread_10y2y", "cpi_yoy", "unemployment", "vix"];

export default function PanoramaPage() {
  const [data, setData] = useState<MacroSnapshotResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [us, setUs] = useState<UsDashboardResponse | null>(null);
  const [usError, setUsError] = useState<string | null>(null);
  const [ar, setAr] = useState<ArDashboardResponse | null>(null);
  const [arError, setArError] = useState<string | null>(null);

  useEffect(() => {
    api<ArDashboardResponse>("/api/ar/dashboard")
      .then(setAr)
      .catch((e) => setArError(e instanceof ApiError ? e.message : "Error inesperado."));
    api<UsDashboardResponse>("/api/us/dashboard")
      .then(setUs)
      .catch((e) => setUsError(e instanceof ApiError ? e.message : "Error inesperado."));
    api<MacroSnapshotResponse>("/api/macro/snapshot")
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Error inesperado."));
  }, []);

  return (
    <>
      <div className="page-head">
        <h1>Panorama</h1>
        <p className="muted" style={{ margin: 0 }}>
          Último dato anual publicado de cada indicador, por país. El año del dato figura debajo de
          cada valor: el Banco Mundial publica con uno o dos años de rezago.
        </p>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Estados Unidos hoy</h2>
          <Link href="/eeuu">Ver tasas, curva e inflación →</Link>
        </div>
        {usError && <ErrorNotice message={usError} />}
        {!us && !usError && <Skeleton height={110} />}
        {us && (
          <>
            <div className="tiles">
              {US_HEADLINE.map((key) => us.items.find((i) => i.indicator.key === key))
                .filter((i) => i !== undefined)
                .map((item) => (
                  <UsTile key={item.indicator.key} item={item} />
                ))}
            </div>
            <SourceLine source={us.source} />
          </>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Argentina hoy</h2>
          <Link href="/argentina">Ver dólar, tasas y BCRA →</Link>
        </div>
        {arError && <ErrorNotice message={arError} />}
        {!ar && !arError && <Skeleton height={110} />}
        {ar && (
          <div className="tiles">
            {["oficial", "bolsa", "contadoconliqui", "blue"].map((house) => {
              const row = ar.dollars.rows.find((r) => r.house === house);
              if (!row) return null;
              return (
                <div className="tile" key={house}>
                  <div className="tile-label">Dólar {row.name}</div>
                  <div className="tile-value">{fmtArValue(row.sell, "fx")}</div>
                  <div className="tile-sub">
                    {row.gap_vs_official !== null
                      ? `brecha ${fmtFraction(row.gap_vs_official, 1, true)}`
                      : "venta"}
                  </div>
                </div>
              );
            })}
            {[ar.country_risk, ...ar.bcra.filter((i) => ["inflacion_mensual", "reservas"].includes(i.indicator.key))].map(
              (item) => (
                <div className="tile" key={item.indicator.key}>
                  <div className="tile-label">{item.indicator.name}</div>
                  <div className="tile-value">{fmtArValue(item.value, item.indicator.kind)}</div>
                  <div className="tile-sub">
                    {item.date ? fmtDate(item.date) : item.status === "ERROR_FUENTE" ? "Fuente no disponible" : "Sin dato"}
                    {item.indicator.kind === "musd" && item.status === "OK" ? ` · ${item.source_unit ?? "millones de USD"}` : ""}
                  </div>
                </div>
              ),
            )}
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Macro por país</h2>
          <Link href="/macro">Ver series históricas →</Link>
        </div>
        {error && <ErrorNotice message={error} />}
        {!data && !error && <Skeleton height={280} />}
        {data && (
          <>
            {Object.keys(data.errors).length > 0 && (
              <div className="notice warn">
                Algunos indicadores no se pudieron consultar:{" "}
                {Object.keys(data.errors)
                  .map((k) => data.indicators.find((i) => i.key === k)?.name ?? k)
                  .join(", ")}
                .
              </div>
            )}
            <div className="table-wrap">
              <table className="tabular">
                <thead>
                  <tr>
                    <th scope="col">País</th>
                    {data.indicators.map((ind) => (
                      <th key={ind.key} scope="col" title={ind.description}>
                        {ind.name}
                        <span className="sub">{ind.unit}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.countries.map(({ country, values }) => (
                    <tr key={country.code}>
                      <td>
                        {country.name}
                        <span className="sub">{country.group}</span>
                      </td>
                      {data.indicators.map((ind) => {
                        const v = values[ind.key];
                        const signed = ind.key === "gdp_growth" || ind.key === "current_account";
                        const cls =
                          signed && v?.value !== null && v?.value !== undefined
                            ? v.value >= 0
                              ? "pos"
                              : "neg"
                            : undefined;
                        return (
                          <td key={ind.key}>
                            <span className={cls}>{fmtByUnit(v?.value ?? null, ind.unit)}</span>
                            <span className="sub">
                              {v?.status === "OK"
                                ? v.period
                                : v?.status === "ERROR_FUENTE"
                                  ? "error de fuente"
                                  : "sin dato"}
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <SourceLine source={data.source} />
          </>
        )}
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Mercados</h2>
          <p className="muted" style={{ margin: 0 }}>
            Beta, correlación y volatilidad de cualquier acción o ETF contra el S&amp;P 500 (SPY),
            con la evolución de la correlación en el tiempo.
          </p>
          <Link href="/mercados">Analizar un activo →</Link>
        </section>
        <section className="card">
          <h2>Próximamente</h2>
          <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
            <li>Resultados trimestrales y reacción del precio</li>
            <li>Panel diario de índices globales</li>
          </ul>
        </section>
      </div>
    </>
  );
}
