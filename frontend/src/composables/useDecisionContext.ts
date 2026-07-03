// 决策快查卡上下文 - v1.7.x
// 维护 大盘 regime + 信号真实胜率 + 板块实时强度 三份背景数据, 5min 刷新一次,
// 暴露 computeDecision(stock, buySignals) 给 StockTable 名称色块 / 展开决策卡共用.
import { ref, watch } from 'vue'
import { useVisiblePolling } from './useVisiblePolling'
import { fetchRegime, type RegimeData } from '../api/market-report'
import { fetchSignalOutcomeStats, type SignalOutcomeStatsItem } from '../api/signals'
import { fetchSectorStrengthBatch, type SectorStrength } from '../api/sector'
import { useStockStore } from '../stores/stock'
import type { Stock, Signal } from '../types'

export interface DecisionReason {
  text: string
  sign: 'pos' | 'neg' | 'neu'   // ✓ 加分 / ✗ 减分 / · 中性提示
}

export interface DecisionVerdict {
  action: 'execute' | 'light' | 'wait' | 'avoid'
  label: string         // "建议执行" / "轻仓试单" / "观望确认" / "回避"
  size: string           // "20-30%" / "5-10%" / "—" / "0%"
  color: string          // 边框/标签颜色
  reasons: DecisionReason[]
}

const REFRESH_INTERVAL_MS = 5 * 60 * 1000

export function useDecisionContext() {
  const regimeData = ref<RegimeData | null>(null)
  const outcomeStatsMap = ref<Record<string, SignalOutcomeStatsItem>>({})
  const sectorStrengthMap = ref<Record<string, SectorStrength>>({})
  const stockStore = useStockStore()

  async function refreshGlobal() {
    try {
      const [rg, os] = await Promise.all([
        fetchRegime().catch(() => null),
        fetchSignalOutcomeStats(90).catch(() => ({} as Record<string, SignalOutcomeStatsItem>)),
      ])
      if (rg) regimeData.value = rg
      if (os) outcomeStatsMap.value = os
    } catch { /* silent — 后台轮询, 失败保留上一份 */ }
  }

  async function refreshSectorStrength() {
    // 仅拉 focused + hold 票, 避免一次性 50+ industry 同时拉
    const codes = stockStore.stocks
      .filter(s => s.focused || s.status === 'hold')
      .map(s => s.code)
      .slice(0, 50)
    if (!codes.length) return
    try {
      const m = await fetchSectorStrengthBatch(codes)
      if (m) sectorStrengthMap.value = m
    } catch { /* silent */ }
  }

  // v1.7.571: 切走标签页暂停(regime+胜率+板块强度三份 5min 背景数据), 切回补刷; 卸载自动清理。
  useVisiblePolling(() => {
    refreshGlobal()
    refreshSectorStrength()
  }, REFRESH_INTERVAL_MS)

  // 股票池首次加载后立即拉一次板块强度 (避免等到 5min 后才有数据)
  let sectorSeeded = false
  watch(() => stockStore.stocks.length, (n) => {
    if (n > 0 && !sectorSeeded) {
      sectorSeeded = true
      refreshSectorStrength()
    }
  }, { immediate: true })

  function computeDecision(stock: Stock, buySignals: Signal[]): DecisionVerdict | null {
    if (!buySignals.length) return null
    const reasons: DecisionReason[] = []
    let score = 0
    const pos = (text: string) => reasons.push({ text, sign: 'pos' })
    const neg = (text: string) => reasons.push({ text, sign: 'neg' })
    const neu = (text: string) => reasons.push({ text, sign: 'neu' })

    // 1) 大盘 regime
    const rg = regimeData.value
    if (rg) {
      if (rg.regime === 'friendly') { score += 30; pos(`大盘友好 ${rg.score} 分`) }
      else if (rg.regime === 'neutral') { score += 10; neu(`大盘中性 ${rg.score} 分`) }
      else { score -= 40; neg(`大盘危险 ${rg.score} 分, 不宜买入`) }
    }

    // 2) 信号胜率: 取触发的买点信号中真实胜率最高那个
    let bestRate: number | null = null
    let bestRateSignal = ''
    let evaluatedTotal = 0
    for (const sig of buySignals) {
      const stat = outcomeStatsMap.value[sig.signal_id]
      if (stat && stat.evaluated >= 3) {
        if (bestRate == null || stat.success_rate > bestRate) {
          bestRate = stat.success_rate
          bestRateSignal = stat.signal_name
          evaluatedTotal = stat.evaluated
        }
      }
    }
    if (bestRate != null) {
      if (bestRate >= 60) { score += 30; pos(`${bestRateSignal} 真实胜率 ${bestRate}% (${evaluatedTotal} 笔)`) }
      else if (bestRate >= 45) { score += 15; pos(`${bestRateSignal} 真实胜率 ${bestRate}% (${evaluatedTotal} 笔)`) }
      else if (bestRate >= 30) { neu(`${bestRateSignal} 真实胜率 ${bestRate}% 偏低 (${evaluatedTotal} 笔)`) }
      else { score -= 15; neg(`${bestRateSignal} 真实胜率仅 ${bestRate}% (${evaluatedTotal} 笔), 历史不利`) }
    } else {
      neu('该信号样本不足 (<3 笔已评估), 胜率参考性弱')
    }

    // 3) 板块实时强度 (v1.7.x: 替换原 sector_rank 死字段)
    const ss = sectorStrengthMap.value[stock.code]
    if (ss && ss.pct_today != null) {
      const sectorPct = ss.pct_today
      const leaderPct = ss.leader_pct ?? 0
      const selfPct = ss.self_pct ?? (stock.pct_change ?? 0)
      const leaderUp = leaderPct >= 9.5  // 真涨停近似

      // 3a) 板块涨幅档位
      if (sectorPct >= 3 && leaderUp) { score += 15; pos(`${ss.industry} 强势 +${sectorPct.toFixed(2)}% (龙头${ss.leader_name} 涨停)`) }
      else if (sectorPct >= 3) { score += 10; pos(`${ss.industry} 强势 +${sectorPct.toFixed(2)}% (龙头${ss.leader_name} ${leaderPct >= 0 ? '+' : ''}${leaderPct.toFixed(2)}%)`) }
      else if (sectorPct >= 1) { score += 5; pos(`${ss.industry} 走强 +${sectorPct.toFixed(2)}%`) }
      else if (sectorPct <= -2) { score -= 15; neg(`${ss.industry} 塌方 ${sectorPct.toFixed(2)}% (板块跌停)`) }
      else if (sectorPct <= -1) { score -= 8; neg(`${ss.industry} 走弱 ${sectorPct.toFixed(2)}%`) }
      else { neu(`${ss.industry} 平稳 ${sectorPct >= 0 ? '+' : ''}${sectorPct.toFixed(2)}%`) }

      // 3b) 自身在板块的位置
      if (ss.self_rank === 1) { score += 8; pos('板块第一 (本票就是龙头)') }
      else if (ss.self_rank && ss.self_rank <= 3) { score += 4; pos(`板块前 3 (#${ss.self_rank})`) }
      else if (leaderPct > 0 && selfPct < leaderPct * 0.4) { score -= 3; neg(`相对龙头明显掉队 (本 ${selfPct.toFixed(2)}% vs 龙头 ${leaderPct.toFixed(2)}%)`) }
    } else if (ss) {
      neu(`${ss.industry || '板块'} 实时数据拉取中...`)
    }
    // 没有 industry 字段的票 / 后端未返回 → 不打分, 不写 reason

    // 4) 当日涨幅: 避免追高
    const pct = stock.pct_change ?? 0
    if (pct >= 7) { score -= 15; neg(`当日已涨 ${pct.toFixed(2)}%, 追高风险大`) }
    else if (pct >= 4) { score -= 5; neg(`当日已涨 ${pct.toFixed(2)}%, 已不便宜`) }

    // 5) 加仓提示
    if (stock.status === 'hold') neu('已持仓: 此为加仓评估')

    // 综合判定
    let action: DecisionVerdict['action']
    let label: string
    let size: string
    let color: string
    if (score >= 50) { action = 'execute'; label = '建议执行'; size = '20-30%'; color = '#16a34a' }
    else if (score >= 25) { action = 'light'; label = '轻仓试单'; size = '5-10%'; color = '#0284c7' }
    else if (score >= 0)  { action = 'wait';  label = '观望确认'; size = '—';      color = '#d97706' }
    else                  { action = 'avoid'; label = '回避';    size = '0%';     color = '#dc2626' }
    return { action, label, size, color, reasons }
  }

  return { regimeData, outcomeStatsMap, sectorStrengthMap, computeDecision }
}
