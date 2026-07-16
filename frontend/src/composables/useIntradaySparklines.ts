import { ref } from 'vue'
import { fetchBatchIntraday, type SparklineData } from '../api/kline'
import { useVisiblePolling } from './useVisiblePolling'

export function useIntradaySparklines(getCodes: () => string[]) {
  // v1.7.x: 按 code 合并而非整张替换 — 防止单只票偶发失败时
  // 上一轮成功的曲线被这一轮的空响应覆盖成 "-"
  const sparklineMap = ref<Record<string, SparklineData>>({})

  async function refresh() {
    const codes = getCodes()
    if (!codes.length) return
    try {
      const result = await fetchBatchIntraday(codes)
      // v1.7.643: 逐 code 原地增量更新, 不再整对象替换 — 整替会让全部行的迷你图
      // 依赖一起失效重渲(187个组件); 增量后只有真有新数据点的票重画。
      const map = sparklineMap.value
      for (const [code, data] of Object.entries(result)) {
        // 只在本轮拿到非空 trends 时覆盖; 失败/停牌返回的空 trends 保留上一轮值
        if (!data || !Array.isArray(data.trends) || data.trends.length < 2) continue
        const old = map[code]
        // 无变化守卫: 点数和末点价都没变(收盘后/停牌常态)就不动, 避免无谓重画
        if (old && old.trends.length === data.trends.length
          && old.trends[old.trends.length - 1]?.price === data.trends[data.trends.length - 1]?.price
          && old.pre_close === data.pre_close) continue
        map[code] = data
      }
      // 清理已经不在当前页面 codes 集合里的旧条目(避免内存堆积)
      const codeSet = new Set(codes)
      for (const code of Object.keys(map)) {
        if (!codeSet.has(code)) delete map[code]
      }
    } catch {
      // silent: 整批失败不动 sparklineMap, 旧曲线继续显示
    }
  }

  // v1.7.571: 切走标签页暂停(全池分时是最重的一个请求), 切回补刷; 卸载自动清理。
  useVisiblePolling(refresh, 30_000)

  return { sparklineMap, refreshSparklines: refresh }
}
