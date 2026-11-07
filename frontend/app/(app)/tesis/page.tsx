"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNotice, Skeleton } from "@/components/Common";
import { api, ApiError } from "@/lib/api";
import { fmtArDateTime, fmtDate, fmtNumber } from "@/lib/format";
import type { ThesisOut } from "@/lib/types";

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Error inesperado.";
}

const STATUS_TONE: Record<ThesisOut["status"], string> = {
  activa: "pill-good",
  invalidada: "pill-bad",
  cerrada: "pill-neutral",
};

function ThesisCard({ t, onChange }: { t: ThesisOut; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const c = t.last_check;

  async function act(path: string, method: "POST" | "DELETE", body?: object) {
    setBusy(true);
    try {
      await api(path, { method, body: body ? JSON.stringify(body) : undefined });
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {t.ticker} <span className={`pill ${STATUS_TONE[t.status]}`}>{t.status}</span>
        </h2>
        <span className="faint">
          creada {fmtArDateTime(t.created_at)}
          {c ? ` · revisada ${fmtArDateTime(c.checked_at)}` : ""}
        </span>
      </div>
      <p style={{ margin: 0 }}>
        <b>1. Hipótesis:</b> {t.hypothesis}
      </p>
      {t.close_reason && (
        <div className={`notice ${t.status === "invalidada" ? "error" : "warn"}`}>
          {t.status === "invalidada" ? "Kill switch: " : "Cerrada: "}
          {t.close_reason}
          {t.status === "invalidada" ? ". Sin promediar a la baja." : ""}
        </div>
      )}
      {c && (
        <div className="grid-2">
          {(["fundamental", "técnica"] as const).map((g) => (
            <div key={g}>
              <h3>{g === "fundamental" ? "2. Validación fundamental" : "3. Confirmación técnica"}</h3>
              <ul className="check-list">
                {c.checks
                  .filter((x) => x.group === g)
                  .map((x) => (
                    <li key={x.name}>
                      <span className={x.status === "cumple" ? "pos" : x.status === "no cumple" ? "neg" : "muted"}>
                        {x.status === "cumple" ? "✓" : x.status === "no cumple" ? "✗" : "—"}
                      </span>{" "}
                      {x.name} <span className="faint">({x.detail})</span>
                    </li>
                  ))}
                {g === "técnica" && c.regime && (
                  <li className="faint">Régimen: {c.regime}</li>
                )}
              </ul>
            </div>
          ))}
        </div>
      )}
      {c && (
        <div>
          <h3>4. Kill switch doble</h3>
          <ul className="check-list">
            <li>
              <span className={c.kill_fundamental.triggered ? "neg" : "pos"}>{c.kill_fundamental.triggered ? "●" : "○"}</span>{" "}
              Fundamental: margen operativo del próximo balance ≥ {fmtNumber(t.min_operating_margin, 1)} %{" "}
              <span className="faint">({c.kill_fundamental.detail})</span>
            </li>
            <li>
              <span className={c.kill_technical.triggered ? "neg" : "pos"}>{c.kill_technical.triggered ? "●" : "○"}</span>{" "}
              Técnico: soporte {fmtNumber(t.support, 2)} con volumen
              {t.use_earnings_avwap ? " y AVWAP del último balance" : ""}{" "}
              <span className="faint">({c.kill_technical.detail})</span>
            </li>
          </ul>
          {c.warnings.map((w) => (
            <div className="notice warn" key={w}>
              {w}
            </div>
          ))}
        </div>
      )}
      {t.notes && <p className="faint" style={{ margin: 0 }}>Notas: {t.notes}</p>}
      <p className="faint" style={{ margin: 0 }}>
        Balance de referencia: {t.baseline_quarter_end ? `trimestre al ${fmtDate(t.baseline_quarter_end)}` : "sin datos de la SEC"}
        {t.entry_price ? ` · entrada ${fmtNumber(t.entry_price, 2)}` : ""}
      </p>
      <div className="controls">
        <button disabled={busy} onClick={() => act(`/api/theses/${t.id}/check`, "POST")}>
          {busy ? "…" : "Reevaluar ahora"}
        </button>
        {t.status === "activa" && (
          <button disabled={busy} onClick={() => act(`/api/theses/${t.id}/close`, "POST", { reason: "cerrada a mano" })}>
            Cerrar
          </button>
        )}
        <button
          className="link"
          disabled={busy}
          onClick={() => {
            if (confirm(`¿Borrar la tesis de ${t.ticker}? No se puede deshacer.`)) void act(`/api/theses/${t.id}`, "DELETE");
          }}
        >
          Borrar
        </button>
      </div>
    </section>
  );
}

export default function TesisPage() {
  const [list, setList] = useState<ThesisOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ ticker: "", hypothesis: "", margin: "", support: "", entry: "", notes: "", avwap: true });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ThesisOut[]>("/api/theses").then(setList).catch((e) => setError(errMsg(e)));
  }, []);
  useEffect(load, [load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setFormError(null);
    try {
      await api("/api/theses", {
        method: "POST",
        body: JSON.stringify({
          ticker: form.ticker,
          hypothesis: form.hypothesis,
          min_operating_margin: Number(form.margin.replace(",", ".")),
          support: Number(form.support.replace(",", ".")),
          use_earnings_avwap: form.avwap,
          entry_price: form.entry ? Number(form.entry.replace(",", ".")) : null,
          notes: form.notes || null,
        }),
      });
      setForm({ ticker: "", hypothesis: "", margin: "", support: "", entry: "", notes: "", avwap: true });
      load();
    } catch (err) {
      setFormError(errMsg(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>Tesis</h1>
        <p className="muted" style={{ margin: 0 }}>
          Cada tesis sigue el protocolo: hipótesis de desalineación, validación fundamental, confirmación
          técnica y un kill switch doble que el agente revisa al cierre de cada rueda. Si se dispara, la
          tesis se invalida y te avisa por WhatsApp: sin promediar a la baja.
        </p>
      </div>

      <section className="card">
        <h2>Nueva tesis</h2>
        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="controls">
            <label className="field">
              Ticker
              <input value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })} required style={{ width: 120 }} />
            </label>
            <label className="field">
              Margen operativo mínimo (%)
              <input value={form.margin} onChange={(e) => setForm({ ...form, margin: e.target.value })} required inputMode="decimal" style={{ width: 120 }} />
            </label>
            <label className="field">
              Soporte estructural
              <input value={form.support} onChange={(e) => setForm({ ...form, support: e.target.value })} required inputMode="decimal" style={{ width: 120 }} />
            </label>
            <label className="field">
              Precio de entrada (opcional)
              <input value={form.entry} onChange={(e) => setForm({ ...form, entry: e.target.value })} inputMode="decimal" style={{ width: 120 }} />
            </label>
          </div>
          <label className="field">
            1. Hipótesis de desalineación
            <textarea
              value={form.hypothesis}
              onChange={(e) => setForm({ ...form, hypothesis: e.target.value })}
              rows={3}
              required
              placeholder="Ej.: cotiza a 18 veces ganancias contra una mediana de 26 en 5 años, con márgenes estables."
            />
          </label>
          <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={form.avwap} onChange={(e) => setForm({ ...form, avwap: e.target.checked })} />
            Invalidar también si pierde el AVWAP del último balance
          </label>
          <label className="field">
            Notas (opcional)
            <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </label>
          {formError && <ErrorNotice message={formError} />}
          <div>
            <button className="primary" type="submit" disabled={saving}>
              {saving ? "Validando…" : "Crear y validar"}
            </button>
          </div>
        </form>
      </section>

      {error && <ErrorNotice message={error} />}
      {!list && !error && <Skeleton height={200} />}
      {list && list.length === 0 && <p className="muted">Todavía no hay tesis.</p>}
      {list?.map((t) => <ThesisCard key={t.id} t={t} onChange={load} />)}
    </>
  );
}
