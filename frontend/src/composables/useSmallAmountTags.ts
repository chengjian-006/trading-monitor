// "小额" 标签评估 - v1.7.x
// 与 quote 3s 刷新解耦, 每 10 分钟独立评估今日预估全天成交额 < 阈值的票.
// 用户首次拿到 stocks 数据时立即评估一次, 之后定期复算.
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import type { Stock } from '../types'
import { estimateFullDayAmount } from '../utils/intradayEstimator'

export const SMALL_AMOUNT_THRESHOLD_YUAN = 2_000_000_000  // 20亿
const REFRESH_INTERVAL_MS = 10 * 60 * 1000

export function useSmallAmountTags(stocks: { value: Stock[] }) {
  const smallAmountMap = ref<Map<string, number>>(new Map())
  let timer: ReturnType<typeof setInterval> | null = null
  let initialized = false

  function recompute() {
    const next = new Map<string, number>()
    const now = new Date()
    for (const s of stocks.value) {
      const est = estimateFullDayAmount(s.amount, now)
      if (est != null && est < SMALL_AMOUNT_THRESHOLD_YUAN) {
        next.set(s.code, est)
      }
    }
    smallAmountMap.value = next
  }

  watch(() => stocks.value.length, (n) => {
    if (n > 0 && !initialized) {
      initialized = true
      recompute()
    }
  }, { immediate: true })

  onMounted(() => {
    timer = setInterval(recompute, REFRESH_INTERVAL_MS)
  })
  onBeforeUnmount(() => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  })

  return { smallAmountMap }
}
