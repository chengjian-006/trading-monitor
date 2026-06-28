<script setup lang="ts">
// 个股策略格式化渲染: 保留换行, 高亮 目标/止损/仓位 三类关键词(A股色彩语义)。
// 富卡 Popover(功能A) 与 策略总览抽屉(功能B) 共用。
import { computed } from 'vue'

const props = defineProps<{ text: string }>()

interface Seg { text: string; color?: string }

// 红=涨方向(目标), 绿=跌方向(止损), 紫=仓位(沿用策略列既有紫色主题)
const PATTERNS: { re: RegExp; color: string }[] = [
  { re: /目标(?:价|位)?\s*[:：]?\s*\d+(?:\.\d+)?%?/g, color: '#cf222e' },
  { re: /止损(?:价|位)?\s*[:：]?\s*\d+(?:\.\d+)?%?/g, color: '#16a34a' },
  { re: /(?:仓位\s*[:：]?\s*\d+(?:\.\d+)?成?%?|加仓\s*\d+(?:\.\d+)?%?|减仓\s*\d+(?:\.\d+)?%?|\d+(?:\.\d+)?成(?:仓)?|半仓|满仓|空仓)/g, color: '#7c3aed' },
]

const segments = computed<Seg[]>(() => {
  const text = props.text || ''
  type M = { start: number; end: number; color: string }
  const matches: M[] = []
  for (const p of PATTERNS) {
    p.re.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = p.re.exec(text)) !== null) {
      matches.push({ start: m.index, end: m.index + m[0].length, color: p.color })
      if (m.index === p.re.lastIndex) p.re.lastIndex++
    }
  }
  matches.sort((a, b) => a.start - b.start)
  const segs: Seg[] = []
  let cursor = 0
  for (const mt of matches) {
    if (mt.start < cursor) continue // 重叠匹配跳过, 保留靠前的
    if (mt.start > cursor) segs.push({ text: text.slice(cursor, mt.start) })
    segs.push({ text: text.slice(mt.start, mt.end), color: mt.color })
    cursor = mt.end
  }
  if (cursor < text.length) segs.push({ text: text.slice(cursor) })
  return segs
})
</script>

<template>
  <span class="strategy-text">
    <span
      v-for="(s, i) in segments"
      :key="i"
      :style="s.color ? { color: s.color, fontWeight: 600 } : undefined"
    >{{ s.text }}</span>
  </span>
</template>

<style scoped>
.strategy-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text1);
}
</style>
