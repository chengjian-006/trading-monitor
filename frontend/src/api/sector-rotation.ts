import client from './client'

// 盘中题材轮动状态(已按强弱排序)
export interface SectorRotationItem {
  theme: string
  state: string // "启动" | "升温" | "高潮" | "退潮" | "持平" | "冷"
  limit_up: number
  yest?: number // 昨日(上一交易日)该题材涨停家数, 日基准口径
  slope: number
  max_height: number
  broken: number
  first_board: number
  samples: string[]
}

// 今日转换事件(弱强转换流水头条): 某题材某时刻发生的状态跃迁
export interface SectorTransition {
  at: string           // 触发时刻 HH:MM
  direction: string    // "weak_to_strong" 弱转强 | "strong_to_weak" 强转弱
  theme: string
  limit_up: number
  yest?: number        // 昨日该题材涨停家数(日基准口径: 昨X→今Y)
  slope: number
  max_height: number
  broken: number
  samples: string[]
}

// 次日预测单条
export interface SectorPredictItem {
  theme: string
  reason: string
  traj: string // 形如 "0→1→2→5"(近期每日涨停家数轨迹)
  today: number
  samples: string[]
}

export interface SectorPredict {
  弱转强候选: SectorPredictItem[]
  强转弱候选: SectorPredictItem[]
  强势延续: SectorPredictItem[]
  疑似终结: SectorPredictItem[]
}

export interface SectorRotationData {
  trade_date: string | null
  computed_at: string | null // 盘中轮动快照时间
  transitions?: SectorTransition[] // 今日转换流水(时间倒序), 老快照可能无此字段
  items: SectorRotationItem[]
  predict_at: string | null // 14:30 次日预测生成时间
  predict: SectorPredict | null // 未到14:30或无数据时为 null
  stale?: boolean // true=当天还没算出, 显示的是回退的上一交易日快照(盘前/非交易日)
}

export async function fetchSectorRotation(): Promise<SectorRotationData> {
  const { data } = await client.get('/api/sector-rotation')
  return data as SectorRotationData
}
