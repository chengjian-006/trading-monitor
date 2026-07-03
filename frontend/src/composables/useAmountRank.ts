// 全市场成交额排名 top100 (code→名次), 每 2 分钟刷新一次。
// 给股票池"成交额排名"列: 前100显示名次, 否则"100名外"。
import { ref } from 'vue'
import { fetchAmountRank } from '../api/market-report'
import { useVisiblePolling } from './useVisiblePolling'

const REFRESH_MS = 120_000

export function useAmountRank() {
  const rankMap = ref<Record<string, number>>({})

  async function refresh() {
    const m = await fetchAmountRank()
    if (m && Object.keys(m).length) rankMap.value = m   // 失败/空不覆盖, 保留上一份
  }

  useVisiblePolling(refresh, REFRESH_MS)   // v1.7.571: 切走标签页暂停

  return { rankMap }
}
