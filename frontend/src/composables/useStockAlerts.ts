import { ref } from 'vue'
import { fetchAllAlerts, type StockAlert } from '../api/stocks'

// 模块级单例: 股票池所有自定义预警的汇总, 供桌面表格与手机卡片共享铃铛状态。
const alerts = ref<StockAlert[]>([])
let loaded = false
let inflight: Promise<void> | null = null

export interface AlertSummary {
  active: number
  triggered: number
}

async function load(force = false): Promise<void> {
  if (inflight) return inflight
  if (loaded && !force) return
  inflight = (async () => {
    try {
      alerts.value = await fetchAllAlerts()
      loaded = true
    } catch {
      // 静默: 未配置/网络问题不打断股票池
    } finally {
      inflight = null
    }
  })()
  return inflight
}

export function useStockAlerts() {
  function summaryFor(code: string): AlertSummary | null {
    let active = 0, triggered = 0
    for (const a of alerts.value) {
      if (a.code !== code) continue
      if (a.status === 'triggered') triggered++
      else if (a.enabled) active++
    }
    if (active === 0 && triggered === 0) return null
    return { active, triggered }
  }

  return {
    alerts,
    loadAlerts: load,
    reloadAlerts: () => load(true),
    summaryFor,
  }
}
