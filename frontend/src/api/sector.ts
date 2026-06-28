// 板块强度 API - v1.7.x
import client from './client'

export interface SectorStrength {
  industry: string
  pct_today: number | null      // 板块今日涨幅(%)
  leader_name: string           // 板块龙头名
  leader_pct: number | null     // 板块龙头涨幅(%)
  self_pct: number | null       // 自己今日涨跌(%)
  self_rank: number | null      // 自己在板块 top 5 内的名次, 不在 top 5 → null
}

/** 一次拉多只票的板块强度. 后端会去重 industry 并发拉, 30s 缓存. */
export async function fetchSectorStrengthBatch(codes: string[]): Promise<Record<string, SectorStrength>> {
  if (!codes.length) return {}
  const { data } = await client.get('/api/sector/strength-batch', { params: { codes: codes.join(',') } })
  return data
}

// v1.7.174: 监控看板行业板块涨跌排行热力图 — 后端 60s 缓存
export interface SectorRankItem {
  rank: number
  industry: string
  bk_code: string
  pct_today: number
}
export interface SectorRanking {
  ranking: SectorRankItem[]
  up_count: number
  down_count: number
  total: number
}

export async function fetchSectorRanking(topN = 100): Promise<SectorRanking> {
  const { data } = await client.get('/api/sector/ranking', { params: { top_n: topN } })
  return data
}
