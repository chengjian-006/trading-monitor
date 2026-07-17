<script setup lang="ts">
// 亮点组件 (v1.7.647): 模型胜率条 —— 模型图鉴/胜率榜用
// 胜率 <40% 走警示黄(提示弱), ≥40% 走强调蓝; 与推送卡图形词汇表同款。
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  label?: string
  value: number        // 胜率百分数 0~100
  weakBelow?: number   // 低于此值转警示色
}>(), { weakBelow: 40 })

const weak = computed(() => props.value < props.weakBelow)
const pct = computed(() => Math.max(0, Math.min(100, props.value)))
</script>

<template>
  <div class="wr-row">
    <span v-if="label" class="lbl">{{ label }}</span>
    <span class="bar"><i :class="{ weak }" :style="{ width: pct + '%' }" /></span>
    <span class="val">{{ Math.round(value) }}%</span>
  </div>
</template>

<style scoped>
.wr-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.lbl { width: 92px; flex-shrink: 0; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar { flex: 1; height: 7px; border-radius: 4px; background: var(--bg-default); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 4px; background: linear-gradient(90deg, var(--accent-fg), #4FA8FF); }
.bar i.weak { background: linear-gradient(90deg, var(--warn-fg), #C9A227); }
.val { width: 34px; text-align: right; font-family: var(--font-mono); font-weight: 600; font-variant-numeric: tabular-nums; }
</style>
