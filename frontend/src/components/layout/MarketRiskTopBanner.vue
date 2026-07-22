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
const sinceAt = ref<string | null>(null)
const sinceDays = ref(0)

const state = computed(() => (latest.value?.state ?? '').toUpperCase())
const active = computed(() => state.value === 'RED' || state.value === 'YELLOW')

// 时间锚点: 今日→「13:11起」, 往日→「7月8日 16:40起 · 已8个交易日」; 解析失败静默省略。
// v1.7.678: 锚点改用后端 since_at(当前状态连续段第一天), 不再用 latest.updated_at —
// EOD 每天 upsert 会刷新 updated_at, 状态没变横幅也天天显示「昨天16:40起」, 掩盖了连续空仓多久。
const since = computed(() => {
  const raw = sinceAt.value ?? latest.value?.updated_at
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
  const tail = sinceDays.value >= 2 ? ` · 已${sinceDays.value}个交易日` : ''
  return `（${m}月${day}日 ${hm}起${tail}）`
})

// v1.7.752: 档名空仓→危险 + 横幅带 0-100 风险分(有值才显示)
const score = ref<number | null>(null)
const scorePart = computed(() => (score.value == null ? '' : `（风险分 ${score.value}）`))
const text = computed(() =>
  state.value === 'RED'
    ? `🚨 大盘危险中${since.value}${scorePart.value} —— 停开新仓、别抄底、先保命`
    : `⚠️ 大盘谨慎中${since.value}${scorePart.value} —— 控制仓位、别追高`)

async function load() {
  try {
    const resp = await fetchMarketRisk()
    latest.value = resp.latest
    sinceAt.value = resp.since_at ?? null
    sinceDays.value = resp.since_days ?? 0
    score.value = resp.score ?? null
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
