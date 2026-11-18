import { fmtDate, fmtUsChange, fmtUsValue } from "@/lib/format";
import type { UsLatest } from "@/lib/types";

/** Tarjeta con el último dato de un indicador de FRED y su cambio contra la observación previa. */
export function UsTile({
  item,
  selected,
  onSelect,
}: {
  item: UsLatest;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const { indicator: ind, latest, change, status } = item;
  const body = (
    <>
      <div className="tile-label">{ind.name}</div>
      <div className="tile-value">{fmtUsValue(latest?.value ?? null, ind.unit)}</div>
      {status === "OK" && latest ? (
        <div className="tile-sub">
          {fmtDate(latest.date)} · {ind.frequency.toLowerCase()}
          <br />
          {change !== null ? `${fmtUsChange(change, ind.unit)} vs. dato anterior` : " "}
        </div>
      ) : (
        <div className="tile-sub">
          {status === "ERROR_FUENTE" ? "Fuente no disponible" : "Sin dato"}
          <br />
          {" "}
        </div>
      )}
    </>
  );
  if (!onSelect) return <div className="tile">{body}</div>;
  return (
    <button
      type="button"
      className="tile tile-button"
      aria-pressed={selected}
      onClick={onSelect}
      title={ind.description}
    >
      {body}
    </button>
  );
}
