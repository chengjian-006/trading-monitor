import client from './client'

export interface LimitUpStock {
  code: string
  name: string
  height: number
  streak_label: string
  reason: string
  pct: number | null
  open_times: number
}

export interface LimitUpMeta {
  limit_up_count?: number | null
  limit_up_history?: number | null
  limit_down_count?: number | null
  broken_board_count?: number | null
  seal_rate?: number | null
}

export interface LimitUpDay {
  trade_date: string | null
  meta: LimitUpMeta
  boards: LimitUpStock[]
  live: boolean
}

export async function fetchLimitUp(date?: string): Promise<LimitUpDay> {
  const { data } = await client.get('/api/limit-up', { params: date ? { date } : {} })
  return data as LimitUpDay
}

export async function fetchLimitUpDates(): Promise<string[]> {
  const { data } = await client.get('/api/limit-up/dates')
  return (data?.dates || []) as string[]
}

export function limitUpExportUrl(date?: string): string {
  const q = date ? `?date=${date}` : ''
  return `/api/limit-up/export${q}`
}
