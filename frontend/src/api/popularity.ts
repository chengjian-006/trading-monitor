import client from './client'

export interface PopularityStock {
  rank: number
  rank_change: number
  code: string
  name: string
  pct_change: number
  amount: number
  turnover: number
  speed: number
  industry: string
  concepts: string[]
  announcements: string[]
  hot_reason: string[]
  ai_analysis: string
  ai_analysis_at?: string   // v1.7.166: AI 解读刷新时间 'YYYY-MM-DD HH:MM:SS'
}

export interface HotSector {
  name: string
  count: number
  stocks: string[]
  pct_today: number
  pct_5day: number
}

export interface PopularityResult {
  stocks: PopularityStock[]
  hot_industries: HotSector[]
  hot_concepts: HotSector[]
  updated_at?: string
}

export async function fetchPopularity(refresh = false, date?: string): Promise<PopularityResult> {
  const params: Record<string, unknown> = {}
  if (refresh) params.refresh = true
  if (date) params.date = date
  const { data } = await client.get('/api/popularity', { params })
  return data as PopularityResult
}

export async function fetchPopularityDates(): Promise<string[]> {
  const { data } = await client.get('/api/popularity/dates')
  return (data as { dates: string[] }).dates
}

export async function analyzeStockAi(code: string, date?: string): Promise<{ ai_analysis: string; ai_analysis_at: string }> {
  const { data } = await client.post(
    `/api/popularity/stocks/${code}/ai-analyze`,
    null,
    { params: date ? { date } : {}, timeout: 90000 },
  )
  const d = data as { ai_analysis: string; ai_analysis_at: string }
  return { ai_analysis: d.ai_analysis, ai_analysis_at: d.ai_analysis_at }
}
