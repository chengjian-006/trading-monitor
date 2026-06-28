import client from './client'
import type { KLineBar } from '../types'

export async function fetchKline(code: string, days = 120): Promise<KLineBar[]> {
  const { data } = await client.get(`/api/kline/${code}`, { params: { days } })
  return data
}

export async function fetchKlineWeek(code: string, weeks = 80): Promise<KLineBar[]> {
  const { data } = await client.get(`/api/kline/${code}/week`, { params: { weeks } })
  return data
}

export interface StockSummary {
  code: string
  name: string | null
  close: number | null
  pct_change: number | null
  amplitude: number | null          // 振幅 %
  pct_5d: number | null             // 5日涨幅 %
  turnover: number | null           // 换手 %
  ma_status: string | null          // 强势/震荡/偏弱… 位置标签
  vol_ratio_avg10: number | null    // 今日量 / 前10日均量
  concept: string | null            // 题材概念(已拼接)
  latest_signal: { name: string; date: string; time: string; direction: string } | null
  near_buy: { tier: number; dist: number | null; name: string | null } | null
}

export async function fetchStockSummary(code: string): Promise<StockSummary | null> {
  try {
    const { data } = await client.get(`/api/kline/${code}/summary`)
    return data && data.code ? data as StockSummary : null
  } catch {
    return null
  }
}

export interface IntradayPoint {
  time: string
  price: number
  avg_price: number
  volume: number
}

export interface IntradayResult {
  pre_close: number       // 昨收(分时着色基准, 与涨跌幅同源); 历史归档或拿不到为 0
  points: IntradayPoint[]
}

// source='snapshot': 当日走 DB 优先(读迷你走势快照, 秒返, 无快照返回空); 缺省=实时
export async function fetchIntraday(code: string, date = '', source = ''): Promise<IntradayResult> {
  const params: Record<string, string> = {}
  if (date) params.date = date
  if (source) params.source = source
  const { data } = await client.get(`/api/kline/${code}/intraday`, { params })
  // 兼容老格式(裸数组)与新格式({pre_close, points}), 部署过渡期不炸
  if (Array.isArray(data)) return { pre_close: 0, points: data }
  return { pre_close: data?.pre_close ?? 0, points: Array.isArray(data?.points) ? data.points : [] }
}

export interface SignalMarker {
  time: string          // HH:MM
  price: number | null
  direction: string     // buy / sell / reduce / plunge
  signal_name: string
}

export async function fetchSignalMarkers(code: string, date = ''): Promise<SignalMarker[]> {
  const { data } = await client.get(`/api/kline/${code}/signal-markers`, { params: date ? { date } : {} })
  return Array.isArray(data) ? data : []
}

export async function fetchSignalDays(code: string): Promise<string[]> {
  const { data } = await client.get(`/api/kline/${code}/signal-days`)
  return Array.isArray(data) ? data : []
}

export interface DailyMarker {
  date: string          // YYYY-MM-DD
  time?: string         // HH:MM 触发时刻
  price: number | null
  direction: string
  signal_name: string
}

export async function fetchKlineMarkersDaily(code: string, days = 150): Promise<DailyMarker[]> {
  const { data } = await client.get(`/api/kline/${code}/signal-markers-daily`, { params: { days } })
  return Array.isArray(data) ? data : []
}

export interface BigOrderTick {
  time: string
  price: number
  hands: number
  amount: number
}

export interface BigOrders {
  code: string
  threshold: number
  total_ticks: number
  big_buys: BigOrderTick[]
  big_sells: BigOrderTick[]
  big_buy_count: number
  big_sell_count: number
  big_buy_amount: number
  big_sell_amount: number
  net_big_amount: number
}

export async function fetchBigOrders(code: string, threshold = 15_000_000): Promise<BigOrders | null> {
  const { data } = await client.get(`/api/kline/${code}/big-orders`, { params: { threshold } })
  return data && data.code ? data : null
}

export interface SparklinePoint {
  time: string
  price: number
}

export interface SparklineData {
  pre_close: number
  trends: SparklinePoint[]
}

export async function fetchBatchIntraday(codes: string[]): Promise<Record<string, SparklineData>> {
  // 后端每批上限 50 (code_list[:50]); 股票池可能 >50 只, 按 50 分批并发再合并,
  // 否则第 51 行之后的"走势"列恒空
  const CHUNK = 50
  const chunks: string[][] = []
  for (let i = 0; i < codes.length; i += CHUNK) chunks.push(codes.slice(i, i + CHUNK))
  const results = await Promise.all(
    chunks.map(async (chunk) => {
      const { data } = await client.get('/api/kline/batch-intraday', {
        params: { codes: chunk.join(',') },
      })
      return data as Record<string, SparklineData>
    }),
  )
  return Object.assign({}, ...results)
}
