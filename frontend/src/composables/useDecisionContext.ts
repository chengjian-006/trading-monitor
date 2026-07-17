// 决策快查卡上下文 - v1.7.x
// 维护 大盘 regime + 信号真实胜率 两份背景数据, 5min 刷新一次,
// 暴露 computeDecision(stock, buySignals) 给 StockTable 名称色块 / 展开决策卡共用.
// v1.7.640: 板块强弱维度移除(用户拍板) — 东财板块接口对生产IP封禁, 该维度每次都是
//   44发全失败的废调用(2~3s+日志刷屏), 数据拿不到打分恒空转, 整层砍掉。
import { ref } from 'vue'
import { useVisiblePolling } from './useVisiblePolling'
import { fetchRegime, type RegimeData } from '../api/market-report'
import { fetchSignalOutcomeStats, type SignalOutcomeStatsItem } from '../api/signals'
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

  // v1.7.571: 切走标签页暂停(regime+胜率两份 5min 背景数据), 切回补刷; 卸载自动清理。
  useVisiblePolling(() => {
    refreshGlobal()
  }, REFRESH_INTERVAL_MS)

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

    // 3) 板块强弱维度已移除 (v1.7.640, 东财封禁致数据恒空, 见文件头注释)

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
    // 机构级 (v1.7.650): 决策色对齐冷调 Token 十六进制值(与 TagLegendButton 的 var 图例像素级一致)
    // execute=success#1A8F4E / light=accent#1668DC / wait=warn#B7791F / avoid=danger#D22B2B
    if (score >= 50) { action = 'execute'; label = '建议执行'; size = '20-30%'; color = '#1A8F4E' }
    else if (score >= 25) { action = 'light'; label = '轻仓试单'; size = '5-10%'; color = '#1668DC' }
    else if (score >= 0)  { action = 'wait';  label = '观望确认'; size = '—';      color = '#B7791F' }
    else                  { action = 'avoid'; label = '回避';    size = '0%';     color = '#D22B2B' }
    return { action, label, size, color, reasons }
  }

  return { regimeData, outcomeStatsMap, computeDecision }
}
