<script setup lang="ts">
// 自选池实时统计栏 (v1.7.781 改双行对照): 「当日」纯前端聚合已加载自选股行(随 quote_refresh 每3s刷),
// 「昨日」(上一交易日)同口径从后端 K线缓存现算, 两行对齐对照 + Δ 变化行(红绿按多空着色)。
import { computed, ref, watch } from 'vue'
import type { Stock } from '../../types'
import { isLimitUp, isLimitDown } from '../../utils/limitBoard'
import { fetchPoolBreadthYesterday, type PoolBreadth } from '../../api/stocks'

const props = defineProps<{ stocks: Stock[] }>()

const stats = computed(() => {
  const rows = props.stocks || []
  const today = rows.filter(s => s.pct_change != null)   // 以 pct_change 非空为"有价"
  let up = 0, down = 0, flat = 0, limitUp = 0, limitDown = 0, sum = 0
  for (const s of today) {
    const pct = s.pct_change as number
    sum += pct
    if (pct > 0) up++
    else if (pct < 0) down++
    else flat++
    if (isLimitUp(pct, s.code, s.name)) limitUp++
    else if (isLimitDown(pct, s.code, s.name)) limitDown++
  }
  const avgToday = today.length ? sum / today.length : null
  const upRatio = today.length ? Math.round((up / today.length) * 100) : null
  const noPrice = rows.length - today.length
  return { total: today.length, up, down, flat, limitUp, limitDown, avgToday, upRatio, noPrice }
})

// ── 昨日(上一交易日)广度: 默认收起(只看今日), 展开才拉昨日并显示对比 ──
const yest = ref<PoolBreadth | null>(null)
const expanded = ref(false)
async function loadYest() {
  try { yest.value = await fetchPoolBreadthYesterday() } catch { yest.value = null }
}
function toggle() {
  expanded.value = !expanded.value
  if (expanded.value && !yest.value) loadYest()   // 首次展开才拉昨日(省一次请求)
}
// 已展开时池增减 → 重取昨日(盘中每3s刷不改池, 不触发)
watch(() => props.stocks.length, (n, o) => { if (n !== o && expanded.value) loadYest() })

// ── 双行对照: 每个指标一列, 今日/昨日/Δ 三行 ──
type Kind = 'pct' | 'int' | 'ratio'
interface Def { key: string; label: string; tv: number | null; yv: number | null; bull: boolean | null; kind: Kind }

function fmtKind(v: number | null, kind: Kind): string {
  if (v == null) return '—'
  if (kind === 'pct') return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
  if (kind === 'ratio') return v + '%'
  return String(v)
}
function todayColor(key: string, v: number | null): string {
  if (v == null) return 'var(--text2)'
  if (key === 'avg') return v >= 0 ? 'var(--red)' : 'var(--green)'
  if (key === 'up' || key === 'limitUp') return 'var(--red)'
  if (key === 'down' || key === 'limitDown') return 'var(--green)'
  if (key === 'upRatio') return v >= 50 ? 'var(--red)' : 'var(--green)'
  return 'var(--text1)'   // 平
}

interface Cell {
  key: string; label: string; todayText: string; yestText: string; todayColor: string
  deltaArrow: string; deltaText: string; deltaClass: 'up' | 'down' | 'flat'
}
const metrics = computed<Cell[]>(() => {
  const t = stats.value, y = yest.value
  const defs: Def[] = [
    { key: 'avg', label: '均涨跌', tv: t.avgToday, yv: y?.avg ?? null, bull: true, kind: 'pct' },
    { key: 'up', label: '涨', tv: t.up, yv: y?.up ?? null, bull: true, kind: 'int' },
    { key: 'down', label: '跌', tv: t.down, yv: y?.down ?? null, bull: false, kind: 'int' },
    { key: 'flat', label: '平', tv: t.flat, yv: y?.flat ?? null, bull: null, kind: 'int' },
    { key: 'limitUp', label: '涨停', tv: t.limitUp, yv: y?.limit_up ?? null, bull: true, kind: 'int' },
    { key: 'limitDown', label: '跌停', tv: t.limitDown, yv: y?.limit_down ?? null, bull: false, kind: 'int' },
    { key: 'upRatio', label: '红盘', tv: t.upRatio, yv: y?.up_ratio ?? null, bull: true, kind: 'ratio' },
  ]
  return defs.map(d => {
    const delta = (d.tv != null && d.yv != null) ? d.tv - d.yv : null
    let deltaArrow = '', deltaText = '', deltaClass: 'up' | 'down' | 'flat' = 'flat'
    if (delta != null && Math.abs(delta) > 1e-9) {
      deltaArrow = delta > 0 ? '↑' : '↓'
      deltaText = d.kind === 'pct' ? Math.abs(delta).toFixed(2)
        : d.kind === 'ratio' ? Math.abs(delta) + '%'
        : String(Math.abs(delta))
      // 多空着色: 该指标"变大=偏多"(bull=true)时 ↑红↓绿; "变大=偏空"(bull=false)时反之; 平=灰
      deltaClass = d.bull == null ? 'flat' : ((delta > 0) === d.bull ? 'up' : 'down')
    }
    return {
      key: d.key, label: d.label,
      todayText: fmtKind(d.tv, d.kind), yestText: fmtKind(d.yv, d.kind),
      todayColor: todayColor(d.key, d.tv), deltaArrow, deltaText, deltaClass,
    }
  })
})
</script>

<template>
  <div class="pool-stats-bar" v-if="stats.total > 0">
    <div class="psb-grid">
      <!-- 表头: 指标名 -->
      <span class="psb-rl"></span>
      <span v-for="m in metrics" :key="'h' + m.key" class="psb-ch">{{ m.label }}</span>
      <!-- 今日 -->
      <span class="psb-rl psb-rl-today">今日</span>
      <span v-for="m in metrics" :key="'t' + m.key" class="psb-tv" :style="{ color: m.todayColor }">{{ m.todayText }}</span>
      <!-- 昨日 + Δ: 仅展开时显示 -->
      <template v-if="expanded">
        <span class="psb-rl psb-muted">昨日</span>
        <span v-for="m in metrics" :key="'y' + m.key" class="psb-yv">{{ m.yestText }}</span>
        <template v-if="yest">
          <span class="psb-rl psb-muted">Δ</span>
          <span v-for="m in metrics" :key="'d' + m.key" class="psb-dv" :class="'d-' + m.deltaClass">{{ m.deltaText ? m.deltaArrow + m.deltaText : '·' }}</span>
        </template>
      </template>
    </div>
    <button class="psb-toggle" type="button" :title="expanded ? '收起' : '对比昨日(上一交易日)'" @click="toggle">
      {{ expanded ? '收起' : '比昨日' }}<span class="psb-caret">{{ expanded ? '▴' : '▾' }}</span>
    </button>
    <div class="psb-notes">
      <span v-if="stats.noPrice > 0" class="psb-note">含 {{ stats.noPrice }} 只无价</span>
      <span v-if="expanded && yest && yest.no_data > 0" class="psb-note">昨日 {{ yest.no_data }} 只无数据</span>
    </div>
  </div>
</template>

<style scoped>
.pool-stats-bar {
  display: flex; align-items: center; gap: 4px 12px; flex-wrap: wrap;
  padding: 6px 12px; border: 1px solid var(--border-color, #eee); border-radius: 8px;
  background: var(--card-bg, #fafafa); overflow-x: auto;
}
.pool-stats-bar::-webkit-scrollbar { height: 4px; }
.pool-stats-bar::-webkit-scrollbar-thumb { background: var(--border-color, #ddd); border-radius: 4px; }

/* 4 行 × (行标签 + 7 指标) 网格, 列对齐 */
.psb-grid {
  display: grid;
  grid-template-columns: auto repeat(7, minmax(40px, 1fr));
  gap: 1px 12px;
  align-items: baseline;
  font-variant-numeric: tabular-nums;
}
.psb-rl { font-size: 11px; color: var(--text-secondary, #999); font-weight: 600; padding-right: 2px; white-space: nowrap; }
.psb-rl-today { color: var(--text1, #333); }
.psb-muted { color: var(--text-secondary, #aaa); font-weight: 400; }

.psb-ch { font-size: 10.5px; color: var(--text-secondary, #999); text-align: right; white-space: nowrap; }
.psb-tv { font-size: 13px; font-weight: 700; text-align: right; white-space: nowrap; }
.psb-yv { font-size: 11.5px; color: var(--text-secondary, #aaa); text-align: right; white-space: nowrap; }
.psb-dv { font-size: 10.5px; font-weight: 600; text-align: right; white-space: nowrap; }
.psb-dv.d-up { color: var(--red); }
.psb-dv.d-down { color: var(--green); }
.psb-dv.d-flat { color: var(--text-secondary, #bbb); }

.psb-toggle {
  flex-shrink: 0; align-self: center; cursor: pointer; appearance: none;
  border: 1px solid var(--border-color, #e5e5e5); background: transparent; border-radius: 6px;
  font-size: 11px; color: var(--text-secondary, #888); padding: 2px 7px; white-space: nowrap;
  display: inline-flex; align-items: center; gap: 2px; line-height: 1.4;
}
.psb-toggle:hover { color: var(--accent-fg); border-color: color-mix(in srgb, var(--accent-fg) 40%, transparent); }
.psb-caret { font-size: 9px; }
.psb-notes { display: flex; flex-direction: column; gap: 2px; }
.psb-note { font-size: 10.5px; color: var(--text-secondary, #aaa); white-space: nowrap; }

@media (max-width: 767px) {
  .pool-stats-bar { padding: 6px 10px; }
  .psb-grid { gap: 1px 9px; grid-template-columns: auto repeat(7, minmax(34px, 1fr)); }
  .psb-tv { font-size: 12px; }
}
</style>
