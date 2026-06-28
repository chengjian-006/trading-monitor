import client from './client'

export interface BoardLadderItem {
  height: number
  count: number
}

export interface BoardStock {
  code: string
  name: string
  height: number            // 真连板数(连续涨停, 后端按日K线倒数)
  streak_label?: string     // 同花顺原始连板描述 "N天M板"/"N连板"/"首板", 断板股做标签用
  reason: string
  pct: number | null
  open_times: number
  in_pool?: boolean
}

export interface EmotionSnapshot {
  trade_date: string
  captured_at: string
  source: string
  limit_up_count: number | null
  limit_up_history: number | null
  limit_down_count: number | null
  limit_down_history: number | null
  broken_board_count: number | null
  up_count: number | null
  down_count: number | null
  seal_rate: number | null
  highest_board: number | null
  board_ladder: BoardLadderItem[] | null
  board_stocks: BoardStock[] | null
  limit_up_codes: string[] | null
  yest_limit_up_premium: number | null
  emotion_phase: string
}

export interface EmotionHistory {
  trade_date: string | null
  points: EmotionSnapshot[]
}

export async function fetchCurrentEmotion(): Promise<EmotionSnapshot | Record<string, never>> {
  const { data } = await client.get('/api/emotion/current')
  return data
}

export async function fetchEmotionHistory(date?: string): Promise<EmotionHistory> {
  const { data } = await client.get('/api/emotion/history', { params: date ? { date } : {} })
  return data as EmotionHistory
}
