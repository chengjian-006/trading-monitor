<script setup lang="ts">
// 数据新鲜度小徽章 - v1.7.x
// 给"数据从 X 时刻起未更新"的卡片提供统一的视觉提示, 解决用户"这个数字现在新不新"困惑
import { computed, onMounted, onUnmounted, ref } from 'vue'

const props = withDefaults(defineProps<{
  updatedAt: string | number | Date | null | undefined  // 后端数据快照时间 (ISO 字符串 / unix ms / Date)
  staleSeconds?: number   // 超过此值开始变橙 (默认 120s)
  errorSeconds?: number   // 超过此值变红 (默认 600s)
}>(), {
  staleSeconds: 120,
  errorSeconds: 600,
})

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  timer = setInterval(() => { now.value = Date.now() }, 5_000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })

const ageSeconds = computed(() => {
  if (!props.updatedAt) return null
  let ts: number
  if (props.updatedAt instanceof Date) ts = props.updatedAt.getTime()
  else if (typeof props.updatedAt === 'number') ts = props.updatedAt
  else {
    // 兼容 "2026-05-28 11:30:45" 这种 MySQL DATETIME 格式
    const s = String(props.updatedAt).replace(' ', 'T')
    ts = new Date(s).getTime()
    if (isNaN(ts)) return null
  }
  return Math.max(0, Math.floor((now.value - ts) / 1000))
})

const display = computed(() => {
  const a = ageSeconds.value
  if (a == null) return '—'
  if (a < 60) return `${a} 秒前`
  if (a < 3600) return `${Math.floor(a / 60)} 分钟前`
  if (a < 86400) return `${Math.floor(a / 3600)} 小时前`
  return `${Math.floor(a / 86400)} 天前`
})

const tone = computed<'fresh' | 'stale' | 'error'>(() => {
  const a = ageSeconds.value
  if (a == null) return 'fresh'
  if (a >= props.errorSeconds) return 'error'
  if (a >= props.staleSeconds) return 'stale'
  return 'fresh'
})
</script>

<template>
  <span class="freshness" :class="tone" :title="`数据快照: ${updatedAt || '未知'}\n${ageSeconds == null ? '' : ageSeconds + ' 秒前'}`">
    <span class="dot" />
    {{ display }}
  </span>
</template>

<style scoped>
.freshness {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-weight: 500;
  user-select: none;
  font-variant-numeric: tabular-nums;
  border: 1px solid transparent;
}
.freshness .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
/* 描边细标签(淡底+彩边+彩字), 走状态色 token 深色自适配 */
.freshness.fresh { background: var(--success-bg-muted); color: var(--success-fg); border-color: color-mix(in srgb, var(--success-fg) 35%, transparent); }
.freshness.fresh .dot { background: var(--success-fg); }
.freshness.stale { background: var(--warn-bg-muted); color: var(--warn-fg); border-color: color-mix(in srgb, var(--warn-fg) 35%, transparent); }
.freshness.stale .dot { background: var(--warn-fg); }
.freshness.error { background: var(--danger-bg-muted); color: var(--danger-fg); border-color: color-mix(in srgb, var(--danger-fg) 35%, transparent); }
.freshness.error .dot { background: var(--danger-fg); }
</style>
