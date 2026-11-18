"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNotice, Skeleton } from "@/components/Common";
import { api, ApiError } from "@/lib/api";
import type { JobRunOut, NotificationOut, SystemStatus } from "@/lib/types";

const TZ = "America/Argentina/Buenos_Aires";

function fmtAr(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("es-AR", {
    timeZone: TZ,
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const RUN_LABEL: Record<JobRunOut["status"], string> = {
  ok: "OK",
  error: "Con error",
  skipped: "Salteada",
  running: "Corriendo",
};
const NOTE_LABEL: Record<NotificationOut["status"], string> = {
  sent: "Enviada",
  failed: "Falló",
  disabled: "Sin canal",
};

function Pill({ tone, children }: { tone: "good" | "bad" | "neutral"; children: React.ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export default function SistemaPage() {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    api<SystemStatus>("/api/system/status")
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Error inesperado."));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  async function act(key: string, path: string, done: (r: unknown) => string) {
    setBusy(key);
    setMessage(null);
    try {
      const r = await api<unknown>(path, { method: "POST" });
      setMessage(done(r));
      load();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Error inesperado.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>Sistema</h1>
        <p className="muted" style={{ margin: 0 }}>
          El agente que trabaja en segundo plano: qué tareas tiene programadas, cuándo corrieron y qué
          avisos mandó. Horarios en hora de Buenos Aires.
        </p>
      </div>

      {error && <ErrorNotice message={error} />}
      {!data && !error && <Skeleton height={300} />}

      {data && (
        <>
          <div className="grid-2">
            <section className="card">
              <div className="card-head">
                <h2>Agente</h2>
                {data.worker_alive ? <Pill tone="good">Activo</Pill> : <Pill tone="bad">Detenido</Pill>}
              </div>
              <p className="muted" style={{ margin: 0 }}>
                {data.worker_last_heartbeat
                  ? `Última señal de vida: ${fmtAr(data.worker_last_heartbeat)}`
                  : "Todavía no dio señales de vida."}{" "}
                {!data.worker_alive &&
                  "Las tareas programadas sólo corren mientras el proceso del agente está encendido (python -m app.worker o el servicio worker de Docker)."}
              </p>
            </section>
            <section className="card">
              <div className="card-head">
                <h2>WhatsApp</h2>
                {data.notifier_configured ? (
                  <Pill tone="good">Configurado</Pill>
                ) : (
                  <Pill tone="neutral">Sin configurar</Pill>
                )}
              </div>
              <p className="muted" style={{ margin: 0 }}>
                {data.notifier_configured
                  ? "Las alertas llegan a tu número a través de CallMeBot."
                  : "Completá WHATSAPP_PHONE y CALLMEBOT_APIKEY (en backend/.env, o en Render y en los secretos de GitHub). Mientras tanto, los avisos quedan registrados acá abajo."}
              </p>
              <div>
                <button
                  disabled={busy !== null}
                  onClick={() =>
                    act("test", "/api/system/notify-test", (r) => {
                      const n = r as NotificationOut;
                      return n.status === "sent"
                        ? "Mensaje de prueba enviado. Revisá tu WhatsApp."
                        : `No se envió: ${n.error ?? NOTE_LABEL[n.status]}`;
                    })
                  }
                >
                  {busy === "test" ? "Enviando…" : "Enviar mensaje de prueba"}
                </button>
              </div>
            </section>
          </div>

          {message && <div className="notice warn">{message}</div>}

          <section className="card">
            <h2>Tareas programadas</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Tarea</th>
                    <th scope="col">Horario</th>
                    <th scope="col">Próxima</th>
                    <th scope="col">Última corrida</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {data.jobs.map((j) => (
                    <tr key={j.id}>
                      <td style={{ whiteSpace: "normal", minWidth: 220 }}>
                        {j.name}
                        <span className="sub">{j.description}</span>
                      </td>
                      <td>{j.schedule}</td>
                      <td>{fmtAr(j.next_run)}</td>
                      <td style={{ whiteSpace: "normal", minWidth: 200 }}>
                        {j.last_run ? (
                          <>
                            <Pill
                              tone={
                                j.last_run.status === "ok"
                                  ? "good"
                                  : j.last_run.status === "error"
                                    ? "bad"
                                    : "neutral"
                              }
                            >
                              {RUN_LABEL[j.last_run.status]}
                            </Pill>{" "}
                            {fmtAr(j.last_run.started_at)}
                            <span className="sub">{j.last_run.detail}</span>
                          </>
                        ) : (
                          <span className="muted">Nunca</span>
                        )}
                      </td>
                      <td>
                        <button
                          disabled={busy !== null}
                          onClick={() =>
                            act(j.id, `/api/system/jobs/${j.id}/run`, (r) => {
                              const run = r as JobRunOut;
                              return `${j.name}: ${RUN_LABEL[run.status]}. ${run.detail ?? ""}`;
                            })
                          }
                        >
                          {busy === j.id ? "Corriendo…" : "Ejecutar ahora"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>Últimos avisos</h2>
            {data.notifications.length === 0 ? (
              <p className="muted">Todavía no se generó ningún aviso.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Cuándo</th>
                      <th scope="col">Origen</th>
                      <th scope="col">Mensaje</th>
                      <th scope="col">Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.notifications.map((n) => (
                      <tr key={n.id}>
                        <td>{fmtAr(n.created_at)}</td>
                        <td>{n.trigger}</td>
                        <td style={{ whiteSpace: "pre-wrap", textAlign: "left", minWidth: 260 }}>{n.text}</td>
                        <td>
                          <Pill tone={n.status === "sent" ? "good" : n.status === "failed" ? "bad" : "neutral"}>
                            {NOTE_LABEL[n.status]}
                          </Pill>
                          {n.error && <span className="sub">{n.error}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
