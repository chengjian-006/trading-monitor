<script setup lang="ts">
// 模拟账户资金趋势(折线/面积图) — 数据为每个交易日收盘后的总资产快照(cfzy_biz_paper_equity)
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { createChart, type IChartApi } from 'lightweight-charts'

const props = defineProps<{ data: any[] }>()
const chartEl = ref<HTMLDivElement>()
let chart: IChartApi | null = null
let resizeObs: ResizeObserver | null = null

function destroy() {
  resizeObs?.disconnect(); resizeObs = null
  chart?.remove(); chart = null
}

function render() {
  if (!chartEl.value) return
  destroy()
  const rows = (props.data || [])
    .map((r: any) => ({ time: String(r.snap_date).slice(0, 10), value: Number(r.total_equity) }))
    .filter((r: any) => r.time && !Number.isNaN(r.value))
  if (rows.length < 1) return

  chart = createChart(chartEl.value, {
    width: chartEl.value.clientWidth,
    height: 220,
    layout: { background: { type: 'solid' as any, color: '#fff' }, textColor: '#888', fontSize: 10 },
    grid: { vertLines: { color: '#f5f5f5' }, horzLines: { color: '#f5f5f5' } },
    rightPriceScale: { borderColor: '#eee' },
    timeScale: { borderColor: '#eee', timeVisible: false, secondsVisible: false },
    crosshair: { mode: 0 },
    handleScroll: false, handleScale: false,
  })
  const s = chart.addAreaSeries({
    lineColor: '#2563eb', topColor: 'rgba(37,99,235,0.18)', bottomColor: 'rgba(37,99,235,0.02)',
    lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
  })
  s.setData(rows as any)
  chart.timeScale().fitContent()

  resizeObs = new ResizeObserver(() => {
    if (chart && chartEl.value) chart.applyOptions({ width: chartEl.value.clientWidth })
  })
  resizeObs.observe(chartEl.value)
}

watch(() => props.data, () => nextTick(render), { deep: true })
onMounted(() => nextTick(render))
onUnmounted(destroy)
</script>

<template>
  <div>
    <div ref="chartEl" class="equity-chart" />
    <div v-if="!data || data.length < 1" class="equity-empty">
      暂无资金曲线数据(每个交易日收盘后记一个点; 重置账户后从当日重新累计)
    </div>
  </div>
</template>

<style scoped>
.equity-chart { width: 100%; min-height: 220px; }
.equity-chart :deep(a[href*="tradingview"]) { display: none !important; }
.equity-empty { font-size: 12px; color: #aaa; text-align: center; padding: 16px; }
</style>
