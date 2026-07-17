<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import type { IndexTrendPoint } from '../../api/market-report'

const props = defineProps<{
  trends: IndexTrendPoint[]
  preClose: number
  width?: number
  height?: number
  pct?: number | null   // 当日涨跌幅(权威值): 传入则颜色直接随它, 保证走势图颜色永远和"涨幅"列一致
}>()

const w = computed(() => props.width || 80)
const h = computed(() => props.height || 36)

// 可视区才渲染 SVG: 股票池 96 行走势同时到达时不一次性全渲染, 只渲染视口内的, 提速首屏/滚动
const root = ref<HTMLElement>()
const visible = ref(false)
let obs: IntersectionObserver | null = null
onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') { visible.value = true; return }
  obs = new IntersectionObserver((entries) => {
    if (entries.some(e => e.isIntersecting)) { visible.value = true; obs?.disconnect(); obs = null }
  }, { rootMargin: '150px' })
  if (root.value) obs.observe(root.value)
})
onUnmounted(() => { obs?.disconnect(); obs = null })

const isUp = computed(() => {
  // 优先用外部传入的权威涨跌幅着色(与涨幅列同源, 杜绝"涨幅红、走势绿"的基准不一致)
  if (props.pct != null) return props.pct >= 0
  // 兜底: 无 pct 时按 末价 vs 昨收 自判
  if (!props.trends.length || !props.preClose) return true
  return props.trends[props.trends.length - 1].price >= props.preClose
})

// v1.7.647 亮点: 对齐 Token 涨跌色(压饱和红/绿) + 渐变填充 + 发光端点
const lineColor = computed(() => isUp.value ? '#D92B26' : '#0F8A5F')
const uid = Math.random().toString(36).slice(2, 8)  // 每实例唯一渐变 id, 防 SVG defs 撞车

// 末点坐标(发光端点用)
const endPoint = computed(() => {
  if (props.trends.length < 2) return null
  const prices = props.trends.map(t => t.price)
  const min = Math.min(...prices, props.preClose)
  const max = Math.max(...prices, props.preClose)
  const range = max - min || 1
  const stepX = w.value / (prices.length - 1)
  const last = prices[prices.length - 1]
  return { x: (prices.length - 1) * stepX, y: h.value - ((last - min) / range) * h.value }
})

const pathD = computed(() => {
  if (props.trends.length < 2) return ''
  const prices = props.trends.map(t => t.price)
  const min = Math.min(...prices, props.preClose)
  const max = Math.max(...prices, props.preClose)
  const range = max - min || 1
  const stepX = w.value / (prices.length - 1)
  return prices
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * stepX).toFixed(1)},${(h.value - ((p - min) / range) * h.value).toFixed(1)}`)
    .join(' ')
})

const areaD = computed(() => {
  if (!pathD.value) return ''
  const prices = props.trends.map(t => t.price)
  const stepX = w.value / (prices.length - 1)
  const lastX = ((prices.length - 1) * stepX).toFixed(1)
  return `${pathD.value} L${lastX},${h.value} L0,${h.value} Z`
})

const preCloseY = computed(() => {
  if (!props.trends.length || !props.preClose) return -1
  const prices = props.trends.map(t => t.price)
  const min = Math.min(...prices, props.preClose)
  const max = Math.max(...prices, props.preClose)
  const range = max - min || 1
  return h.value - ((props.preClose - min) / range) * h.value
})
</script>

<template>
  <div ref="root" class="mini-wrap" :style="{ width: w + 'px', height: h + 'px' }">
    <svg v-if="visible && trends.length >= 2" :width="w" :height="h" class="mini-sparkline" aria-hidden="true">
      <defs>
        <linearGradient :id="`spk-${uid}`" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" :stop-color="lineColor" stop-opacity="0.22" />
          <stop offset="1" :stop-color="lineColor" stop-opacity="0" />
        </linearGradient>
      </defs>
      <path :d="areaD" :fill="`url(#spk-${uid})`" />
      <line v-if="preCloseY >= 0" x1="0" :y1="preCloseY" :x2="w" :y2="preCloseY" stroke="#ccc" stroke-width="0.5" stroke-dasharray="2,2" />
      <path :d="pathD" :stroke="lineColor" stroke-width="1.5" fill="none" stroke-linejoin="round" stroke-linecap="round" />
      <template v-if="endPoint">
        <circle :cx="endPoint.x" :cy="endPoint.y" r="3.6" :fill="lineColor" opacity="0.22" />
        <circle :cx="endPoint.x" :cy="endPoint.y" r="1.9" :fill="lineColor" />
      </template>
    </svg>
  </div>
</template>

<style scoped>
.mini-wrap { flex-shrink: 0; }
.mini-sparkline { display: block; flex-shrink: 0; }
</style>
