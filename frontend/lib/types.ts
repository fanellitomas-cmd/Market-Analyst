// Espejo de backend/app/schemas.py

export type DataStatus = "OK" | "NO_DISPONIBLE" | "ERROR_FUENTE";

export interface Metric {
  value: number | null;
  status: DataStatus;
  note: string | null;
}

export interface Observation {
  period: string;
  value: number;
}

export interface SourceInfo {
  name: string;
  url: string;
  fetched_at: string;
}

export interface IndicatorInfo {
  key: string;
  name: string;
  unit: string;
  description: string;
  source_id: string;
}

export interface CountryInfo {
  code: string;
  name: string;
  group: string;
}

export interface CountrySeries {
  country_code: string;
  country_name: string;
  status: DataStatus;
  observations: Observation[];
  missing_periods: number;
  note: string | null;
}

export interface MacroSeriesResponse {
  indicator: IndicatorInfo;
  start_year: number;
  end_year: number;
  series: CountrySeries[];
  source: SourceInfo;
}

export interface LatestValue {
  value: number | null;
  period: string | null;
  status: DataStatus;
}

export interface MacroSnapshotResponse {
  indicators: IndicatorInfo[];
  countries: { country: CountryInfo; values: Record<string, LatestValue> }[];
  errors: Record<string, string>;
  source: SourceInfo;
}

export type PriceRange = "6mo" | "1y" | "2y" | "5y";

export interface RelativeMetrics {
  beta: Metric;
  correlation: Metric;
  r_squared: Metric;
  asset_return: Metric;
  benchmark_return: Metric;
  asset_volatility: Metric;
  benchmark_volatility: Metric;
  asset_max_drawdown: Metric;
  benchmark_max_drawdown: Metric;
}

export interface RelativeAnalysis {
  symbol: string;
  name: string | null;
  currency: string | null;
  benchmark: string;
  benchmark_name: string | null;
  range: PriceRange;
  start_date: string | null;
  end_date: string | null;
  observations: number;
  dropped_dates: number;
  adjusted_prices: boolean;
  metrics: RelativeMetrics;
  rolling_window: number;
  rolling: { date: string; correlation: number | null; beta: number | null }[];
  rebased_prices: { date: string; asset: number; benchmark: number }[];
  source: SourceInfo;
  computed_at: string;
}

export interface CompareRow {
  symbol: string;
  name: string | null;
  status: DataStatus;
  note: string | null;
  observations: number;
  metrics: RelativeMetrics | null;
}

export interface CompareResponse {
  benchmark: string;
  range: PriceRange;
  rows: CompareRow[];
  source: SourceInfo;
  computed_at: string;
}

// ---------- EE.UU. (FRED) ----------

export interface UsIndicatorInfo {
  key: string;
  series_id: string;
  name: string;
  unit: string;
  frequency: string;
  category: string;
  description: string;
  transform: "level" | "yoy_pct" | "diff";
}

export interface DatedValue {
  date: string;
  value: number;
}

export interface UsLatest {
  indicator: UsIndicatorInfo;
  status: DataStatus;
  latest: DatedValue | null;
  previous: DatedValue | null;
  change: number | null;
  year_ago: DatedValue | null;
  note: string | null;
}

export interface UsDashboardResponse {
  items: UsLatest[];
  source: SourceInfo;
}

export interface UsSeriesResponse {
  indicator: UsIndicatorInfo;
  start: string;
  observations: DatedValue[];
  missing_points: number;
  dropped_points: number;
  source: SourceInfo;
}

export interface CurvePoint {
  maturity: string;
  series_id: string;
  value: number | null;
  date: string | null;
  status: DataStatus;
}

export interface YieldCurveResponse {
  curves: { label: string; target_date: string; points: CurvePoint[] }[];
  source: SourceInfo;
}
