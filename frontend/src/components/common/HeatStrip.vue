<script setup lang="ts">
// 亮点组件 (v1.7.647): 战绩热带 —— 贡献图式逐笔胜负, 模型图鉴/复盘用
// 绿=跌(负)红=涨(胜)?? 注意: 这里胜=盈利用 up-fg 红, 负=亏损用 down-fg 绿, 守 A 股红涨绿跌 = 红盈绿亏。
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  results: (1 | 0 | -1)[]   // 1=胜 0=平/未评估 -1=负, 按时间顺序
  cell?: number
  showLegend?: boolean
}>(), { cell: 15, showLegend: true })

const wins = computed(() => props.results.filter(r => r === 1).length)
const losses = computed(() => props.results.filter(r => r === -1).length)
const rate = computed(() => {
  const judged = wins.value + losses.value
  return judged ? Math.round((wins.value / judged) * 100) : 0
})
function cls(r: number) { return r === 1 ? 'w' : r === -1 ? 'l' : 'n' }
</script>

<template>
  <div class="heat-wrap">
    <div class="heat">
      <i v-for="(r, i) in results" :key="i" :class="cls(r)"
         :style="{ width: cell + 'px', height: cell + 'px' }" />
    </div>
    <div v-if="showLegend" class="legend">
      <span><i class="w" />胜 {{ wins }}</span>
      <span><i class="l" />负 {{ losses }}</span>
      <span>胜率 {{ rate }}%</span>
    </div>
  </div>
</template>

<style scoped>
.heat { display: flex; gap: 3px; flex-wrap: wrap; }
.heat i { border-radius: 3px; flex-shrink: 0; }
.w { background: var(--up-fg); }
.l { background: var(--down-fg); }
.n { background: var(--border-muted); }
.legend { display: flex; gap: 14px; margin-top: 9px; font-size: 11px; color: var(--fg-subtle); }
.legend span { display: inline-flex; align-items: center; gap: 4px; }
.legend i { width: 9px; height: 9px; border-radius: 2px; }
</style>
