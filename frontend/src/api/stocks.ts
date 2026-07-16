import client from './client'
import type { Stock } from '../types'

export async function fetchStocks(signal?: AbortSignal): Promise<Stock[]> {
  const { data } = await client.get('/api/stocks', { signal })
  return data
}

export async function addStock(params: {
  code: string
  name?: string
  trade_type?: string
  status?: string
}) {
  const { data } = await client.post('/api/stocks', params)
  return data
}

export async function updateStock(code: string, params: Record<string, string>) {
  const { data } = await client.put(`/api/stocks/${code}`, params)
  return data
}

export async function deleteStock(code: string) {
  const { data } = await client.delete(`/api/stocks/${code}`)
  return data
}

export async function reorderStocks(codes: string[]) {
  const { data } = await client.post('/api/stocks/reorder', { codes })
  return data
}

export async function searchStock(q: string) {
  const { data } = await client.get('/api/search', { params: { q } })
  return data as { code: string; name: string }[]
}

export async function ocrRecognize(file: File): Promise<{ stocks?: { code: string; name: string }[]; error?: string }> {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await client.post('/api/stocks/ocr-recognize', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
  return data
}

export async function batchImportStocks(stocks: { code: string; name: string }[]): Promise<{ ok: boolean; success: number; total: number }> {
  const { data } = await client.post('/api/stocks/batch-import', { stocks }, { timeout: 60000 })
  return data
}

export async function batchDeleteStocks(codes: string[]): Promise<{ ok: boolean; deleted: number }> {
  const { data } = await client.post('/api/stocks/batch-delete', { codes }, { timeout: 60000 })
  return data
}

// ── 自定义预警 ──
export type AlertDim = 'price' | 'pct' | 'ma_near' | 'ma_cross'
export interface AlertCondition {
  dim: AlertDim
  op?: 'gte' | 'lte'      // price / pct
  value?: number          // price / pct
  ma?: 5 | 10 | 20 | 60   // ma_near / ma_cross
  band?: number           // ma_near
  dir?: 'up' | 'down'     // ma_cross
}
export type AlertPreset = 'ma10' | 'ma20' | 'ma60'
export interface StockAlert {
  id: number
  user_id: number
  code: string
  note?: string | null
  conditions: AlertCondition[]
  preset?: AlertPreset | ''       // 均线快捷提醒标记(空=普通自定义)
  repeat_daily?: number           // 1=每天最多提醒一次(次日自动恢复)
  enabled: number
  status: 'active' | 'triggered'
  last_triggered_at?: string | null
  triggered_price?: number | null
}

export async function fetchAllAlerts(): Promise<StockAlert[]> {
  const { data } = await client.get('/api/stocks/alerts')
  return data
}

export async function fetchStockAlerts(code: string): Promise<StockAlert[]> {
  const { data } = await client.get(`/api/stocks/${code}/alerts`)
  return data
}

export async function createAlert(code: string, conditions: AlertCondition[], note = ''): Promise<{ ok: boolean; id: number }> {
  const { data } = await client.post(`/api/stocks/${code}/alerts`, { conditions, note })
  return data
}

export async function updateAlert(id: number, params: { conditions?: AlertCondition[]; note?: string; enabled?: number; status?: 'active' | 'triggered' }): Promise<{ ok: boolean }> {
  const { data } = await client.put(`/api/stocks/alerts/${id}`, params)
  return data
}

/** 均线快捷提醒一键开关: 开=建「碰线±0.5%·每天一次」预警, 关=删。 */
export async function togglePresetAlert(code: string, preset: AlertPreset, on: boolean): Promise<{ ok: boolean; id?: number }> {
  const { data } = await client.post(`/api/stocks/${code}/alerts/preset`, { preset, on })
  return data
}

export async function deleteAlert(id: number): Promise<{ ok: boolean }> {
  const { data } = await client.delete(`/api/stocks/alerts/${id}`)
  return data
}
