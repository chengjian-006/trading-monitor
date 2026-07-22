import client from './client'

export interface MarketReport {
  id: number
  time_slot: string
  content: string
  created_at: string
}

const SLOT_NAMES: Record<string, string> = {
  '0926': '早盘概览',
  '1000': '早盘跟踪',
  '1130': '上午收盘',
  '1400': '午后分析',
  '1500': '收盘总结',
}

export function getSlotName(slot: string): string {
  return SLOT_NAMES[slot] || slot
}

export async function fetchTodayReports(): Promise<MarketReport[]> {
  const { data } = await client.get('/api/market-report')
  return data
}

export async function fetchLatestReport(): Promise<MarketReport | null> {
  const { data } = await client.get('/api/market-report/latest')
  return data
}

export async function triggerReport(): Promise<{ ok: boolean }> {
  const { data } = await client.post('/api/market-report/generate')
  return data
}

export interface IndexTrendPoint {
  time: string
  price: number
  volume?: number      // v1.7.x: 该分钟新增成交量(手), 供成交量柱图
  avg_price?: number   // 当日均线
}

export interface IndexTrendData {
  name: string
  pre_close: number
  amount: number
  trends: IndexTrendPoint[]
}

export async function fetchIndexTrends(): Promise<Record<string, IndexTrendData>> {
  const { data } = await client.get('/api/market-report/index-trends')
  return data
}

export interface MarketStats {
  limit_up: number
  limit_down: number
  up_count: number
  down_count: number
}

export async function fetchMarketStats(): Promise<MarketStats> {
  const { data } = await client.get('/api/market-report/market-stats')
  return data
}

// v1.7.88: 实时市场概览(全球+A股+温度), 替代原 AI 报告里那块静态展示
export interface IndexQuote {
  name: string
  price: number
  pct_change: number
  amount?: number
  region?: string
  update_time?: string   // 该指数报价时间 'MM-DD HH:MM' (海外/港股与A股不同时段, 美股新浪无此字段为空)
}

export interface MarketOverview {
  global_indices: IndexQuote[]
  indices: IndexQuote[]
  market_stats: MarketStats
  snapshot_at?: string | null  // v1.7.97: DB 里这份快照的时间戳
}

export async function fetchMarketOverview(): Promise<MarketOverview> {
  const { data } = await client.get('/api/market-report/overview')
  return data
}

// v1.7.x: 监控看板 tape — 涨幅前 20 + 跌幅前 15 (后端 30s 缓存)
export interface HotStock {
  code: string
  name: string
  price: number
  pct: number
}
export interface HotStocks {
  gainers: HotStock[]
  losers: HotStock[]
}
export async function fetchHotStocks(): Promise<HotStocks> {
  const { data } = await client.get('/api/market-report/hot-stocks')
  return data
}

// v1.7.x: 登录页用的公开版 overview, 无需 auth
export interface PublicOverview {
  indices: { name: string; price: number; pct_change: number }[]
  limit_up: number
  limit_down: number
  snapshot_at: string | null
}

export async function fetchPublicOverview(): Promise<PublicOverview> {
  // 登录页未登录, 不能用带 token 的 client; 用原生 fetch 避免 401 拦截
  const resp = await fetch('/api/market-report/overview-public')
  if (!resp.ok) throw new Error('fetch failed')
  return await resp.json()
}

// regime 接口已删(v1.7.752): 大盘状态统一走 /api/signals/market-risk(三档+风险分+大白话),
// 实时成交额并入 /turnover(today_yi 每次请求实时覆盖)。

// 两市成交额: 今日/较上一日/5日均额/60日均额/预测全天 (单位: 亿)
export interface TurnoverData {
  today_yi: number | null
  prev_yi: number | null
  ma5_yi: number | null
  ma60_yi: number | null
  projected_yi: number | null
  as_of: string | null
  ok: boolean
}
export async function fetchTurnover(): Promise<TurnoverData | null> {
  try {
    const { data } = await client.get('/api/market-report/turnover')
    return data
  } catch {
    return null
  }
}

// 全市场成交额排名 top100: { code: 名次(1-100) }; 不在表里=100名外
export async function fetchAmountRank(): Promise<Record<string, number>> {
  try {
    const { data } = await client.get('/api/market-report/amount-rank')
    return (data && typeof data === 'object') ? data : {}
  } catch {
    return {}
  }
}

// 今日成交放量股 (自选+持仓, 按量比降序)
export interface VolumeSurgeItem {
  code: string
  name: string
  volume_ratio: number
  amount?: number    // 成交额, 单位「元」
  pct_change: number
  status?: string
}
export async function fetchVolumeSurge(): Promise<VolumeSurgeItem[]> {
  try {
    const { data } = await client.get('/api/market-report/volume-surge')
    return data?.items ?? []
  } catch {
    return []
  }
}

// 市场情绪温度表: 日期×题材 涨停家数矩阵 (按炒作大类归并)
export interface ThemeHeatSub { theme: string; c: number; s: string }   // 大类内部细题材分布(下钻用)
export interface ThemeHeatTheme { name: string; total: number; days_on: number; members?: string[] }
export interface ThemeHeatCell { c: number; s: string; sub?: ThemeHeatSub[] }
export interface ThemeHeatData {
  dates: string[]
  themes: ThemeHeatTheme[]
  cells: Record<string, Record<string, ThemeHeatCell>>
}
export async function fetchThemeHeat(days = 15): Promise<ThemeHeatData | null> {
  try {
    const { data } = await client.get('/api/market-report/theme-heat', { params: { days } })
    return data
  } catch {
    return null
  }
}

// v1.7.x: 4 指数日 K 趋势 (近 N 个交易日)
export interface IndexDailyBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface IndexDailyData {
  name: string
  data: IndexDailyBar[]
}

export async function fetchIndexDaily(days = 30): Promise<Record<string, IndexDailyData>> {
  const { data } = await client.get('/api/market-report/index-daily', { params: { days } })
  return data
}

// ── AI 报告反馈 ──
export interface ReportFeedback {
  id: number
  user_id: number
  report_id: number
  vote: 'up' | 'down'
  notes: string | null
  created_at: string
  updated_at: string
}

export async function upsertReportFeedback(reportId: number, vote: 'up' | 'down', notes?: string | null): Promise<{ id: number; ok: boolean }> {
  const { data } = await client.post(`/api/market-report/${reportId}/feedback`, { vote, notes: notes ?? null })
  return data
}

export async function deleteReportFeedback(reportId: number): Promise<void> {
  await client.delete(`/api/market-report/${reportId}/feedback`)
}

export async function fetchReportFeedback(reportIds?: number[]): Promise<ReportFeedback[]> {
  const params: Record<string, string> = {}
  if (reportIds && reportIds.length) {
    params.report_ids = reportIds.join(',')
  }
  const { data } = await client.get('/api/market-report/feedback', { params })
  return data
}
