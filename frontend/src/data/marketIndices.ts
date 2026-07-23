// 大盘指数条(股票池顶部)的取数助手 —— 与监控看板同源 (v1.7.736)
// 数据源就是 fetchMarketOverview() 那份 30s DB 快照(indices ∪ global_indices), 和监控看板
// MarketIndexOverview.vue 用的完全是同一份, 故名称与涨跌幅两处必然一致。
// A股 5 个指数直接取 overview.indices(后端 get_market_indices 定死顺序: 上证/深证/创业板/科创指数/全A);
// 港股 2 个从 overview.global_indices 里按名过滤追加(那里还混着美股/欧洲/日本, 只挑恒生这两个)。
import type { MarketOverview, IndexQuote } from '../api/market-report'

// 要追加到 A 股后面的港股指数(顺序即展示顺序)
export const HK_STRIP_INDICES = ['恒生指数', '恒生科技'] as const

// 分时缩略图取数用的行情代码 (v1.7.786): 与 /api/market-report/index-trends 快照的 key、
// 以及监控看板 MarketIndexOverview 的 SYMBOLS 完全同一套, 故两处走势必然一致。
// A 股按 overview.indices 的固定顺序位置对应(后端 get_market_indices 定死: 上证/深证/创业板/科创/全A);
// 港股按名称对应(它们混在 global_indices 里, 没有稳定位置)。
export const A_STRIP_KEYS = ['sh000001', 'sz399001', 'sz399006', 'sh000688', 'sz399317'] as const
export const HK_STRIP_KEYS: Record<string, string> = { 恒生指数: 'hkHSI', 恒生科技: 'hkHSTECH' }

export interface StripItem {
  name: string
  pct: number
  key: string   // 分时快照 key, 空串=没有对应走势(只显数字)
}

export function buildIndexStrip(ov: MarketOverview | null): StripItem[] {
  if (!ov) return []
  const aShare: StripItem[] = (ov.indices || []).map((i: IndexQuote, idx: number) => ({
    name: i.name,
    pct: i.pct_change,
    key: A_STRIP_KEYS[idx] || '',
  }))
  const hk: StripItem[] = HK_STRIP_INDICES.map((nm) => {
    const g = (ov.global_indices || []).find((x: IndexQuote) => x.name === nm)
    return g ? { name: g.name, pct: g.pct_change, key: HK_STRIP_KEYS[nm] || '' } : null
  }).filter((x): x is StripItem => x !== null)
  return [...aShare, ...hk]
}
