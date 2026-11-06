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

// ---------- Argentina ----------

export type ArKind = "musd" | "mars" | "fx" | "pct" | "index" | "bps";

export interface ArIndicatorInfo {
  key: string;
  name: string;
  group: string;
  kind: ArKind;
  description: string;
  source: string;
}

export interface ArLatest {
  indicator: ArIndicatorInfo;
  status: DataStatus;
  value: number | null;
  date: string | null;
  source_description: string | null;
  source_unit: string | null;
  note: string | null;
}

export interface DollarRow {
  house: string;
  name: string;
  buy: number | null;
  sell: number | null;
  updated_at: string | null;
  gap_vs_official: number | null;
}

export interface ArDashboardResponse {
  dollars: { status: DataStatus; rows: DollarRow[]; note: string | null; source: SourceInfo };
  country_risk: ArLatest;
  country_risk_change: number | null;
  bcra: ArLatest[];
  bcra_source: SourceInfo;
}

export interface ArSeriesResponse {
  indicator: ArIndicatorInfo;
  start: string;
  observations: DatedValue[];
  source_description: string | null;
  source: SourceInfo;
}

// ---------- Resultados (SEC) ----------

export interface QuarterRow {
  period_start: string;
  period_end: string;
  revenue: Metric;
  revenue_yoy: Metric;
  gross_margin: Metric;
  operating_margin: Metric;
  net_margin: Metric;
  net_income: Metric;
  eps_diluted: Metric;
  eps_yoy: Metric;
  derived: boolean;
}

export interface Reaction {
  horizon: number;
  stock: number | null;
  benchmark: number | null;
  excess: number | null;
  status: DataStatus;
  note: string | null;
}

export interface EarningsEvent {
  filing_date: string;
  accepted_at_ny: string | null;
  session: string;
  base_date: string | null;
  reactions: Reaction[];
  filing_url: string;
}

export interface EarningsResponse {
  company: { ticker: string; name: string; cik: number };
  quarters: QuarterRow[];
  events: EarningsEvent[];
  avg_abs_move_1d: Metric;
  sources: SourceInfo[];
}
