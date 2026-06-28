export interface Stock {
  code: string
  name: string
  trade_type: string
  status: string
  added_at: string
  focused?: number
  hold_source?: string
  price?: number | null
  pct_change?: number | null
  pct_5d?: number | null            // 5日涨幅(%): 最新价 vs 5个交易日前收盘
  amount?: number | null
  speed?: number | null
  industry?: string
  volume_ratio?: number | null
  ma10?: number | null              // v1.7.424: 均线位置筛选(近10线±2%)
  ma20?: number | null              // 站上20线 / ≥MA20 列
  ma60?: number | null              // 近60线±2%
  free_cap?: number | null
  turnover?: number | null
  popularity_rank?: number | null
  sector_rank?: number | null
  strategy?: string | null
  substance_score?: number          // 真受益人工评分 0-5
  substance_note?: string | null    // 人工备注
  substance_updated_at?: string | null
  concepts?: string                 // v1.7.x: 概念题材, 逗号分隔(最多4个)
  limit_up_days?: number | null     // v1.7.x: 连板数(连续涨停交易日数)
  board_name?: string               // 持仓用于排名的"最热题材"板块名
  board_rank?: number | null        // 在该板块内的当日涨幅名次(1为最强)
  board_total?: number | null       // 该板块成分股总数
  quote_updated_at?: string | null  // 行情最后更新时间(给"滞后"标记判断新鲜度)
}

export interface SignalIndicators {
  close?: number
  ma5?: number
  ma10?: number
  ma20?: number
  ma60?: number
  vol_ratio_5?: number
  vol_ratio_20?: number
  pct_change?: number
  rsi?: number
  dif?: number
  dea?: number
}

export interface Signal {
  id: number
  code: string
  name: string
  signal_id: string
  signal_name: string
  direction: 'buy' | 'add' | 'sell' | 'reduce'
  price: number
  pct_change?: number
  detail: string
  strategy?: string
  triggered_at: string
  time?: string
  indicators?: SignalIndicators | null
  outcome?: 'success' | 'fail' | 'neutral' | null   // 触发后第5日收盘判定
  outcome_p1_pct?: number | null
  outcome_p3_pct?: number | null
  outcome_p5_pct?: number | null
}

export interface KLineBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
}

export interface AppConfig {
  pushplus_token: string
  pushplus_enabled: boolean
  lark_webhook: string
  lark_enabled: boolean
  scan_interval_seconds: number
  trading_hours: { start: string; end: string }[]
  anthropic_api_key: string
  ai_base_url: string
  ai_model: string
  ai_report_enabled: boolean
  sso_enabled: boolean
  ths_xml_path?: string
  database?: Record<string, unknown>
}

export interface ThsGroup {
  id: string
  name: string
  count: number
}

export interface ThsCompareResult {
  ok: boolean
  msg?: string
  source?: string
  ths_count?: number
  system_count?: number
  both?: number
  ths_only?: { code: string; name: string }[]
  system_only?: { code: string; name: string }[]
}

export interface User {
  id: number
  username: string
  role: string
  mobile?: string
  lark_webhook?: string
  lark_enabled?: number
  created_at?: string
}

export interface SignalParamConfig {
  enabled: boolean
  [key: string]: number | boolean | string
}

export type SignalConfig = Record<string, SignalParamConfig>

export interface ScheduledTask {
  id: number
  job_id: string
  name: string
  description: string
  schedule_type: 'interval' | 'cron'
  schedule_config: Record<string, number>
  handler: string
  enabled: boolean
  last_run_at: string | null
  last_status: string | null
  consecutive_failures?: number
  last_error_msg?: string | null
  next_run_at: string | null
  running: boolean
  created_at: string
  updated_at: string
}

export interface OperationLog {
  id: number
  user_id: number
  username: string
  action: string
  target: string
  old_value: Record<string, unknown> | null
  new_value: Record<string, unknown> | null
  created_at: string
}
