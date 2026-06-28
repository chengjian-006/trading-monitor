// 信号按股票分组 — StockTable / StockList / SignalSummaryBar 共用 (v1.7.407 瘦身)
import { computed } from 'vue'
import type { Signal } from '../types'

export function useSignalGrouping(signalsGetter: () => Signal[]) {
  const signalsByCode = computed(() => {
    const map = new Map<string, Signal[]>()
    for (const s of signalsGetter()) {
      if (!map.has(s.code)) map.set(s.code, [])
      map.get(s.code)!.push(s)
    }
    return map
  })

  function getSignals(code: string): Signal[] {
    return signalsByCode.value.get(code) || []
  }

  return { signalsByCode, getSignals }
}
