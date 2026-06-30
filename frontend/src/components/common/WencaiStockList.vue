<script setup lang="ts">
// 问财候选股网格 — 即时搜索结果 / 各候选榜复用。自管勾选, 加自选与点股弹图向上 emit。
import { reactive, computed } from 'vue'
import { NButton, NIcon, NCheckbox, NTooltip } from 'naive-ui'
import { AddCircleOutline } from '@vicons/ionicons5'
import type { WencaiItem } from '../../api/wencai'

const props = defineProps<{ items: WencaiItem[]; adding?: boolean }>()
const emit = defineEmits<{ (e: 'add', picks: { code: string; name: string }[]): void; (e: 'open', it: WencaiItem): void }>()

const selected = reactive(new Set<string>())

const allSelected = computed(() => props.items.length > 0 && props.items.every(it => selected.has(it.code)))
function toggleAll() {
  if (allSelected.value) selected.clear()
  else props.items.forEach(it => selected.add(it.code))
}
function toggle(code: string) {
  selected.has(code) ? selected.delete(code) : selected.add(code)
}
function pctText(v: number | null) {
  if (v == null) return '—'
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}
function amountText(v?: number) {
  if (v == null) return ''
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`
  return `${v}`
}
function doAdd() {
  const picks = props.items.filter(it => selected.has(it.code)).map(it => ({ code: it.code, name: it.name }))
  emit('add', picks)
  selected.clear()
}
defineExpose({ selectedCount: () => selected.size })
</script>

<template>
  <div>
    <div class="bar">
      <NCheckbox v-if="items.length" :checked="allSelected" @update:checked="toggleAll">全选</NCheckbox>
      <NButton size="small" type="primary" :disabled="!selected.size" :loading="adding" @click="doAdd">
        <template #icon><NIcon :component="AddCircleOutline" /></template>
        加入自选{{ selected.size ? `(${selected.size})` : '' }}
      </NButton>
    </div>
    <div class="grid">
      <div v-for="it in items" :key="it.code" :class="['cell', { picked: selected.has(it.code) }]">
        <div class="cell-top">
          <NCheckbox :checked="selected.has(it.code)" @update:checked="toggle(it.code)" @click.stop />
          <div class="stock" role="button" tabindex="0" :aria-label="`${it.name} ${it.code} 详情`"
               @click="emit('open', it)" @keydown.enter="emit('open', it)">
            <span class="name">{{ it.name }}</span>
            <span class="code">{{ it.code }}</span>
          </div>
          <div class="quote">
            <span class="price">{{ it.price != null ? it.price.toFixed(2) : '—' }}</span>
            <span class="pct" :class="(it.pct_change ?? 0) >= 0 ? 'up' : 'down'">{{ pctText(it.pct_change) }}</span>
          </div>
        </div>
        <div class="tags">
          <NTooltip v-if="it.extra.tech_pattern" trigger="hover">
            <template #trigger><span class="tag t-tech">{{ it.extra.tech_pattern.split('||')[0] }}</span></template>
            技术形态: {{ it.extra.tech_pattern.replace(/\|\|/g, ' · ') }}
          </NTooltip>
          <NTooltip v-if="it.extra.buy_signal" trigger="hover">
            <template #trigger><span class="tag t-buy">{{ it.extra.buy_signal.split('||')[0] }}</span></template>
            买入信号: {{ it.extra.buy_signal.replace(/\|\|/g, ' · ') }}
          </NTooltip>
          <span v-if="it.extra.concepts" class="tag t-concept">{{ it.extra.concepts.split(/[;,]/)[0] }}</span>
          <span v-else-if="it.extra.industry" class="tag t-concept">{{ it.extra.industry.split('-').slice(-1)[0] }}</span>
          <span v-if="it.extra.turnover != null" class="tag t-num">换手{{ it.extra.turnover.toFixed(1) }}%</span>
          <span v-if="it.extra.amount != null" class="tag t-num">额{{ amountText(it.extra.amount) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-bottom: 8px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 8px; }
.cell { border: 1px solid #efeff5; border-radius: 7px; padding: 8px 10px; transition: all 0.15s; }
.cell.picked { background: rgba(46,158,255,0.05); border-color: rgba(46,158,255,0.4); }
.cell-top { display: flex; align-items: center; gap: 8px; }
.stock { display: flex; align-items: baseline; gap: 6px; min-width: 0; flex: 1; cursor: pointer; touch-action: manipulation; }
.name { font-size: 13px; font-weight: 600; color: rgba(0,0,0,0.85); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.code { font-size: 11px; color: #999; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.quote { display: flex; align-items: baseline; gap: 6px; flex-shrink: 0; }
.price { font-size: 13px; font-weight: 600; color: rgba(0,0,0,0.8); font-variant-numeric: tabular-nums; }
.pct { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.tag { font-size: 10.5px; padding: 1px 7px; border-radius: 9px; white-space: nowrap; }
.t-tech { background: rgba(208,48,80,0.1); color: #d03050; }
.t-buy { background: rgba(24,160,88,0.12); color: #18a058; }
.t-concept { background: rgba(46,158,255,0.1); color: #2e9eff; }
.t-num { background: rgba(0,0,0,0.05); color: #888; font-variant-numeric: tabular-nums; }
.up { color: #d03050; }
.down { color: #18a058; }
@media (max-width: 768px) {
  .grid { grid-template-columns: 1fr; }
}
</style>
