"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNotice, Skeleton, SourceLine } from "@/components/Common";
import { api, ApiError } from "@/lib/api";
import { fmtByUnit } from "@/lib/format";
import type { MacroSnapshotResponse } from "@/lib/types";

export default function PanoramaPage() {
  const [data, setData] = useState<MacroSnapshotResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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
            <li>Tasas, curva de rendimientos e inflación mensual de EE.UU. (FRED)</li>
            <li>Reservas, tipo de cambio y tasas de Argentina (BCRA)</li>
            <li>Resultados trimestrales y reacción del precio</li>
            <li>Panel diario de índices globales</li>
          </ul>
        </section>
      </div>
    </>
  );
}
