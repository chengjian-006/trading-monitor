import client from './client'

// 同花顺特有标签(归一化后, 视查询而定, 可能缺省)
export interface WencaiExtra {
  tech_pattern?: string   // 技术形态
  buy_signal?: string     // 同花顺买入信号汇总
  concepts?: string       // 所属概念
  industry?: string       // 所属同花顺行业
  turnover?: number       // 换手率%
  amount?: number         // 成交额(元)
  free_cap?: number       // 流通市值(元)
}

export interface WencaiItem {
  code: string
  name: string
  price: number | null
  pct_change: number | null
  extra: WencaiExtra
}

export interface WencaiStrategy {
  strategy_id: string
  strategy_name: string
  query_text: string
  trade_date: string | null
  computed_at: string | null
  stock_count: number
  last_error: string
  is_custom: boolean
  query_id: number | null
  items: WencaiItem[]
}

export interface WencaiQuery {
  id: number
  user_id: number
  name: string
  query_text: string
  enabled: number
  sort_order: number
}

export async function fetchWencai(): Promise<{ strategies: WencaiStrategy[] }> {
  const { data } = await client.get('/api/wencai')
  return data
}

export async function addWencaiToPool(stocks: { code: string; name: string }[]): Promise<{ ok: boolean; added: number; total: number }> {
  const { data } = await client.post('/api/wencai/add-to-pool', { stocks }, { timeout: 60000 })
  return data
}

// 即时搜索(不保存), 后端会跑 pywencai, 给足超时
export async function searchWencai(query: string): Promise<{ query: string; stock_count: number; items: WencaiItem[] }> {
  const { data } = await client.post('/api/wencai/search', { query }, { timeout: 40000 })
  return data
}

// 手工触发: 跑预置+本人启用的全部语句刷新候选(串行, 给足超时)
export async function scanWencai(): Promise<{ ok: boolean; total: number; succeeded: number; failed: string[] }> {
  const { data } = await client.post('/api/wencai/scan', {}, { timeout: 120000 })
  return data
}

export async function listWencaiQueries(): Promise<{ queries: WencaiQuery[] }> {
  const { data } = await client.get('/api/wencai/queries')
  return data
}

// 新增常驻语句(后端会立即跑一次出榜, 给足超时)
export async function createWencaiQuery(name: string, query: string): Promise<{ ok: boolean; id: number; run: { ok: boolean; stock_count: number; error: string } }> {
  const { data } = await client.post('/api/wencai/queries', { name, query }, { timeout: 40000 })
  return data
}

export async function updateWencaiQuery(id: number, params: { name?: string; query?: string; enabled?: number }): Promise<{ ok: boolean }> {
  const { data } = await client.put(`/api/wencai/queries/${id}`, params, { timeout: 40000 })
  return data
}

export async function deleteWencaiQuery(id: number): Promise<{ ok: boolean }> {
  const { data } = await client.delete(`/api/wencai/queries/${id}`)
  return data
}
