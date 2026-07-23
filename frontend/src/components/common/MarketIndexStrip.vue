<script setup lang="ts">
// 大盘指数条 (v1.7.736) — 股票池顶部的紧凑指数概览
// 显示 名称 + 涨跌幅 + 分时缩略图(上证/深证/创业板/科创/全A/恒生/恒生科技), 一条横排。
// 数据与监控看板「大盘指数概览」同源: 涨跌幅读 fetchMarketOverview() 那份 30s DB 快照,
// 分时读 fetchIndexTrends() 同一份快照里的 index_trends(纯读库, 不额外打行情源),
// 故数字与走势跟看板必然一致。取数逻辑收敛在 data/marketIndices.ts 的 buildIndexStrip()。
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { fetchMarketOverview, fetchIndexTrends, type MarketOverview, type IndexTrendData } from '../../api/market-report'
import { buildIndexStrip } from '../../data/marketIndices'
import { useResponsive } from '../../composables/useResponsive'
import MiniSparkline from '../chart/MiniSparkline.vue'

const overview = ref<MarketOverview | null>(null)
const trendsMap = ref<Record<string, IndexTrendData>>({})
let timer: number | null = null

const { isPhone } = useResponsive()
// 手机端窄一档, 保证一屏仍能横滑看完 7 个指数
const spkW = computed(() => (isPhone.value ? 60 : 76))
const spkH = computed(() => (isPhone.value ? 18 : 24))

const items = computed(() => buildIndexStrip(overview.value))

function trendOf(key: string): IndexTrendData | null {
  return (key && trendsMap.value[key]) || null
}

async function load() {
  try {
    const [ov, tr] = await Promise.all([
      fetchMarketOverview(),
      fetchIndexTrends().catch(() => ({} as Record<string, IndexTrendData>)),
    ])
    if (ov) overview.value = ov
    if (tr && Object.keys(tr).length) trendsMap.value = tr
  } catch { /* silent — 顶部小条, 拉不到就不显示, 不打扰主流程 */ }
}

function pctClass(p: number) { return p > 0 ? 'up' : p < 0 ? 'down' : '' }
function pctText(p: number) { return (p >= 0 ? '+' : '') + p.toFixed(2) + '%' }

onMounted(() => {
  load()
  // 30s 轮询, 与看板/概览条同频; 切走标签页跳过网络(省请求)
  timer = window.setInterval(() => { if (!document.hidden) load() }, 30_000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div v-if="items.length" class="index-strip">
    <div v-for="it in items" :key="it.name" class="idx-cell">
      <span class="idx-name">{{ it.name }}</span>
      <span class="idx-pct" :class="pctClass(it.pct)">{{ pctText(it.pct) }}</span>
      <!-- 分时缩略图: 虚线=昨收基准, 颜色跟涨跌幅同源(传 pct), 拿不到走势就留等高占位不跳版 -->
      <MiniSparkline
        v-if="trendOf(it.key)"
        :trends="trendOf(it.key)!.trends"
        :pre-close="trendOf(it.key)!.pre_close"
        :pct="it.pct"
        :width="spkW"
        :height="spkH"
      />
      <span v-else class="idx-spk-ph" :style="{ width: spkW + 'px', height: spkH + 'px' }" />
    </div>
  </div>
</template>

<style scoped>
.index-strip {
  display: flex;
  align-items: stretch;
  gap: 0;
  background: var(--bg-surface, #fff);
  border: 1px solid var(--border-default, #efeff5);
  border-radius: 6px;
  padding: 8px 4px;
  margin-bottom: 10px;
  overflow-x: auto;               /* 窄屏横滚, 不换行不挤压 */
  -webkit-overflow-scrolling: touch;
}
.idx-cell {
  flex: 1 0 auto;
  min-width: 92px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 2px 10px;
  border-right: 1px solid var(--border-muted, #f0f0f4);
}
.idx-cell:last-child { border-right: none; }
.idx-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text1, #1f2328);
  white-space: nowrap;
}
.idx-pct {
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.up { color: var(--up-fg, #D92B26); }
.down { color: var(--down-fg, #0F8A5F); }
/* 走势拿不到时的等高占位: 保证有图/无图两种状态条高一致, 不来回跳版 */
.idx-spk-ph { display: block; flex-shrink: 0; }

@media (max-width: 768px) {
  .index-strip { padding: 6px 2px; margin-bottom: 8px; }
  .idx-cell { min-width: 66px; padding: 2px 6px; gap: 2px; }
  .idx-name { font-size: 12px; }
  .idx-pct { font-size: 11px; }
}
</style>
