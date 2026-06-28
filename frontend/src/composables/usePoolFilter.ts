// 股票池客户端即时筛选 (v1.7.419): 盘中快筛胶囊 + 高级阈值面板, 全部在前端对已加载的池子做过滤。
// 输入: 股票列表 getter + 信号分组(Map<code, Signal[]>); 输出: 筛选状态 ref + filteredStocks 计算属性。
import { ref, computed, type Ref } from 'vue'
import type { Stock, Signal } from '../types'
import { pinyinInitials } from '../utils/pinyin'

export type StatusFilter = 'all' | 'hold' | 'watch'   // 全部 / 持仓 / 关注(focused)
export type UpDownFilter = 'all' | 'up' | 'down'       // 不限 / 上涨 / 下跌

export function usePoolFilter(
  stocksGetter: () => Stock[],
  signalsByCode: Ref<Map<string, Signal[]>>,
) {
  // 快捷胶囊
  const fStatus = ref<StatusFilter>('all')
  const fUpDown = ref<UpDownFilter>('all')
  const fHasBuy = ref(false)    // 今日有买点(direction buy/add)
  const fHasSell = ref(false)   // 今日有卖点(direction sell/reduce)
  const fLimitUp = ref(false)   // 涨停
  const fLianBan = ref(false)   // 连板(连续涨停≥2)

  // 均线位置 (v1.7.424)
  const fAboveMa20 = ref(false) // 站上20日线(现价 ≥ MA20)
  const fBelowMa20 = ref(false) // 未站上20日线(现价 < MA20)
  const fNearMa10 = ref(false)  // 贴近10日线(现价偏离 MA10 在 ±2% 内)
  const fNearMa60 = ref(false)  // 贴近60日线(现价偏离 MA60 在 ±2% 内)
  const MA_NEAR_PCT = 0.02      // 附近=偏离 ≤2%

  // 高级面板
  const advancedOpen = ref(false)
  const fTradeTypes = ref<string[]>([])          // 空=不限; ['short','mid','index']
  const fPctMin = ref<number | null>(null)        // 涨幅下限 %
  const fPctMax = ref<number | null>(null)        // 涨幅上限 %
  const fTurnoverMin = ref<number | null>(null)   // 换手率 ≥ %
  const fVolRatioMin = ref<number | null>(null)   // 量比 ≥
  const fBoardRankMax = ref<number | null>(null)  // 板块内名次 ≤ N(越小越强)
  const fPopRankMax = ref<number | null>(null)    // 人气榜名次 ≤ N
  const fIndustry = ref('')                        // 行业/题材包含
  const fKeyword = ref('')                         // 代码/名称包含

  const hasActiveFilter = computed(() =>
    fStatus.value !== 'all' || fUpDown.value !== 'all' ||
    fHasBuy.value || fHasSell.value || fLimitUp.value || fLianBan.value ||
    fAboveMa20.value || fBelowMa20.value || fNearMa10.value || fNearMa60.value ||
    fTradeTypes.value.length > 0 ||
    fPctMin.value != null || fPctMax.value != null ||
    fTurnoverMin.value != null || fVolRatioMin.value != null ||
    fBoardRankMax.value != null || fPopRankMax.value != null ||
    fIndustry.value.trim() !== '' || fKeyword.value.trim() !== '',
  )

  function reset() {
    fStatus.value = 'all'
    fUpDown.value = 'all'
    fHasBuy.value = false
    fHasSell.value = false
    fLimitUp.value = false
    fLianBan.value = false
    fAboveMa20.value = false
    fBelowMa20.value = false
    fNearMa10.value = false
    fNearMa60.value = false
    fTradeTypes.value = []
    fPctMin.value = null
    fPctMax.value = null
    fTurnoverMin.value = null
    fVolRatioMin.value = null
    fBoardRankMax.value = null
    fPopRankMax.value = null
    fIndustry.value = ''
    fKeyword.value = ''
  }

  function dirs(code: string): Set<string> {
    const set = new Set<string>()
    for (const s of signalsByCode.value.get(code) || []) set.add(s.direction)
    return set
  }

  const filteredStocks = computed(() => {
    const kw = fKeyword.value.trim().toLowerCase()
    const ind = fIndustry.value.trim().toLowerCase()
    return stocksGetter().filter((s) => {
      // 状态: 持仓=status hold; 关注=focused(与池顶部统计口径一致)
      if (fStatus.value === 'hold' && s.status !== 'hold') return false
      if (fStatus.value === 'watch' && !s.focused) return false

      const pct = s.pct_change ?? null
      // 涨跌
      if (fUpDown.value === 'up' && !(pct != null && pct > 0)) return false
      if (fUpDown.value === 'down' && !(pct != null && pct < 0)) return false
      // 涨停 / 连板
      if (fLimitUp.value && !((pct != null && pct >= 9.8) || (s.limit_up_days ?? 0) >= 1)) return false
      if (fLianBan.value && (s.limit_up_days ?? 0) < 2) return false

      // 均线位置 (v1.7.424): 现价相对 MA10/MA20/MA60 (null=无数据视为不满足)
      if (fAboveMa20.value && !(s.ma20 != null && s.price != null && s.price >= s.ma20)) return false
      if (fBelowMa20.value && !(s.ma20 != null && s.price != null && s.price < s.ma20)) return false
      if (fNearMa10.value && !(s.ma10 != null && s.price != null && s.ma10 > 0
            && Math.abs(s.price - s.ma10) / s.ma10 <= MA_NEAR_PCT)) return false
      if (fNearMa60.value && !(s.ma60 != null && s.price != null && s.ma60 > 0
            && Math.abs(s.price - s.ma60) / s.ma60 <= MA_NEAR_PCT)) return false

      // 信号(今日)
      if (fHasBuy.value || fHasSell.value) {
        const d = dirs(s.code)
        if (fHasBuy.value && !(d.has('buy') || d.has('add'))) return false
        if (fHasSell.value && !(d.has('sell') || d.has('reduce'))) return false
      }

      // 交易类型
      if (fTradeTypes.value.length && !fTradeTypes.value.includes(s.trade_type)) return false

      // 涨幅区间
      if (fPctMin.value != null && !(pct != null && pct >= fPctMin.value)) return false
      if (fPctMax.value != null && !(pct != null && pct <= fPctMax.value)) return false
      // 换手率 / 量比
      if (fTurnoverMin.value != null && !(s.turnover != null && s.turnover >= fTurnoverMin.value)) return false
      if (fVolRatioMin.value != null && !(s.volume_ratio != null && s.volume_ratio >= fVolRatioMin.value)) return false
      // 板块内名次 / 人气榜名次 (≤N, null 视为不满足)
      if (fBoardRankMax.value != null && !(s.board_rank != null && s.board_rank <= fBoardRankMax.value)) return false
      if (fPopRankMax.value != null && !(s.popularity_rank != null && s.popularity_rank <= fPopRankMax.value)) return false

      // 行业/题材包含
      if (ind) {
        const hay = `${s.industry ?? ''} ${s.concepts ?? ''}`.toLowerCase()
        if (!hay.includes(ind)) return false
      }
      // 代码 / 名称 / 名称拼音首字母 包含(输入 gzmt 命中 贵州茅台)
      if (kw) {
        const hay = `${s.code} ${s.name} ${pinyinInitials(s.name)}`.toLowerCase()
        if (!hay.includes(kw)) return false
      }
      return true
    })
  })

  return {
    fStatus, fUpDown, fHasBuy, fHasSell, fLimitUp, fLianBan,
    fAboveMa20, fBelowMa20, fNearMa10, fNearMa60,
    advancedOpen, fTradeTypes, fPctMin, fPctMax, fTurnoverMin, fVolRatioMin,
    fBoardRankMax, fPopRankMax, fIndustry, fKeyword,
    hasActiveFilter, filteredStocks, reset,
  }
}
