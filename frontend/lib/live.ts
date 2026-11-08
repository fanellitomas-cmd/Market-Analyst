"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Ejecuta `load` al montar y después cada `intervalMs`, sólo mientras la pestaña está visible
 * (así no se gastan pedidos a las fuentes con la pestaña en segundo plano). Al volver a la
 * pestaña se actualiza enseguida. Devuelve la hora de la última actualización.
 */
export function useAutoRefresh(load: () => void | Promise<unknown>, intervalMs: number): Date | null {
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (document.hidden) return;
      await loadRef.current();
      if (!cancelled) setLastUpdated(new Date());
    };
    void run();
    const timer = setInterval(run, intervalMs);
    const onVisible = () => {
      if (!document.hidden) void run();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs]);

  return lastUpdated;
}

export function fmtClock(d: Date | null): string {
  if (!d) return "—";
  return d.toLocaleTimeString("es-AR", {
    timeZone: "America/Argentina/Buenos_Aires",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
