<script setup lang="ts">
// 大盘风险全局顶栏横幅 (v1.7.630) — 对标灾难横幅规范: 风险(谨慎/空仓)期间全站每页常驻,
// 与推送侧横幅同款文案+时间锚点(「13:11起」, 状态页 since 模式), 点击跳监控看板看详情。
// 与 MarketRiskLight(顶栏小药丸)互补: 药丸是常态化状态灯, 本横幅只在黄/红档出现、通栏压顶。
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchMarketRisk, type MarketRiskRow } from '../../api/signals'
import { useVisiblePolling } from '../../composables/useVisiblePolling'

const router = useRouter()
const route = useRoute()
const latest = ref<MarketRiskRow | null>(null)

const state = computed(() => (latest.value?.state ?? '').toUpperCase())
const active = computed(() => state.value === 'RED' || state.value === 'YELLOW')

// 时间锚点: 今日→「13:11起」, 往日→「7月15日 16:40起」; 解析失败静默省略
const since = computed(() => {
  const raw = latest.value?.updated_at
  if (!raw) return ''
  const s = String(raw).replace('T', ' ')
  const datePart = s.slice(0, 10)
  const hm = s.slice(11, 16)
  if (!/^\d{2}:\d{2}$/.test(hm)) return ''
  const d = new Date()
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  if (datePart === today) return `（${hm}起）`
  const m = parseInt(datePart.slice(5, 7), 10)
  const day = parseInt(datePart.slice(8, 10), 10)
  return `（${m}月${day}日 ${hm}起）`
})

const text = computed(() =>
  state.value === 'RED'
    ? `🚨 大盘空仓中${since.value} —— 停开新仓、别抄底、先保命`
    : `⚠️ 大盘谨慎中${since.value} —— 控制仓位、别追高`)

async function load() {
  try {
    latest.value = (await fetchMarketRisk()).latest
  } catch {
    /* 顶栏静默 */
  }
}
useVisiblePolling(load, 120000)

function go() {
  if (route.path !== '/') router.push('/')
}
</script>

<template>
  <button v-if="active" type="button" class="risk-top-banner" :class="state.toLowerCase()"
          :aria-label="`${text}，点击查看监控看板`" @click="go">
    <span class="banner-text">{{ text }}</span>
    <span class="banner-more">看详情 ›</span>
  </button>
</template>

<style scoped>
.risk-top-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  padding: 8px 16px;
  border: none;
  cursor: pointer;
  font-size: 14px;
  font-weight: 700;
  color: #fff;
  line-height: 1.4;
  text-align: center;
}
.risk-top-banner.red {
  background: #dc2626;
  animation: risk-banner-pulse 2s ease-in-out infinite;
}
.risk-top-banner.yellow { background: #d97706; }
.banner-text { min-width: 0; }
.banner-more {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 500;
  opacity: 0.85;
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: 10px;
  padding: 1px 8px;
}
@keyframes risk-banner-pulse {
  0%, 100% { background: #dc2626; }
  50% { background: #b91c1c; }
}
@media (max-width: 768px) {
  .risk-top-banner { font-size: 12px; padding: 7px 10px; gap: 6px; }
  .banner-more { display: none; }
}
</style>
