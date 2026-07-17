<script setup lang="ts">
// 当日情绪四线趋势 — 涨停/跌停(右轴) + 上涨/下跌(左轴), 数据来自情绪快照(每3分钟一行)
// 横轴固化为全天交易时段(09:30-11:30/13:00-15:00, 压缩午休), 盘中数据只填左侧
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { createChart, type IChartApi, LineStyle } from 'lightweight-charts'
import { fetchEmotionHistory, type EmotionSnapshot } from '../../api/emotion'

const chartEl = ref<HTMLDivElement>()
const pointCount = ref(0)
let chart: IChartApi | null = null
let resizeObs: ResizeObserver | null = null
let timer: number | undefined

// 交易分钟序号: 09:30→0 … 11:30→120, 13:00→121 … 15:00→241 (压缩午休)
const SESSION_LAST = 241
function clockToIdx(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  const sec = (h || 0) * 3600 + (m || 0) * 60
  const AM_OPEN = 34200, AM_CLOSE = 41400, PM_OPEN = 46800, PM_CLOSE = 54000
  if (sec <= AM_OPEN) return 0
  if (sec <= AM_CLOSE) return Math.round((sec - AM_OPEN) / 60)
  if (sec < PM_OPEN) return 120
  if (sec <= PM_CLOSE) return 121 + Math.round((sec - PM_OPEN) / 60)
  return SESSION_LAST
}
function idxToClock(idx: number): string {
  const sec = idx <= 120 ? 34200 + idx * 60 : 46800 + (idx - 121) * 60
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60)
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}

function seriesFrom(points: EmotionSnapshot[], key: keyof EmotionSnapshot) {
  const byIdx = new Map<number, number>()
  for (const p of points) {
    const t = (p.captured_at || '').slice(11, 16)
    if (!t) continue
    const v = p[key] as number | null
    if (v == null) continue
    byIdx.set(clockToIdx(t), v)
  }
  return [...byIdx.keys()].sort((a, b) => a - b).map(i => ({ time: i * 60, value: byIdx.get(i)! }))
}

function destroy() {
  resizeObs?.disconnect(); resizeObs = null
  chart?.remove(); chart = null
}

async function render() {
  if (!chartEl.value) return
  let points: EmotionSnapshot[] = []
  try {
    const h = await fetchEmotionHistory()
    points = h.points || []
  } catch { /* silent */ }
  pointCount.value = points.length
  destroy()
  if (points.length < 2) return  // 不足两点画不出趋势(当日刚开盘/历史单点)

  chart = createChart(chartEl.value, {
    width: chartEl.value.clientWidth,
    height: 170,
    layout: { background: { type: 'solid' as any, color: '#fff' }, textColor: '#5A6472', fontSize: 10 },
    grid: { vertLines: { color: '#E6EAF0' }, horzLines: { color: '#E6EAF0' } },
    rightPriceScale: { visible: true, borderColor: '#D2D8E1' },
    leftPriceScale: { visible: true, borderColor: '#D2D8E1' },
    timeScale: {
      borderColor: '#D2D8E1', timeVisible: true, secondsVisible: false,
      tickMarkFormatter: (t: number) => idxToClock(Math.round((t as number) / 60)),
    },
    crosshair: { mode: 0 },
    handleScroll: false, handleScale: false,
  })
  const mk = (key: keyof EmotionSnapshot, color: string, scale: 'left' | 'right', dashed = false) => {
    const s = chart!.addLineSeries({
      priceScaleId: scale, color, lineWidth: 2,
      lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: true,
    })
    const data = seriesFrom(points, key)
    // 固化横轴: 末尾补 15:00 空白点
    if (data.length && data[data.length - 1].time < SESSION_LAST * 60) {
      (data as any).push({ time: SESSION_LAST * 60 })
    }
    s.setData(data as any)
  }
  // 涨停/跌停 → 右轴(数值小); 上涨/下跌 → 左轴(数值大, 虚线)
  mk('limit_up_count', '#E0342A', 'right')
  mk('limit_down_count', '#12A06B', 'right')
  mk('up_count', '#f59e0b', 'left', true)
  mk('down_count', '#0891b2', 'left', true)
  chart.timeScale().setVisibleRange({ from: 0 as any, to: (SESSION_LAST * 60) as any })

  resizeObs = new ResizeObserver(() => {
    if (chart && chartEl.value) chart.applyOptions({ width: chartEl.value.clientWidth })
  })
  resizeObs.observe(chartEl.value)
}

onMounted(() => {
  nextTick(render)
  // v1.7.571: 切走标签页时跳过重绘(原来后台每分钟拉情绪历史+整图销毁重建); 保留 onUnmounted 的 destroy 清理
  timer = window.setInterval(() => { if (!document.hidden) render() }, 60000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer); destroy() })
</script>

<template>
  <div class="trend-box">
    <div class="trend-title">
      当日情绪趋势
      <span class="legend"><i class="dot zt" />涨停<i class="dot dt" />跌停<i class="dot up" />上涨<i class="dot dn" />下跌</span>
    </div>
    <div ref="chartEl" class="trend-chart" />
    <div v-if="pointCount < 2" class="trend-empty">当日数据积累中（每3分钟一点，盘中逐步成形；历史日仅单点无法成线）</div>
  </div>
</template>

<style scoped>
.trend-box { margin-top: 12px; }
.trend-title { font-size: 13px; font-weight: 600; color: rgba(0,0,0,0.7); margin-bottom: 6px; display: flex; align-items: center; gap: 10px; }
.legend { font-size: 11px; font-weight: 400; color: #999; display: inline-flex; align-items: center; gap: 4px; }
.legend .dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; margin-left: 8px; }
.legend .dot.zt { background: #dc2626; }
.legend .dot.dt { background: #16a34a; }
.legend .dot.up { background: #f59e0b; }
.legend .dot.dn { background: #0891b2; }
.trend-chart { width: 100%; min-height: 170px; }
.trend-chart :deep(a[href*="tradingview"]) { display: none !important; }
.trend-empty { font-size: 12px; color: #aaa; text-align: center; padding: 8px; }
</style>
