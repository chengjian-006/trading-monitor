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
      <!-- v1.7.686: 原写「RED期内信号胜率30%均值-3.6%」「信号质量未显著下降」, 数字来自带
           前视偏差的旧回测且 YELLOW 那句与实测相反; 改用 OOS 独立样本实测值(2021-2025)。 -->
      <template v-if="isRed">5日均收益 {{ avg5 }}% · 广度MA20 {{ breadth }}% · 实测此档买点单笔均 -2.3%（正常档 -0.5%）{{ latest.source === 'intraday' ? ' · 盘中预升级, 收盘复核' : '' }}</template>
      <template v-else-if="isYellow">涨跌比 {{ advance }}% · 广度MA20 {{ breadth }}% · 实测此档买点单笔均 -1.8%（正常档 -0.5%）</template>
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
.risk-banner.red  { background: var(--danger-bg-muted); border: 1px solid var(--border-default); }
.risk-banner.yellow { background: var(--warn-bg-muted); border: 1px solid var(--border-default); }
.risk-banner.green { background: transparent; border: 1px solid transparent; }
.risk-title { font-weight: 600; color: var(--text1); white-space: nowrap; }
.risk-levels { display: flex; align-items: center; gap: 4px; }
.risk-arrow { color: var(--fg-subtle); font-size: 10px; }
.risk-lv {
  display: flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 12px;
  font-size: 11px; color: var(--fg-subtle); white-space: nowrap;
  transition: all 0.15s;
}
.risk-lv.active {
  font-weight: 700; color: var(--on-emphasis);
  background: var(--fg-subtle);
}
.risk-lv.active:has(.dot.green)  { background: var(--success-fg); }
.risk-lv.active:has(.dot.yellow) { background: var(--warn-fg); }
.risk-lv.active:has(.dot.red)    { background: var(--danger-fg); }
.dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dot.green  { background: var(--success-fg); }
.dot.yellow { background: var(--warn-fg); }
.dot.red    { background: var(--danger-fg); }
.risk-lv.active .dot { background: var(--on-emphasis); }
.risk-detail { color: var(--fg-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.red  .risk-detail { color: var(--danger-fg); }
.yellow .risk-detail { color: var(--warn-fg); }
</style>
