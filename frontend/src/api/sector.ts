// 板块 API - v1.7.x
// v1.7.640: strength-batch(板块强弱)接口封装已随决策卡板块维度一起移除(东财封禁致数据恒空)。
import client from './client'

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
