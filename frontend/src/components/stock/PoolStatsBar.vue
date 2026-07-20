<script setup lang="ts">
// 自选池实时统计栏 — 纯前端聚合已加载的自选股行(随 quote_refresh 每3s自动刷新)。
// 当日: 平均涨跌幅/涨跌平家数/涨停跌停家数/红盘占比。
// 无价票(price/pct 为空)不计入均值与涨跌家数, 若有则栏尾标注只数。
import { computed } from 'vue'
import type { Stock } from '../../types'
import { isLimitUp, isLimitDown } from '../../utils/limitBoard'

const props = defineProps<{ stocks: Stock[] }>()

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}
function pctColor(v: number | null): string {
  if (v == null) return 'var(--text-secondary, #888)'
  return v >= 0 ? 'var(--red)' : 'var(--green)'
}

const stats = computed(() => {
  const rows = props.stocks || []
  // 当日: 以 pct_change 非空为"有价"
  const today = rows.filter(s => s.pct_change != null)
  let up = 0, down = 0, flat = 0, limitUp = 0, limitDown = 0, sum = 0
  for (const s of today) {
    const pct = s.pct_change as number
    sum += pct
    if (pct > 0) up++
    else if (pct < 0) down++
    else flat++
    if (isLimitUp(pct, s.code, s.name, s.limit_up_days)) limitUp++
    else if (isLimitDown(pct, s.code, s.name)) limitDown++
  }
  const avgToday = today.length ? sum / today.length : null
  const upRatio = today.length ? Math.round((up / today.length) * 100) : null

  const noPrice = rows.length - today.length
  return { total: today.length, up, down, flat, limitUp, limitDown, avgToday, upRatio, noPrice }
})
</script>

<template>
  <div class="pool-stats-bar" v-if="stats.total > 0">
    <div class="stat-group">
      <span class="grp-label">当日</span>
      <span class="stat"><i>均</i><b :style="{ color: pctColor(stats.avgToday) }">{{ fmtPct(stats.avgToday) }}</b></span>
      <span class="stat"><i class="up">涨</i><b class="up">{{ stats.up }}</b></span>
      <span class="stat"><i class="down">跌</i><b class="down">{{ stats.down }}</b></span>
      <span class="stat"><i>平</i><b>{{ stats.flat }}</b></span>
      <span class="stat"><i class="up">涨停</i><b class="up">{{ stats.limitUp }}</b></span>
      <span class="stat"><i class="down">跌停</i><b class="down">{{ stats.limitDown }}</b></span>
      <span class="stat"><i>红盘</i><b>{{ stats.upRatio == null ? '—' : stats.upRatio + '%' }}</b></span>
    </div>
    <span v-if="stats.noPrice > 0" class="stat muted">含 {{ stats.noPrice }} 只无价</span>
  </div>
</template>

<style scoped>
.pool-stats-bar {
  display: flex;
  flex-wrap: nowrap;      /* 强制同一行, 窄了横向滚而非折成两行 */
  overflow-x: auto;
  align-items: center;
  gap: 6px 14px;
  padding: 8px 12px;
  border: 1px solid var(--border-color, #eee);
  border-radius: 8px;
  background: var(--card-bg, #fafafa);
  font-size: 13px;
  line-height: 1.4;
}
.pool-stats-bar::-webkit-scrollbar { height: 4px; }
.pool-stats-bar::-webkit-scrollbar-thumb { background: var(--border-color, #ddd); border-radius: 4px; }
.stat-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
}
.grp-label {
  font-weight: 600;
  color: var(--text-secondary, #888);
  margin-right: 2px;
}
.stat {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  white-space: nowrap;
}
.stat i {
  font-style: normal;
  color: var(--text-secondary, #888);
}
.stat b {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.stat i.up, .stat b.up { color: var(--red); }
.stat i.down, .stat b.down { color: var(--green); }
.stat.muted, .stat.muted b { color: var(--text-secondary, #aaa); font-weight: 400; }
/* 移动端: 允许换行, 字号收紧 */
@media (max-width: 767px) {
  .pool-stats-bar { flex-wrap: wrap; overflow-x: visible; gap: 6px 10px; font-size: 12px; padding: 8px 10px; }
  .stat-group { gap: 4px 10px; }
}
</style>
