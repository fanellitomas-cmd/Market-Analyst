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

// ---------- Sistema (agente 24/7) ----------

export interface JobRunOut {
  id: number;
  job_id: string;
  started_at: string;
  finished_at: string | null;
  status: "ok" | "error" | "skipped" | "running";
  detail: string | null;
}

export interface JobOut {
  id: string;
  name: string;
  description: string;
  schedule: string;
  trading_days_only: boolean;
  next_run: string | null;
  last_run: JobRunOut | null;
}

export interface NotificationOut {
  id: number;
  created_at: string;
  channel: string;
  trigger: string;
  text: string;
  status: "sent" | "failed" | "disabled";
  error: string | null;
}

export interface SystemStatus {
  timezone: string;
  now: string;
  worker_last_heartbeat: string | null;
  worker_alive: boolean;
  notifier_channel: string;
  notifier_configured: boolean;
  jobs: JobOut[];
  notifications: NotificationOut[];
}

// ---------- Liquidez y calendario ----------

export interface LiqChange {
  label: string;
  kind: "pct" | "abs";
  value: number | null;
  base: DatedValue | null;
}

export interface LiqIndicator {
  key: string;
  name: string;
  group: string;
  unit: string;
  description: string;
  source: string;
  status: DataStatus;
  latest: DatedValue | null;
  changes: LiqChange[];
  note: string | null;
}

export interface LiquidityDashboard {
  indicators: LiqIndicator[];
  computed_at: string;
}

export interface LiquiditySeries {
  key: string;
  name: string;
  unit: string;
  start: string;
  points: DatedValue[];
  note: string | null;
  source: SourceInfo;
}

export interface EconEventOut {
  id: number;
  title: string;
  name: string;
  country: string;
  release_at: string;
  impact: string;
  forecast: string | null;
  forecast_value: number | null;
  previous: string | null;
  actual: number | null;
  actual_unit: string | null;
  surprise: number | null;
  surprise_note: string | null;
  reference_month: string | null;
  tracked: boolean;
}

export interface CalendarResponse {
  upcoming: EconEventOut[];
  recent: EconEventOut[];
  last_refresh: string | null;
}

// ---------- Técnico ----------

export type RegimeKind = "uptrend" | "downtrend" | "squeeze" | "range" | "unclear" | "insufficient";

export interface TechnicalResponse {
  symbol: string;
  name: string | null;
  currency: string | null;
  as_of: string;
  close: number;
  regime: {
    kind: RegimeKind;
    label: string;
    explanation: string;
    ema20: number | null;
    ema50: number | null;
    ema200: number | null;
    adx: number | null;
    bb_width: number | null;
    bb_width_percentile: number | null;
    conditions: { name: string; ok: boolean; detail: string }[];
  };
  volume: { last: number | null; avg50: number | null; zscore: number | null; relative: number | null };
  avwaps: {
    key: string;
    label: string;
    anchor: string | null;
    value: number | null;
    distance: number | null;
    status: "OK" | "NO_DISPONIBLE";
    note: string | null;
    series: { time: string; value: number }[];
  }[];
  profile: {
    interval: string;
    range: string;
    bins: { low: number; high: number; volume: number }[];
    poc: number;
    vah: number;
    val: number;
    bars_used: number;
    bars_skipped: number;
  } | null;
  profile_note: string | null;
  candles: { time: string; open: number; high: number; low: number; close: number; volume: number | null }[];
  emas: { time: string; ema20: number | null; ema50: number | null; ema200: number | null }[];
  notes: string[];
  sources: SourceInfo[];
}

// ---------- Forense ----------

export interface ScoreResult {
  value: number | null;
  zone: string | null;
  components: Record<string, number>;
  missing: string[];
  note: string | null;
}

export interface YearScores {
  fiscal_year_end: string;
  beneish: ScoreResult;
  altman: ScoreResult;
  quick_ratio: number | null;
  roic: number | null;
  fcf: number | null;
  sbc: number | null;
  fcf_minus_sbc: number | null;
  sbc_to_revenue: number | null;
  sbc_to_fcf: number | null;
  revenue: number | null;
  operating_margin: number | null;
  notes: string[];
}

export interface ForensicResponse {
  ticker: string;
  name: string;
  sic: number | null;
  sic_description: string | null;
  altman_variant: "original" | "Z''";
  years: YearScores[];
  pe: {
    current: number | null;
    percentile: number | null;
    median: number | null;
    low: number | null;
    high: number | null;
    observations: number;
    excluded_days: number;
    series: { date: string; pe: number }[];
    note: string | null;
  };
  wacc: {
    value: number | null;
    roic: number | null;
    spread: number | null;
    inputs: Record<string, number | null>;
    assumptions: string[];
    missing: string[];
  };
  sources: SourceInfo[];
}

// ---------- Screener ----------

export interface ScreenerRow {
  ticker: string;
  name: string | null;
  passes: boolean;
  met: number;
  pe: number | null;
  revenue_cagr_5y: number | null;
  operating_margin: number | null;
  operating_margin_prev: number | null;
  roic: number | null;
  wacc: number | null;
  criteria: { name: string; status: "cumple" | "no cumple" | "sin dato"; detail: string }[];
  error: string | null;
}

export interface ScreenerResponse {
  universe: string[];
  run_at: string | null;
  rows: ScreenerRow[];
}

// ---------- Centinela ----------

export interface CryptoAsset {
  coin: string;
  price: number | null;
  funding_rate: number | null;
  funding_annualized: number | null;
  funding_avg_7d: number | null;
  funding_reading: string | null;
  funding_history: { t: string; v: number }[];
  open_interest_usd: number | null;
  open_interest_change_7d: number | null;
  open_interest_history: { t: string; v: number }[];
  long_short_ratio: number | null;
  long_short_change_24h: number | null;
  liq_long_usd: number | null;
  liq_short_usd: number | null;
  liq_count: number;
  liq_since: string | null;
  errors: string[];
}

export interface CryptoResponse {
  assets: CryptoAsset[];
  exchange: string;
  computed_at: string;
}

export interface MarketRow {
  symbol: string;
  name: string;
  group: string;
  last: number | null;
  date: string | null;
  changes: { label: string; value: number | null }[];
  spark: number[];
  error: string | null;
}

export interface CommoditiesResponse {
  rows: MarketRow[];
  copper_gold: { t: string; v: number }[];
  copper_gold_change_3m: number | null;
  note: string;
  computed_at: string;
}

export interface ArbRow {
  kind: "ADR" | "CEDEAR";
  foreign: string;
  local: string;
  name: string | null;
  ratio: number;
  foreign_price: number | null;
  local_price: number | null;
  foreign_time: string | null;
  local_time: string | null;
  implied_fx: number | null;
  vs_ccl: number | null;
  vs_median: number | null;
  comparable: boolean;
  note: string | null;
}

export interface ArbitrageResponse {
  rows: ArbRow[];
  ccl_market: number | null;
  ccl_median_implied: number | null;
  cedear_ratios: { local: string; foreign: string; ratio: number }[];
  computed_at: string;
}
