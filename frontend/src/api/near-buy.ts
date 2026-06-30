import client from './client'

export interface NearBuyHit {
  kind: '触发' | '接近'
  buy_id: string
  buy_name: string
  note: string
  miss: string[]
  // 可视化差距(v1.7.536): 贴线度 = 1 - dist_pct/band_pct; 条件满足 = met/total
  dist_pct?: number   // 距均线/上沿 %(越小越贴)
  band_pct?: number   // 贴线带阈值 %(归一化用)
  met?: number        // 已满足条件数
  total?: number      // 总条件数
}

export interface NearBuyItem {
  code: string
  name: string
  status: string | null
  status_label: string
  price: number
  pct: number
  tier: number // 2=触发 1=接近
  dist: number // 距相关均线 %
  hits: NearBuyHit[]
}

export interface NearBuySnapshot {
  trade_date: string | null
  computed_at: string | null
  scanned: number
  near_count?: number
  items: NearBuyItem[]
}

export async function fetchNearBuy(): Promise<NearBuySnapshot> {
  const { data } = await client.get('/api/near-buy')
  return data as NearBuySnapshot
}
