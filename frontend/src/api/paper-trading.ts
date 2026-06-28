import client from './client'

export interface PaperSummary {
  initial_capital: number; cash: number; holdings_mv: number; total_equity: number
  total_return_pct: number; total_pnl: number
  today_pnl: number; today_pnl_pct: number | null
  position_count: number; realized_pnl: number
  closed_trades: number; win_rate: number | null; profit_factor: number | null
  max_drawdown_pct: number; max_positions: number
  account_key: string; account_name: string
  buy_position_pct: number; unlimited_bullets: number
}

// 账户标识: default=模拟账户(20%/笔), unlimited=无限子弹(5%/笔, 现金可透支/不限持仓/可加仓)
export type AccountKey = 'default' | 'unlimited'

export const fetchPaperSummary = (accountKey: AccountKey = 'default') =>
  client.get<PaperSummary>('/api/paper-trading/summary', { params: { account_key: accountKey } }).then(r => r.data)

export const fetchPaperPositions = (accountKey: AccountKey = 'default') =>
  client.get('/api/paper-trading/positions', { params: { account_key: accountKey } }).then(r => r.data)

export const fetchPaperTrades = (limit = 100, offset = 0, accountKey: AccountKey = 'default') =>
  client.get('/api/paper-trading/trades', { params: { limit, offset, account_key: accountKey } }).then(r => r.data)

export const fetchPaperEquity = (accountKey: AccountKey = 'default') =>
  client.get('/api/paper-trading/equity', { params: { account_key: accountKey } }).then(r => r.data)

export const fetchPaperModelStats = (accountKey: AccountKey = 'default') =>
  client.get('/api/paper-trading/model-stats', { params: { account_key: accountKey } }).then(r => r.data)

export const updatePaperSettings = (initial_capital?: number, max_positions?: number, accountKey: AccountKey = 'default') =>
  client.put('/api/paper-trading/settings', { initial_capital, max_positions, account_key: accountKey }).then(r => r.data)

export const resetPaperAccount = (initial_capital: number, max_positions: number, accountKey: AccountKey = 'default') =>
  client.post('/api/paper-trading/reset', { initial_capital, max_positions, account_key: accountKey }).then(r => r.data)
