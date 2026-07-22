import client from './client'

export interface UploadStatus {
  uploaded_today: boolean
  should_remind: boolean
  last_import: string | null
}

export async function fetchUploadStatus(): Promise<UploadStatus> {
  const { data } = await client.get('/api/trade-analysis/upload-status')
  return data
}

export interface PairedTrade {
  code: string
  name: string
  buy_date: string
  buy_price: number
  sell_date: string
  sell_price: number
  quantity: number
  buy_amount: number
  sell_amount: number
  profit: number
  return_pct: number
  hold_days: number
}

export interface StockSummary {
  name: string
  total_trades: number
  win_count: number
  loss_count: number
  total_profit: number
  total_fee: number
  net_profit: number
  avg_return_pct: number
  avg_hold_days: number
  still_holding: number
}

export interface AnalysisSummary {
  total_trades: number
  win_count: number
  loss_count: number
  win_rate: number
  total_profit: number
  total_fee: number
  net_profit: number
  avg_return_pct: number
  max_profit_pct: number
  max_loss_pct: number
  avg_hold_days: number
}

export interface TradeRecord {
  trade_date: string
  trade_time: string
  code: string
  name: string
  direction: 'buy' | 'sell'
  quantity: number
  price: number
  amount: number
  fee: number
  net_amount: number
}

export interface AnalysisResult {
  ok: boolean
  msg?: string
  record_count?: number
  records?: TradeRecord[]
  trades?: PairedTrade[]
  by_stock?: Record<string, StockSummary>
  summary?: AnalysisSummary
}

export async function importText(text: string): Promise<AnalysisResult> {
  // 导入要全量重算830+条记录, 比全局默认10s久; 与Excel上传同样给60s
  const { data } = await client.post('/api/trade-analysis/import-text', { text }, { timeout: 60000 })
  return data
}

export async function importHistory(text: string, tradeDate: string): Promise<AnalysisResult> {
  // 历史成交导入: text=粘贴内容(无日期列), tradeDate=YYYY-MM-DD(日期选择器)
  const { data } = await client.post(
    '/api/trade-analysis/import-history',
    { text, trade_date: tradeDate },
    { timeout: 60000 },
  )
  return data
}

export async function importExcel(file: File): Promise<AnalysisResult> {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await client.post('/api/trade-analysis/import-excel', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
  return data
}

// ── 实盘 vs 模型 对比 ──
export interface BuyCompareItem {
  code: string
  name: string
  buy_date: string
  buy_price: number
  verdict: '符合模型' | '偏离模型' | '无法评估'
  matched_signal: string
  matched_signal_name: string
  signal_gap: number | null
  detail: string
}

export interface SellCompareItem {
  code: string
  name: string
  buy_date: string
  sell_date: string
  actual_return: number
  hold_days: number
  model_exit_date: string
  model_reason: string
  model_return: number | null
  day_diff: number | null
  verdict: '符合模型' | '卖太晚' | '卖太早' | '无法评估'
}

export interface MissedSignal {
  code: string
  name: string
  signal_date: string
  signal_id: string
  signal_name: string
  detail: string
  forward_ret_5d: number | null
}

export interface GroupStat {
  count: number
  win_rate: number
  avg_return: number
}

export interface CompareResult {
  ok: boolean
  msg?: string
  buy_compare?: { total: number; aligned: number; deviated: number; not_evaluable: number; details: BuyCompareItem[] }
  sell_compare?: { total: number; aligned: number; too_late: number; too_early: number; not_evaluable: number; details: SellCompareItem[] }
  missed_signals?: MissedSignal[]
  pnl_contrast?: { aligned: GroupStat; deviated: GroupStat }
  meta?: {
    signal_window: number
    stocks_total: number
    stocks_evaluated: number
    stocks_no_kline: string[]
    paired_trades: number
  }
}

export async function compareToModel(signalWindow = 5): Promise<CompareResult> {
  const { data } = await client.get('/api/trade-analysis/compare', {
    params: { signal_window: signalWindow },
    timeout: 180000,
  })
  return data
}

// ── 当前持仓明细(交割单摊薄成本口径) ──
export interface HoldingItem {
  code: string
  name: string
  qty: number
  avg_cost: number | null          // 摊薄成本; 成本存疑时 null
  cost_unreliable: boolean         // 卖>买缺买单致成本偏低
  price: number | null             // 现价(自选池实时刷)
  pct_change: number | null
  market_value: number | null      // 市值 = 现价×股数
  float_pnl: number | null         // 浮动盈亏额
  float_pnl_pct: number | null     // 浮动盈亏%; 成本≤0时 null
  entry_date: string               // 建仓日(当前持仓段)
  holding_days: number             // 持仓天数(交易日)
  entry_model: string | null       // 买入模型名(开放回合归因, 无则 null)
  board_name: string | null        // 所属最热题材板块
  board_rank: number | null        // 板块内涨幅名次
  board_total: number | null
}

export interface HoldingsSummary {
  count: number
  total_market_value: number
  total_float_pnl: number
  total_float_pnl_pct: number | null
}

export interface HoldingsResult {
  holdings: HoldingItem[]
  summary: HoldingsSummary
}

export async function fetchHoldings(): Promise<HoldingsResult> {
  const { data } = await client.get('/api/trade-analysis/holdings', { timeout: 30000 })
  return data
}

export async function syncPositions(): Promise<{ ok: boolean; synced: number }> {
  const { data } = await client.post('/api/trade-analysis/sync-positions', {}, { timeout: 60000 })
  return data
}
