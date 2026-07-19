<script setup lang="ts">
// 全局顶栏「大盘风险红绿灯」(v1.7.563) — 监控看板中部的市场风险状态条绿色时几乎隐形、易被忽略,
// 提炼成全站可见的图形标识: 绿=安静小点, 黄/红=彩色药丸+呼吸闪烁+「谨慎/空仓」字样, 点击跳监控看板看详情。
// 数据同 MarketRiskBanner(/api/signals/market-risk), 5min 轮询, 切走标签页暂停。
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchMarketRisk, type MarketRiskRow } from '../../api/signals'
import { useVisiblePolling } from '../../composables/useVisiblePolling'

const router = useRouter()
const route = useRoute()
const latest = ref<MarketRiskRow | null>(null)

const state = computed(() => (latest.value?.state ?? '').toUpperCase())
const label = computed(() =>
  state.value === 'RED' ? '大盘空仓' : state.value === 'YELLOW' ? '大盘谨慎' : '大盘正常')
const tip = computed(() => {
  const l = latest.value
  if (!l) return ''
  const b = l.breadth_ma20 == null ? '—' : l.breadth_ma20.toFixed(0)
  const a = l.advance_ratio == null ? '—' : l.advance_ratio.toFixed(0)
  const base = `市场风险 ${label.value.slice(2)}（${l.trade_date}${l.source === 'intraday' ? ' · 盘中预升级' : ''}）\n涨跌比 ${a}% · 广度MA20 ${b}%`
  // v1.7.686: 旧提示写死「胜率30%均值-3.6%」(带前视偏差的旧回测), 改用 OOS 实测值
  return state.value === 'RED' ? `${base}\n实测此档买点单笔均 -2.3%（正常档 -0.5%），建议停开新仓` : `${base}\n点击查看监控看板详情`
})

async function load() {
  try {
    latest.value = (await fetchMarketRisk()).latest
  } catch {
    /* 顶栏静默 */
  }
}
useVisiblePolling(load, 300000)

function go() {
  if (route.path !== '/') router.push('/')
}
</script>

<template>
  <button v-if="latest" type="button" class="risk-light" :class="state.toLowerCase()"
          :title="tip" :aria-label="`${label}，点击查看监控看板`" @click="go">
    <span class="lamp" />
    <span class="txt">{{ label }}</span>
  </button>
</template>

<style scoped>
/* 融入顶栏状态母线 (v1.7.663 重设计): 去掉刺眼实心块, 统一为 圆点+彩色标签;
   红态保留脉冲圆点吸睛, 但不再是压过全栏的红方块。 */
.risk-light {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  line-height: 1.4;
  transition: opacity 0.15s;
}
.risk-light:hover { opacity: 0.75; }
.lamp { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

/* 绿: 安静小点 + 淡字 */
.risk-light.green .lamp { background: var(--success-fg); }
.risk-light.green .txt { color: var(--fg-subtle); }

/* 黄: 橙点 + 橙粗字 */
.risk-light.yellow .lamp { background: var(--warn-fg); }
.risk-light.yellow .txt { color: var(--warn-fg); font-weight: 700; }

/* 红: 脉冲红点 + 红粗字, 全站最醒目的状态但融入母线 */
.risk-light.red .lamp {
  background: var(--danger-fg);
  box-shadow: 0 0 0 0 rgba(210, 43, 43, 0.55);
  animation: risk-pulse 1.6s ease-in-out infinite;
}
.risk-light.red .txt { color: var(--danger-fg); font-weight: 700; }

@keyframes risk-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(210, 43, 43, 0.5); }
  50% { box-shadow: 0 0 0 5px rgba(210, 43, 43, 0); }
}

@media (max-width: 768px) {
  .lamp { width: 7px; height: 7px; }
}
</style>
