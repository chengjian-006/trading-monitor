// 大盘指数条(股票池顶部)的取数助手 —— 与监控看板同源 (v1.7.736)
// 数据源就是 fetchMarketOverview() 那份 30s DB 快照(indices ∪ global_indices), 和监控看板
// MarketIndexOverview.vue 用的完全是同一份, 故名称与涨跌幅两处必然一致。
// A股 5 个指数直接取 overview.indices(后端 get_market_indices 定死顺序: 上证/深证/创业板/科创指数/全A);
// 港股 2 个从 overview.global_indices 里按名过滤追加(那里还混着美股/欧洲/日本, 只挑恒生这两个)。
import type { MarketOverview, IndexQuote } from '../api/market-report'

// 要追加到 A 股后面的港股指数(顺序即展示顺序)
export const HK_STRIP_INDICES = ['恒生指数', '恒生科技'] as const

export interface StripItem {
  name: string
  pct: number
}

export function buildIndexStrip(ov: MarketOverview | null): StripItem[] {
  if (!ov) return []
  const aShare: StripItem[] = (ov.indices || []).map((i: IndexQuote) => ({
    name: i.name,
    pct: i.pct_change,
  }))
  const hk: StripItem[] = HK_STRIP_INDICES.map((nm) => {
    const g = (ov.global_indices || []).find((x: IndexQuote) => x.name === nm)
    return g ? { name: g.name, pct: g.pct_change } : null
  }).filter((x): x is StripItem => x !== null)
  return [...aShare, ...hk]
}
