<script setup lang="ts">
// 市场风险两级预警状态条 — 监控看板 (v1.7.x 替代CashAlertBanner)
// GREEN=一行灰绿小字 / YELLOW=黄色提示 / RED=红色横幅空仓
import { ref, computed } from 'vue'
import { fetchMarketRisk, type MarketRiskRow } from '../../api/signals'
import { useVisiblePolling } from '../../composables/useVisiblePolling'

const latest = ref<MarketRiskRow | null>(null)

const riskState = computed(() => latest.value?.state ?? 'GREEN')
const isRed = computed(() => riskState.value === 'RED')
const isYellow = computed(() => riskState.value === 'YELLOW')
const isGreen = computed(() => riskState.value === 'GREEN')

const breadth = computed(() => {
  const v = latest.value?.breadth_ma20
  return v == null ? '—' : v.toFixed(0)
})
const advance = computed(() => {
  const v = latest.value?.advance_ratio
  return v == null ? '—' : v.toFixed(0)
})
const avg5 = computed(() => {
  const v = latest.value?.avg_ret_ma5
  return v == null ? '—' : v.toFixed(2)
})

async function load() {
  try {
    const data = await fetchMarketRisk()
    latest.value = data.latest
  } catch {
    /* 看板静默 */
  }
}

useVisiblePolling(load, 300000)
</script>

<template>
  <div v-if="latest" class="risk-banner" :class="riskState.toLowerCase()">
    <span class="risk-title">市场风险</span>
    <div class="risk-levels">
      <span class="risk-lv" :class="{ active: isGreen }">
        <span class="dot green" /> 正常
      </span>
      <span class="risk-arrow">→</span>
      <span class="risk-lv" :class="{ active: isYellow }">
        <span class="dot yellow" /> 谨慎
      </span>
      <span class="risk-arrow">→</span>
      <span class="risk-lv" :class="{ active: isRed }">
        <span class="dot red" /> 空仓
      </span>
    </div>
    <span class="risk-detail">
      <template v-if="isRed">5日均收益 {{ avg5 }}% · 广度MA20 {{ breadth }}% · RED期内信号胜率30%均值-3.6%{{ latest.source === 'intraday' ? ' · 盘中预升级, 收盘复核' : '' }}</template>
      <template v-else-if="isYellow">涨跌比 {{ advance }}% · 广度MA20 {{ breadth }}% · 信号质量未显著下降</template>
      <template v-else>涨跌比 {{ advance }}% · 广度MA20 {{ breadth }}% · {{ latest.trade_date }}</template>
    </span>
  </div>
</template>

<style scoped>
.risk-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  flex-wrap: wrap;
}
.risk-banner.red  { background: #fef2f2; border: 1px solid #fecaca; }
.risk-banner.yellow { background: #fffbeb; border: 1px solid #fde68a; }
.risk-banner.green { background: transparent; border: 1px solid transparent; }
.risk-title { font-weight: 600; color: var(--text1); white-space: nowrap; }
.risk-levels { display: flex; align-items: center; gap: 4px; }
.risk-arrow { color: #c4c9d1; font-size: 10px; }
.risk-lv {
  display: flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 12px;
  font-size: 11px; color: #94a3b8; white-space: nowrap;
  transition: all 0.15s;
}
.risk-lv.active {
  font-weight: 700; color: #fff;
  background: #94a3b8;
}
.risk-lv.active:has(.dot.green)  { background: #16a34a; }
.risk-lv.active:has(.dot.yellow) { background: #d97706; }
.risk-lv.active:has(.dot.red)    { background: #dc2626; }
.dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dot.green  { background: #16a34a; }
.dot.yellow { background: #d97706; }
.dot.red    { background: #dc2626; }
.risk-lv.active .dot { background: #fff; }
.risk-detail { color: #888; font-size: 11px; font-variant-numeric: tabular-nums; }
.red  .risk-detail { color: #b91c1c; }
.yellow .risk-detail { color: #92400e; }
</style>
