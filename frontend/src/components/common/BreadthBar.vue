<script setup lang="ts">
// 亮点组件 (v1.7.647): 市场广度条 —— 涨/跌家数做成红绿渐变条, 一眼看多空力量
// 数据密集页(监控看板/概览带)用, 比纯数字直观。红涨绿跌守色。
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  up: number           // 上涨家数
  down: number         // 下跌家数
  width?: number
  showCount?: boolean  // 是否在两侧显示家数
}>(), { width: 130, showCount: false })

const total = computed(() => Math.max(props.up + props.down, 1))
const upPct = computed(() => (props.up / total.value) * 100)
</script>

<template>
  <span class="breadth" :style="{ width: showCount ? 'auto' : width + 'px' }">
    <b v-if="showCount" class="c up">{{ up }}</b>
    <span class="bar" :style="{ width: width + 'px' }">
      <span class="seg up" :style="{ width: upPct + '%' }" />
      <span class="seg down" :style="{ width: (100 - upPct) + '%' }" />
    </span>
    <b v-if="showCount" class="c down">{{ down }}</b>
  </span>
</template>

<style scoped>
.breadth { display: inline-flex; align-items: center; gap: 8px; font-variant-numeric: tabular-nums; }
.c { font-size: 12px; font-weight: 600; }
.c.up { color: var(--up-fg); }
.c.down { color: var(--down-fg); }
.bar {
  height: 7px; border-radius: 4px; overflow: hidden; display: flex;
  box-shadow: inset 0 0 0 1px var(--border-muted);
}
.seg { height: 100%; }
.seg.up { background: linear-gradient(90deg, #FF7A6E, var(--up-fg)); }
.seg.down { background: linear-gradient(90deg, var(--down-fg), #4FBE8E); }
</style>
