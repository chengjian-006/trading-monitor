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
  border-radius: 8px;
  font-weight: 500;
  user-select: none;
  font-variant-numeric: tabular-nums;
}
.freshness .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.freshness.fresh { background: rgba(22, 163, 74, 0.08); color: #15803d; }
.freshness.fresh .dot { background: #16a34a; }
.freshness.stale { background: rgba(217, 119, 6, 0.10); color: #b45309; }
.freshness.stale .dot { background: #d97706; }
.freshness.error { background: rgba(220, 38, 38, 0.10); color: #b91c1c; }
.freshness.error .dot { background: #dc2626; }
</style>
