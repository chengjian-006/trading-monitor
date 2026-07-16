import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import type { Stock } from '../types'
import * as stockApi from '../api/stocks'

export type FlashDir = 'up' | 'down'
export type FlashFields = Partial<Record<'price' | 'pct_change' | 'speed', FlashDir>>

export const useStockStore = defineStore('stock', () => {
  const stocks = ref<Stock[]>([])
  const loading = ref(false)
  const flashMap = reactive(new Map<string, FlashFields>())
  let loaded = false
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let delayTimer: ReturnType<typeof setTimeout> | null = null
  let flashTimer: ReturnType<typeof setTimeout> | null = null
  let abortController: AbortController | null = null

  async function loadStocks(force = false) {
    if (loaded && !force) return
    loading.value = true
    try {
      stocks.value = await stockApi.fetchStocks()
      loaded = true
    } finally {
      loading.value = false
    }
  }

  function detectFlash(oldStock: Stock, newStock: Stock): FlashFields | null {
    const fields: FlashFields = {}
    let hasChange = false
    const checks: Array<{ key: 'price' | 'pct_change' | 'speed' }> = [
      { key: 'price' },
      { key: 'pct_change' },
      { key: 'speed' },
    ]
    for (const { key } of checks) {
      const oldVal = oldStock[key] as number | null | undefined
      const newVal = newStock[key] as number | null | undefined
      if (newVal != null && oldVal != null && newVal !== oldVal) {
        fields[key] = newVal > oldVal ? 'up' : 'down'
        hasChange = true
      }
    }
    return hasChange ? fields : null
  }

  function clearFlash() {
    flashMap.clear()
  }

  async function refreshQuotes() {
    if (!loaded) return
    abortController?.abort()
    abortController = new AbortController()
    try {
      const fresh = await stockApi.fetchStocks(abortController.signal)
      const map = new Map(stocks.value.map(s => [s.code, s]))
      const freshCodes = new Set(fresh.map(s => s.code))
      const existingCodes = new Set(stocks.value.map(s => s.code))

      // Detect changes for flash
      const newFlash = new Map<string, FlashFields>()
      for (const item of fresh) {
        const existing = map.get(item.code)
        if (existing) {
          const diff = detectFlash(existing, item)
          if (diff) newFlash.set(item.code, diff)
        }
      }

      // Remove stocks no longer in the list
      if (fresh.length !== stocks.value.length || [...existingCodes].some(c => !freshCodes.has(c))) {
        stocks.value = stocks.value.filter(s => freshCodes.has(s.code))
      }

      for (const item of fresh) {
        const existing = map.get(item.code)
        if (existing) {
          Object.assign(existing, item)
        } else {
          stocks.value.push(item)
        }
      }

      // Preserve server order — v1.7.643: 顺序没变就不动(原来每3s都in-place sort, 白触发一轮数组响应式)
      const orderMap = new Map(fresh.map((s, i) => [s.code, i]))
      const inOrder = stocks.value.every((s, i, arr) =>
        i === 0 || (orderMap.get(arr[i - 1].code) ?? 0) <= (orderMap.get(s.code) ?? 0))
      if (!inOrder) {
        stocks.value.sort((a, b) => (orderMap.get(a.code) ?? 0) - (orderMap.get(b.code) ?? 0))
      }

      // Apply flash
      if (newFlash.size > 0) {
        flashMap.clear()
        for (const [code, fields] of newFlash) {
          flashMap.set(code, fields)
        }
        if (flashTimer) clearTimeout(flashTimer)
        flashTimer = setTimeout(clearFlash, 1500)
      }
    } catch (e: any) {
      if (e?.code === 'ERR_CANCELED') return
    }
  }

  // v1.7.50: 盘外 30 分钟刷新 ± 抖动削峰
  //  交易时段:   3 秒
  //  非交易时段: 30 分钟基准 + 随机抖动 ±3min (1620s ~ 1980s)
  // 抖动让每次 tick 落在 30 分钟周期内随机时刻, 避免多客户端在 30 分钟边界集中请求
  function getPollInterval(): number {
    const now = new Date()
    const day = now.getDay()
    const inTradingHours = (() => {
      if (day === 0 || day === 6) return false
      const hm = now.getHours() * 60 + now.getMinutes()
      const inMorning = hm >= 9 * 60 + 25 && hm <= 11 * 60 + 30
      const inAfternoon = hm >= 13 * 60 && hm <= 15 * 60
      return inMorning || inAfternoon
    })()
    if (inTradingHours) return 3000
    // 1800s ± 180s
    const base = 1800000
    const jitter = Math.floor((Math.random() - 0.5) * 360000)
    return base + jitter
  }

  // 标签页切走时暂停发请求(定时器照转但跳过网络), 切回立即刷一次 —
  // 盘中3s高频轮询在后台标签页空转纯属浪费(还占后端行情通道)
  function onVisibilityChange() {
    if (!document.hidden) refreshQuotes()
  }

  function startPolling() {
    stopPolling()
    const tick = () => {
      if (!document.hidden) refreshQuotes()
      pollTimer = setTimeout(tick, getPollInterval())
    }
    delayTimer = setTimeout(tick, 1500)
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  function stopPolling() {
    if (delayTimer) {
      clearTimeout(delayTimer)
      delayTimer = null
    }
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }

  async function addStock(code: string, name: string, trade_type: string, status: string) {
    await stockApi.addStock({ code, name, trade_type, status })
    const fresh = await stockApi.fetchStocks()
    stocks.value = fresh
    loaded = true
  }

  async function removeStock(code: string) {
    await stockApi.deleteStock(code)
    stocks.value = stocks.value.filter((s) => s.code !== code)
  }

  async function updateStock(code: string, params: Record<string, string>) {
    await stockApi.updateStock(code, params)
  }

  // 手动拖拽排序: 把 srcCode 移到 targetCode 的位置, 本地即时重排 + 持久化 sort_order
  async function reorderTo(srcCode: string, targetCode: string) {
    const arr = [...stocks.value]
    const from = arr.findIndex(s => s.code === srcCode)
    const to = arr.findIndex(s => s.code === targetCode)
    if (from < 0 || to < 0 || from === to) return
    const [moved] = arr.splice(from, 1)
    arr.splice(to, 0, moved)
    stocks.value = arr
    try {
      await stockApi.reorderStocks(arr.map(s => s.code))
    } catch {
      // 失败不回滚本地顺序; 下次 loadStocks(force) 会回到服务器顺序
    }
  }

  // 置顶/置底: 把某票移到自定义顺序的首/末
  async function moveToEdge(code: string, edge: 'top' | 'bottom') {
    const arr = [...stocks.value]
    const i = arr.findIndex(s => s.code === code)
    if (i < 0) return
    const [moved] = arr.splice(i, 1)
    if (edge === 'top') arr.unshift(moved)
    else arr.push(moved)
    stocks.value = arr
    try {
      await stockApi.reorderStocks(arr.map(s => s.code))
    } catch {
      // 失败不回滚; 下次 loadStocks(force) 回到服务器顺序
    }
  }

  return { stocks, loading, flashMap, loadStocks, addStock, removeStock, updateStock, reorderTo, moveToEdge, startPolling, stopPolling }
})
