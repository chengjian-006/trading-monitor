<script setup lang="ts">
// 单个指数图卡 - v1.7.x
// 顶部: 指数名 + 现价 + 涨跌幅
// 中部: 分时图(价格 area + 均价 dashed) + 成交量柱图副 pane
// Tab: 分时 / 日K 趋势(area line + 成交量柱)
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, type IChartApi, type ISeriesApi, LineStyle } from 'lightweight-charts'
import type { IndexTrendData } from '../../api/market-report'
import { formatYiFromYi } from '../../utils/formatAmount'
import type { IndexDailyData } from '../../api/market-report'
import { useResponsive } from '../../composables/useResponsive'

const { isPhone } = useResponsive()

const props = defineProps<{
  symbolKey: string                      // 'sh000001' 等, 用于 daily 数据键
  intraday: IndexTrendData | null
  daily: IndexDailyData | null
  currentPrice: number
  pctChange: number
  height?: number                        // 图表高度(px), 不传则按端默认(PC 200/移动 150)
  showHead?: boolean                     // 是否显示卡片头(名/价/涨跌/分时日K切换), 默认 true; 概览大图由外层渲染头部
}>()

const mode = ref<'intraday' | 'daily'>('intraday')
const chartEl = ref<HTMLDivElement>()
let chart: IChartApi | null = null
let priceSeries: ISeriesApi<'Area'> | ISeriesApi<'Candlestick'> | null = null
let avgSeries: ISeriesApi<'Line'> | null = null
let volumeSeries: ISeriesApi<'Histogram'> | null = null
let resizeObs: ResizeObserver | null = null

const A_UP = '#dc2626'    // 红涨
const A_DOWN = '#16a34a'  // 绿跌

const chartMinPx = computed(() => props.height ?? (isPhone.value ? 150 : 200))
const pctColor = computed(() => props.pctChange >= 0 ? '#dc2626' : '#16a34a')
const pctText = computed(() => (props.pctChange >= 0 ? '+' : '') + props.pctChange.toFixed(2) + '%')
const up = computed(() => props.pctChange >= 0)

function timeToSeconds(s: string): number {
  // 兼容 "09:35" / "2026-05-28 09:35"
  const last = s.length >= 5 ? s.slice(-5) : s
  const [h, m] = last.split(':').map(Number)
  return (h || 0) * 3600 + (m || 0) * 60
}

// 交易分钟序号: 把横坐标"固化"为全天交易时段(压缩午休), 盘中数据只填左侧、不拉伸。
// A股 09:30→0…11:30→120, 13:00→121…15:00→241; 港股晚收1h 09:30→0…12:00→150, 13:00→151…16:00→331。
const AM_OPEN = 9 * 3600 + 30 * 60
const PM_OPEN = 13 * 3600
function isHK(): boolean { return props.symbolKey.startsWith('hk') }
function sessCfg() {
  return isHK()
    ? { amClose: 12 * 3600, pmClose: 16 * 3600, amCloseIdx: 150, lastIdx: 331 }
    : { amClose: 11 * 3600 + 30 * 60, pmClose: 15 * 3600, amCloseIdx: 120, lastIdx: 241 }
}
function secToSessionIdx(sec: number): number {
  const c = sessCfg()
  if (sec <= AM_OPEN) return 0
  if (sec <= c.amClose) return Math.round((sec - AM_OPEN) / 60)
  if (sec < PM_OPEN) return c.amCloseIdx
  if (sec <= c.pmClose) return c.amCloseIdx + 1 + Math.round((sec - PM_OPEN) / 60)
  return c.lastIdx
}
function sessionIdxToClock(idx: number): string {
  const c = sessCfg()
  const sec = idx <= c.amCloseIdx ? AM_OPEN + idx * 60 : PM_OPEN + (idx - c.amCloseIdx - 1) * 60
  const hh = Math.floor(sec / 3600)
  const mm = Math.floor((sec % 3600) / 60)
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}
function dateToTs(d: string): number {
  return Math.floor(new Date(d).getTime() / 1000)
}

function destroyChart() {
  resizeObs?.disconnect()
  resizeObs = null
  chart?.remove()
  chart = null
  priceSeries = avgSeries = volumeSeries = null
}

function render() {
  if (!chartEl.value) return
  destroyChart()
  const lineColor = up.value ? A_UP : A_DOWN
  const areaTop = up.value ? 'rgba(220,38,38,0.18)' : 'rgba(22,163,74,0.18)'
  const chartHeight = props.height ?? (isPhone.value ? 150 : 200)  // 外层可指定; 否则移动端矮一截
  let intradayLastIdx = sessCfg().lastIdx  // 分时 X 轴右端: 默认收盘(15:00), 有数据时收敛到最新一笔
  chart = createChart(chartEl.value, {
    width: chartEl.value.clientWidth,
    height: chartHeight,
    layout: { background: { type: 'solid' as any, color: '#fff' }, textColor: '#666', fontSize: 10 },
    grid: { vertLines: { color: '#f5f5f5' }, horzLines: { color: '#f5f5f5' } },
    rightPriceScale: { borderColor: '#e5e5e5', scaleMargins: { top: 0.05, bottom: 0.30 } },
    // 左轴显示涨跌幅%(相对昨收), 仅分时且有昨收时启用; 与右侧价格轴同 margins 故一一对应
    leftPriceScale: { visible: mode.value === 'intraday' && (props.intraday?.pre_close ?? 0) > 0, borderColor: '#e5e5e5', scaleMargins: { top: 0.05, bottom: 0.30 } },
    timeScale: {
      visible: true,
      borderColor: '#e5e5e5',
      timeVisible: mode.value === 'intraday',
      secondsVisible: false,
      tickMarkFormatter: mode.value === 'intraday'
        ? (t: number) => sessionIdxToClock(Math.round((t as number) / 60))
        : undefined,
    },
    crosshair: { mode: 0 },
    handleScroll: false,
    handleScale: false,
  })

  if (mode.value === 'intraday') {
    priceSeries = chart.addAreaSeries({
      lineColor,
      topColor: areaTop,
      bottomColor: 'transparent',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    })
  } else {
    priceSeries = chart.addCandlestickSeries({
      upColor: A_UP,
      downColor: A_DOWN,
      borderUpColor: A_UP,
      borderDownColor: A_DOWN,
      wickUpColor: A_UP,
      wickDownColor: A_DOWN,
      priceLineVisible: false,
      lastValueVisible: true,
    })
  }
  volumeSeries = chart.addHistogramSeries({
    color: up.value ? 'rgba(220,38,38,0.45)' : 'rgba(22,163,74,0.45)',
    priceScaleId: 'volume',
    priceFormat: { type: 'volume' },
    lastValueVisible: false,
    priceLineVisible: false,
  })
  chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } })

  if (mode.value === 'intraday') {
    avgSeries = chart.addLineSeries({
      color: '#f59e0b',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })
    const pts = props.intraday?.trends || []
    const preClose = props.intraday?.pre_close ?? 0
    // 以"交易分钟序号"为 X 轴 (压缩午休), 同一序号去重保留最后一笔
    const priceByIdx = new Map<number, number>()
    const avgByIdx = new Map<number, number>()
    const volByIdx = new Map<number, { v: number; up: boolean }>()
    for (const p of pts) {
      const idx = secToSessionIdx(timeToSeconds(p.time))
      priceByIdx.set(idx, p.price)
      if (p.avg_price != null) avgByIdx.set(idx, p.avg_price)
      if (p.volume != null) {
        // 昨收缺失时不再一律染红, 退而与指数当日涨跌方向(权威pctChange)保持一致
        volByIdx.set(idx, { v: p.volume, up: preClose > 0 ? p.price >= preClose : (props.pctChange ?? 0) >= 0 })
      }
    }
    const sortedIdx = [...priceByIdx.keys()].sort((a, b) => a - b)
    const priceData: any[] = sortedIdx.map(i => ({ time: i * 60, value: priceByIdx.get(i)! }))
    const avgData: any[] = [...avgByIdx.keys()].sort((a, b) => a - b).map(i => ({ time: i * 60, value: avgByIdx.get(i)! }))
    const volData: any[] = [...volByIdx.keys()].sort((a, b) => a - b).map(i => {
      const o = volByIdx.get(i)!
      return { time: i * 60, value: o.v, color: o.up ? 'rgba(220,38,38,0.55)' : 'rgba(22,163,74,0.55)' }
    })
    // 左端固化到 09:30(开盘前补空白点); 右端不再补 15:00, 让坐标轴只画到最新一笔(盘中右侧不留白到收盘)
    if (!priceByIdx.has(0)) priceData.unshift({ time: 0 })
    intradayLastIdx = sortedIdx.length ? sortedIdx[sortedIdx.length - 1] : sessCfg().lastIdx
    priceSeries.setData(priceData)
    if (avgData.length) avgSeries.setData(avgData)
    volumeSeries.setData(volData)
    // 左侧涨跌幅% 刻度: 透明线(不画线)只用来驱动左轴, 值=相对昨收涨跌幅, 与右侧价格一一对应
    if (preClose > 0) {
      const pctSeries = chart.addLineSeries({
        priceScaleId: 'left',
        color: 'rgba(0,0,0,0)',
        lineWidth: 1,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceFormat: { type: 'custom', minMove: 0.01, formatter: (v: number) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%' },
      })
      pctSeries.setData(sortedIdx.map(i => ({ time: i * 60, value: (priceByIdx.get(i)! - preClose) / preClose * 100 })) as any)
    }
  } else {
    const bars = props.daily?.data || []
    const candleData: any[] = []
    const volData: any[] = []
    for (const b of bars) {
      const ts = dateToTs(b.date)
      candleData.push({ time: ts, open: b.open, high: b.high, low: b.low, close: b.close })
      const dayUp = b.close >= b.open
      volData.push({ time: ts, value: b.volume, color: dayUp ? 'rgba(220,38,38,0.45)' : 'rgba(22,163,74,0.45)' })
    }
    priceSeries.setData(candleData)
    volumeSeries.setData(volData)
  }
  if (mode.value === 'intraday') {
    // X 轴左端固定 09:30, 右端跟随最新一笔数据 (盘中不再固定留白到 15:00)
    chart.timeScale().setVisibleRange({ from: 0 as any, to: (intradayLastIdx * 60) as any })
  } else {
    chart.timeScale().fitContent()
  }

  resizeObs = new ResizeObserver(() => {
    if (chart && chartEl.value) chart.applyOptions({ width: chartEl.value.clientWidth })
  })
  resizeObs.observe(chartEl.value)
}

onMounted(() => nextTick(render))
watch([() => props.intraday, () => props.daily, mode, isPhone], () => nextTick(render), { deep: true })
onUnmounted(destroyChart)
</script>

<template>
  <div class="idx-card" :class="{ 'no-head': showHead === false }">
    <div v-if="showHead !== false" class="idx-head">
      <span class="idx-name">{{ intraday?.name || daily?.name || '—' }}</span>
      <span class="idx-price">{{ currentPrice ? currentPrice.toFixed(2) : '—' }}</span>
      <span class="idx-pct" :style="{ color: pctColor }">{{ pctText }}</span>
      <span class="idx-amount" v-if="intraday?.amount">{{ formatYiFromYi(intraday.amount) }}</span>
      <span class="spacer" />
      <div class="tabs">
        <button :class="['tab', mode === 'intraday' && 'active']" @click="mode = 'intraday'">分时</button>
        <button :class="['tab', mode === 'daily' && 'active']" @click="mode = 'daily'">日K趋势</button>
      </div>
    </div>
    <div ref="chartEl" class="idx-chart" :style="{ minHeight: chartMinPx + 'px' }" />
  </div>
</template>

<style scoped>
.idx-card {
  background: #fff;
  border: 1px solid var(--border, #efeff5);
  border-radius: 6px;
  padding: 6px 8px;
}
.idx-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 12px;
}
.idx-name { font-weight: 600; color: var(--text1); }
.idx-price { font-family: monospace; font-weight: 700; font-size: 13px; }
.idx-pct { font-weight: 700; font-variant-numeric: tabular-nums; }
.idx-amount { font-size: 11px; color: var(--text2); font-variant-numeric: tabular-nums; }
.spacer { flex: 1; }
.tabs { display: inline-flex; gap: 2px; }
.tab {
  font-size: 10.5px;
  padding: 2px 7px;
  border: 1px solid var(--border, #d4d4d8);
  background: #fafafa;
  color: var(--text2);
  border-radius: 3px;
  cursor: pointer;
  touch-action: manipulation;
  user-select: none;
}
.tab:hover { color: var(--primary); }
.tab.active { background: var(--primary); color: #fff; border-color: var(--primary); }
.idx-chart { width: 100%; }
.idx-card.no-head { padding: 0; border: none; }
.idx-chart :deep(a[href*="tradingview"]) { display: none !important; }

@media (max-width: 768px) {
  .idx-card { padding: 5px 7px; }
  .idx-head { gap: 6px; margin-bottom: 3px; font-size: 11.5px; }
  .idx-price { font-size: 12px; }
  .tab { font-size: 10px; padding: 2px 6px; }
}
</style>
