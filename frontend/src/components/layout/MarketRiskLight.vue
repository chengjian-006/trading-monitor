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
  return state.value === 'RED' ? `${base}\nRED期内信号胜率30%均值-3.6%, 建议空仓` : `${base}\n点击查看监控看板详情`
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
.risk-light {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 12px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  line-height: 1.4;
  transition: all 0.15s;
}
.lamp { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }

/* 绿: 安静小点 + 淡字, 深色顶栏/浅色手机头都可读 */
.risk-light.green .lamp { background: #16a34a; }
.risk-light.green .txt { color: #7f8b99; }
.risk-light.green:hover { border-color: rgba(127, 139, 153, 0.35); }

/* 黄/红: 彩色药丸 + 白字 + 呼吸闪烁, 全站醒目 */
.risk-light.yellow { background: #d97706; }
.risk-light.red { background: #dc2626; }
.risk-light.yellow .txt,
.risk-light.red .txt { color: #fff; font-weight: 700; }
.risk-light.yellow .lamp,
.risk-light.red .lamp { background: #fff; animation: risk-blink 1.6s ease-in-out infinite; }
.risk-light.red { animation: risk-glow 1.6s ease-in-out infinite; }

@keyframes risk-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
@keyframes risk-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.55); }
  50% { box-shadow: 0 0 0 6px rgba(220, 38, 38, 0); }
}

@media (max-width: 768px) {
  .risk-light { padding: 2px 8px; font-size: 11px; }
  .lamp { width: 8px; height: 8px; }
}
</style>
