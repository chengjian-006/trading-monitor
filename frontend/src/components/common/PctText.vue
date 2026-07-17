<script setup lang="ts">
// 基线0 业务件 (v1.7.646): 涨跌幅/涨跌数值文本
// 规范: 语义色(红涨绿跌灰平) + tabular-nums + 全列统一小数位 + 可选单元格级 flash(550ms, 动画中不重复触发)
import { computed, ref, watch, onUnmounted } from 'vue'

const props = withDefaults(defineProps<{
  value: number | null | undefined
  digits?: number      // 统一小数位
  signed?: boolean     // 正数带 + 号
  suffix?: string      // 默认 '%', 传 '' 可展示普通数值
  flash?: boolean      // 值变动时闪 10% 底色 550ms 淡出
  bold?: boolean
}>(), { digits: 2, signed: true, suffix: '%', flash: false, bold: false })

const text = computed(() => {
  const v = props.value
  if (v == null || Number.isNaN(v)) return '—'
  return (props.signed && v > 0 ? '+' : '') + v.toFixed(props.digits) + props.suffix
})

const toneClass = computed(() => {
  const v = props.value
  if (v == null || Number.isNaN(v) || v === 0) return 'flat'
  return v > 0 ? 'up' : 'down'
})

// 单元格级 flash: 只在值真实变动时触发; 动画进行中不重复触发
const flashClass = ref('')
let flashTimer: number | undefined
watch(() => props.value, (nv, ov) => {
  if (!props.flash || nv == null || ov == null || nv === ov || flashClass.value) return
  flashClass.value = nv > ov ? 'cell-flash-up' : 'cell-flash-down'
  flashTimer = window.setTimeout(() => { flashClass.value = '' }, 600)
})
onUnmounted(() => { if (flashTimer) clearTimeout(flashTimer) })
</script>

<template>
  <span class="pct-text" :class="[toneClass, flashClass, { bold }]">{{ text }}</span>
</template>

<style scoped>
.pct-text {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.up { color: var(--up-fg); }
.down { color: var(--down-fg); }
.flat { color: var(--flat-fg); }
.bold { font-weight: 600; }
</style>
