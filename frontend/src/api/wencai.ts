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
  items: WencaiItem[]
}

export async function fetchWencai(): Promise<{ strategies: WencaiStrategy[] }> {
  const { data } = await client.get('/api/wencai')
  return data
}

export async function addWencaiToPool(stocks: { code: string; name: string }[]): Promise<{ ok: boolean; added: number; total: number }> {
  const { data } = await client.post('/api/wencai/add-to-pool', { stocks }, { timeout: 60000 })
  return data
}
