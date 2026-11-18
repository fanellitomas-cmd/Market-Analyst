import type { SourceInfo } from "@/lib/types";
import { fmtDateTime } from "@/lib/format";

export function SourceLine({ source, extra }: { source: SourceInfo; extra?: string }) {
  return (
    <p className="faint" style={{ margin: 0 }}>
      Fuente:{" "}
      <a href={source.url} target="_blank" rel="noreferrer">
        {source.name}
      </a>{" "}
      · consultado {fmtDateTime(source.fetched_at)}
      {extra ? ` · ${extra}` : ""}
    </p>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice error" role="alert">
      {message}
    </div>
  );
}

export function Skeleton({ height = 160 }: { height?: number }) {
  return <div className="skeleton" style={{ minHeight: height }} aria-label="Cargando" />;
}
