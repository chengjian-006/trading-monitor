import client from './client'
import type { Signal } from '../types'

export async function fetchTodaySignals(): Promise<Signal[]> {
  const { data } = await client.get('/api/signals/today')
  return data
}

export async function fetchSignalHistory(
  limit = 200,
  opts?: { date?: string; startDate?: string; endDate?: string; signalId?: string },
): Promise<Signal[]> {
  const params: Record<string, any> = { limit, with_perf: true }
  if (opts?.date) params.date = opts.date
  if (opts?.startDate) params.start_date = opts.startDate
  if (opts?.endDate) params.end_date = opts.endDate
  if (opts?.signalId) params.signal_id = opts.signalId
  const { data } = await client.get('/api/signals/history', { params })
  return data
}

export interface SignalStatsItem {
  signal_id: string
  signal_name: string
  direction: string
  count: number
  avg_max_pct: number
  win_5pct: number
  win_10pct: number
  win_20pct: number
}

export async function fetchSignalStats(daysBack = 30): Promise<Record<string, SignalStatsItem>> {
  const { data } = await client.get('/api/signals/stats', { params: { days_back: daysBack } })
  return data
}

// 市场风险两级预警 — cfzy_biz_market_risk, 每交易日一行
export interface MarketRiskRow {
  trade_date: string
  advance_ratio: number | null   // 涨跌比%
  breadth_ma20: number | null    // 广度MA20%
  avg_ret_ma5: number | null     // 全市场5日均收益%
  low52_ratio: number | null     // 52周新低占比%
  zha_rate: number | null        // 炸板率%
  state: string                  // GREEN/YELLOW/RED
  source: string                 // eod | intraday
  updated_at?: string | null     // 状态行最后变更时刻(全局顶栏横幅「几点起」锚点)
}

export async function fetchMarketRisk(): Promise<{ latest: MarketRiskRow | null; rows: MarketRiskRow[] }> {
  const { data } = await client.get('/api/signals/market-risk')
  return data
}

export interface SignalOutcomeStatsItem {
  signal_id: string
  signal_name: string
  direction: string
  count: number          // 触发总数
  evaluated: number      // 已评估(已≥5个交易日 + K线就位)
  success: number        // p5_pct ≥ +5%
  fail: number           // p5_pct ≤ -3%
  neutral: number        // 介于之间
  pending: number        // 尚未评估
  success_rate: number   // success / evaluated * 100
  avg_p1_pct: number | null
  avg_p3_pct: number | null
  avg_p5_pct: number | null
}

const _outcomeCache = new Map<number, { data: Record<string, SignalOutcomeStatsItem>; ts: number }>()
const _OUTCOME_TTL = 5 * 60 * 1000

export async function fetchSignalOutcomeStats(daysBack = 90): Promise<Record<string, SignalOutcomeStatsItem>> {
  const c = _outcomeCache.get(daysBack)
  if (c && Date.now() - c.ts < _OUTCOME_TTL) return c.data
  const { data } = await client.get('/api/signals/outcome-stats', { params: { days_back: daysBack } })
  _outcomeCache.set(daysBack, { data, ts: Date.now() })
  return data
}

export interface OutcomeSide {
  count: number
  evaluated: number
  success: number
  fail: number
  neutral: number
  pending: number
  success_rate: number
  avg_p1: number | null
  avg_p3: number | null
  avg_p5: number | null
}

export interface OutcomeCompare {
  buy: OutcomeSide
  sell: OutcomeSide
}

export async function fetchOutcomeCompare(daysBack = 90): Promise<OutcomeCompare> {
  const { data } = await client.get('/api/signals/outcome-compare', { params: { days_back: daysBack } })
  return data
}

export interface WeeklyTrendWeek {
  week_start: string
  buy: { evaluated: number; success: number; rate: number | null }
  sell: { evaluated: number; success: number; rate: number | null }
}

export async function fetchWeeklyTrend(weeks = 12): Promise<WeeklyTrendWeek[]> {
  const { data } = await client.get('/api/signals/weekly-trend', { params: { weeks } })
  return Array.isArray(data) ? data : []
}

export interface ModelWeeklyCell {
  week_start: string
  evaluated: number
  success: number
  rate: number | null
  avg_p5: number | null
}
export interface ModelWeeklyRow {
  signal_id: string
  signal_name: string
  cells: ModelWeeklyCell[]
  recent_eval: number
  recent_success: number
  recent_rate: number | null
}
export interface ModelWeekly {
  weeks: string[]
  models: ModelWeeklyRow[]
}

export async function fetchModelWeekly(weeks = 8): Promise<ModelWeekly> {
  const { data } = await client.get('/api/signals/model-weekly', { params: { weeks } })
  return data && Array.isArray(data.models) ? data : { weeks: [], models: [] }
}

export interface ModelBacktestRow {
  model_name: string
  signal_id: string
  window_start: string
  n: number
  win_rate: number
  avg_span: number
  avg_eff: number
  net_mean: number
  net_after_cost: number
  annualized: number
  pf: number
}
export interface ModelBacktest {
  run_date: string | null
  window_start: string | null
  models: ModelBacktestRow[]
}
export async function fetchModelBacktest(): Promise<ModelBacktest> {
  const { data } = await client.get('/api/signals/model-backtest')
  return data && Array.isArray(data.models) ? data : { run_date: null, window_start: null, models: [] }
}

export interface ModelMonthlyPoint {
  ym: string
  win_rate: number
  n: number
  net: number
}
export interface ModelWinrateRow {
  signal_id: string
  model_name: string
  win_rate_3m: number | null
  net_3m: number | null
  n_3m: number
  win_rate_6m: number | null
  net_6m: number | null
  n_6m: number
  rank_3m: number | null
  rank_n: number
  monthly?: ModelMonthlyPoint[]
  max_drawdown?: number | null
}
export interface ModelWinrate {
  run_date: string | null
  models: ModelWinrateRow[]
}
export async function fetchModelWinrate(): Promise<ModelWinrate> {
  const { data } = await client.get('/api/signals/model-winrate')
  return data && Array.isArray(data.models) ? data : { run_date: null, models: [] }
}

export interface SignalMatrixRow {
  signal_id: string
  signal_name: string
  signal_group: string
  direction: string
  counts: number[]
  total: number
}

export interface SignalMatrixResponse {
  dates: string[]
  rows: SignalMatrixRow[]
}

export async function fetchSignalMatrix(days = 14): Promise<SignalMatrixResponse> {
  const { data } = await client.get('/api/signals/matrix', { params: { days } })
  return data
}

export async function triggerScan() {
  const { data } = await client.post('/api/scan')
  return data as { ok: boolean; signals: Signal[] }
}

export interface ReviewSignalRow {
  code: string
  name: string
  signal_id: string
  signal_name: string
  direction: string
  trigger_date: string
  trigger_time: string
  trigger_price: number | null
  cur_price: number | null
  cur_ret_pct: number | null
  max_gain_pct: number | null
  max_dd_pct: number | null
  t1_pct: number | null
  t3_pct: number | null
  t5_pct: number | null
  frozen: boolean
  outcome: string | null
  tp_label: string
  tp_price: number | null
  tp_hit: boolean
  sl_label: string
  sl_price: number | null
  sl_hit: boolean
  other_exit: string
  trade_plan: string | null
  detail: string | null
}

export interface ReviewSummaryRow {
  signal_id: string
  signal_name: string
  count: number
  win_rate: number | null
  avg_cur_ret: number | null
  median_cur_ret: number | null
  avg_max_gain: number | null
  avg_max_dd: number | null
  avg_t5: number | null
  success_rate: number | null
}

export interface ReviewListResp {
  start: string
  end: string
  latest_kline_date: string | null
  rows: ReviewSignalRow[]
  summary: ReviewSummaryRow[]
}

export async function fetchReviewSignals(
  start: string, end: string, categories: string[],
): Promise<ReviewListResp> {
  const { data } = await client.get('/api/signals/review-list', {
    params: { start, end, categories: categories.join(',') },
  })
  return data
}
