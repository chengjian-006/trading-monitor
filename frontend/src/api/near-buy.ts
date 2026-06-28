import client from './client'

export interface NearBuyHit {
  kind: '触发' | '接近'
  buy_id: string
  buy_name: string
  note: string
  miss: string[]
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
