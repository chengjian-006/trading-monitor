import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/paper-trading'

export const usePaperStore = defineStore('paper', () => {
  const summary = ref<api.PaperSummary | null>(null)
  const positions = ref<any[]>([])
  const trades = ref<any[]>([])
  const equity = ref<any[]>([])
  const modelStats = ref<any[]>([])
  const loading = ref(false)
  // 当前查看的账户: default=模拟账户 / unlimited=无限子弹
  const accountKey = ref<api.AccountKey>('default')

  function setAccount(key: api.AccountKey) { accountKey.value = key }

  async function loadAll() {
    loading.value = true
    const key = accountKey.value
    try {
      const [s, p, t, e, m] = await Promise.all([
        api.fetchPaperSummary(key),
        api.fetchPaperPositions(key),
        api.fetchPaperTrades(100, 0, key),
        api.fetchPaperEquity(key),
        api.fetchPaperModelStats(key),
      ])
      summary.value = s
      positions.value = p
      trades.value = t
      equity.value = e
      modelStats.value = m
    } finally {
      loading.value = false
    }
  }

  // 轻量刷新: 只刷概览+持仓(带实时现价), 供页面盘中轮询用, 不重拉流水/曲线
  async function refreshLive() {
    const key = accountKey.value
    const [s, p] = await Promise.all([api.fetchPaperSummary(key), api.fetchPaperPositions(key)])
    summary.value = s
    positions.value = p
  }

  return { summary, positions, trades, equity, modelStats, loading, accountKey, setAccount, loadAll, refreshLive }
})
