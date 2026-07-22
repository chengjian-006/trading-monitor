<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, LineStyle, type IChartApi, type ISeriesApi, type IPriceLine } from 'lightweight-charts'
import type { KLineBar } from '../../types'
import type { DailyMarker } from '../../api/kline'
import { useResponsive } from '../../composables/useResponsive'

const { isPhone } = useResponsive()

const props = defineProps<{
  data: KLineBar[]
  height?: number
  markers?: DailyMarker[]
  defaultBars?: number   // 默认显示最近 N 根(可缩放/拖动看更早), 不传则铺满全部
}>()

// 点击某根K线 → 抛出该交易日(YYYY-MM-DD), 供外层跳分时图看当天分时回放
const emit = defineEmits<{ dayClick: [date: string] }>()

// 机构级 (v1.7.650): 蜡烛涨跌色对齐冷调 Token 值(红涨绿跌不变, 仅微调至冷调)
const A_UP = '#E0342A'
const A_DOWN = '#12A06B'
const MA_DEFS: { key: 'ma5' | 'ma10' | 'ma20' | 'ma60'; color: string; label: string }[] = [
  { key: 'ma5', color: '#f59e0b', label: 'MA5' },
  { key: 'ma10', color: '#3b82f6', label: 'MA10' },
  { key: 'ma20', color: '#a855f7', label: 'MA20' },
  { key: 'ma60', color: '#64748b', label: 'MA60' },
]
// 成交量均量线(量MA5/量MA10): 同花顺式, 判放量/缩量
const VMA_DEFS: { period: number; color: string }[] = [
  { period: 5, color: '#f59e0b' },
  { period: 10, color: '#3b82f6' },
]

const chartEl = ref<HTMLDivElement>()
const tipEl = ref<HTMLDivElement>()
let chart: IChartApi | null = null
let candle: ISeriesApi<'Candlestick'> | null = null
let resizeObs: ResizeObserver | null = null
// 按日期索引当天的买卖点(给十字线悬浮详情用): 哪个买点 + 几点 + 价格
let markersByDate: Record<string, DailyMarker[]> = {}
// 买卖点 markers(常驻) 与 最高/最低价 markers(随可见区间变) 分开存, 每次合并 setMarkers
let signalMarkers: any[] = []
// 可见区间高/低价的两条水平虚线(v1.7.730 取代原实心圆点 marker)。
// 区间一变就要重算, 故存引用以便先删后建 —— 不删会越堆越多条线。
let hiLine: IPriceLine | null = null
let loLine: IPriceLine | null = null
function clearExtremeLines() {
  if (candle) {
    if (hiLine) candle.removePriceLine(hiLine)
    if (loLine) candle.removePriceLine(loLine)
  }
  hiLine = null
  loLine = null
}
let dataArr: KLineBar[] = []
let idxByDate: Record<string, number> = {}

// 顶部读数条(随十字线变; 不悬浮时显示最新一根)
interface Legend {
  date: string; o: number; h: number; l: number; c: number
  chgPct: number | null; up: boolean; ma: (number | null)[]
}
const legend = ref<Legend | null>(null)

// lightweight-charts 把 'yyyy-mm-dd' 字符串归一成 BusinessDay 对象({year,month,day}),
// 故十字线/点击回调里 param.time 是对象而非原字符串 —— 统一转回 'yyyy-mm-dd' 才能当 idxByDate 键。
function timeToDateStr(t: unknown): string {
  if (t && typeof t === 'object' && 'year' in (t as any)) {
    const d = t as { year: number; month: number; day: number }
    return `${d.year}-${String(d.month).padStart(2, '0')}-${String(d.day).padStart(2, '0')}`
  }
  return String(t ?? '')
}

function fmt(v: number | null | undefined): string {
  return v == null ? '-' : v.toFixed(2)
}
function dirText(dir: string) {
  return dir === 'buy' ? '买' : (dir === 'reduce' ? '减' : '卖')
}

function setLegendAt(i: number) {
  if (i < 0 || i >= dataArr.length) return
  const b = dataArr[i]
  const prev = i > 0 ? dataArr[i - 1].close : b.open
  const chgPct = prev ? (b.close - prev) / prev * 100 : null
  legend.value = {
    date: b.date, o: b.open, h: b.high, l: b.low, c: b.close,
    chgPct, up: b.close >= b.open,
    ma: MA_DEFS.map(m => (b[m.key] ?? null) as number | null),
  }
}

// 可见区间内的最高/最低价 → 打标签(同花顺式 39.14 / 24.70)
function refreshExtremes(fromLogical: number, toLogical: number) {
  if (!candle || !dataArr.length) return
  const n = dataArr.length
  const from = Math.max(0, Math.floor(fromLogical))
  const to = Math.min(n - 1, Math.ceil(toLogical))
  if (from > to) return
  let hi = -Infinity, hiIdx = from, lo = Infinity, loIdx = from
  for (let i = from; i <= to; i++) {
    if (dataArr[i].high > hi) { hi = dataArr[i].high; hiIdx = i }
    if (dataArr[i].low < lo) { lo = dataArr[i].low; loIdx = i }
  }
  // v1.7.730: 原来用 setMarkers 打两个实心圆点(shape:'circle')标高低价, 两个毛病:
  //   ① 圆点压在最高/最低那根 K 线的极值端上, 恰好挡住最想看的地方;
  //   ② 与买卖点走的是同一套 markers, 视觉同级 → 圆点容易被误读成"这里有个信号"。
  // 改用原生 createPriceLine 画水平虚线 + 价格轴标签: 完全不遮挡 K 线, 与买卖点标记彻底分层,
  // 且虚线本身是可对照的基准 —— 能一眼看出现价距区间高/低点还有多远。
  // 高低点随可见区间变(缩放/拖动都会重算), 故每次先删旧线再建新线。
  clearExtremeLines()
  hiLine = candle.createPriceLine({
    price: hi, color: A_UP, lineWidth: 1, lineStyle: LineStyle.Dashed,
    axisLabelVisible: true, title: '高',
  })
  loLine = candle.createPriceLine({
    price: lo, color: A_DOWN, lineWidth: 1, lineStyle: LineStyle.Dashed,
    axisLabelVisible: true, title: '低',
  })
  // markers 现在只承载买卖点, 语义干净
  candle.setMarkers([...signalMarkers].sort((a, b) => (a.time < b.time ? -1 : 1)))
}

function destroy() {
  resizeObs?.disconnect()
  resizeObs = null
  chart?.remove()
  chart = null
  candle = null
  // chart.remove() 已连带销毁价格线, 这里只需清引用 —— 若留着旧引用, 下次 clearExtremeLines
  // 会拿它去新的 series 上 removePriceLine, 那是另一个图表的对象。
  hiLine = null
  loLine = null
}

function render() {
  if (!chartEl.value) return
  destroy()
  legend.value = null
  if (!Array.isArray(props.data) || !props.data.length) return
  dataArr = props.data

  // 小屏降高: 取 max(240, 30vh), 但不超过桌面给定高度; 桌面维持原 props.height||360
  const deskH = props.height || 360
  const phoneH = Math.min(deskH, Math.max(240, Math.round((typeof window !== 'undefined' ? window.innerHeight : 800) * 0.3)))
  chart = createChart(chartEl.value, {
    width: chartEl.value.clientWidth,
    height: isPhone.value ? phoneH : deskH,
    layout: { background: { type: 'solid' as any, color: '#fff' }, textColor: '#5A6472', fontSize: 11 },
    grid: { vertLines: { color: '#E6EAF0' }, horzLines: { color: '#E6EAF0' } },
    rightPriceScale: { borderColor: '#D2D8E1', scaleMargins: { top: 0.06, bottom: 0.26 } },
    timeScale: { borderColor: '#D2D8E1', timeVisible: false, secondsVisible: false },
    crosshair: { mode: 0 },
  })

  candle = chart.addCandlestickSeries({
    upColor: A_UP, downColor: A_DOWN,
    borderUpColor: A_UP, borderDownColor: A_DOWN,
    wickUpColor: A_UP, wickDownColor: A_DOWN,
    priceLineVisible: false, lastValueVisible: true,
  })
  const vol = chart.addHistogramSeries({
    priceScaleId: 'volume', priceFormat: { type: 'volume' },
    priceLineVisible: false, lastValueVisible: false,
  })
  chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, borderVisible: false })

  const maSeries: Record<string, ISeriesApi<'Line'>> = {}
  for (const m of MA_DEFS) {
    maSeries[m.key] = chart.addLineSeries({
      color: m.color, lineWidth: 1, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false,
    })
  }
  const vmaSeries = VMA_DEFS.map(v => chart!.addLineSeries({
    priceScaleId: 'volume', color: v.color, lineWidth: 1, priceLineVisible: false,
    lastValueVisible: false, crosshairMarkerVisible: false,
  }))

  const candleData: any[] = []
  const volData: any[] = []
  const vols: number[] = []
  const maData: Record<string, any[]> = { ma5: [], ma10: [], ma20: [], ma60: [] }
  idxByDate = {}
  props.data.forEach((b, i) => {
    const t = b.date
    idxByDate[t] = i
    candleData.push({ time: t, open: b.open, high: b.high, low: b.low, close: b.close })
    volData.push({ time: t, value: b.volume, color: b.close >= b.open ? 'rgba(207,34,46,0.4)' : 'rgba(26,127,55,0.4)' })
    vols.push(b.volume)
    for (const m of MA_DEFS) {
      const v = b[m.key]
      if (v != null) maData[m.key].push({ time: t, value: v })
    }
  })
  candle.setData(candleData)
  vol.setData(volData)
  for (const m of MA_DEFS) maSeries[m.key].setData(maData[m.key])
  // 均量线: 从成交量序列滚动平均
  VMA_DEFS.forEach((v, vi) => {
    const line: any[] = []
    for (let i = 0; i < vols.length; i++) {
      if (i + 1 < v.period) continue
      let s = 0
      for (let j = i + 1 - v.period; j <= i; j++) s += vols[j]
      line.push({ time: props.data[i].date, value: s / v.period })
    }
    vmaSeries[vi].setData(line)
  })

  // 买卖点标记: 买=红上箭头 卖=绿下箭头 减=橙下箭头, 落在触发日
  markersByDate = {}
  signalMarkers = []
  if (props.markers && props.markers.length) {
    signalMarkers = props.markers
      .filter(m => m.direction !== 'plunge')
      .map(m => {
        const isBuy = m.direction === 'buy'
        const isReduce = m.direction === 'reduce'
        ;(markersByDate[m.date] ||= []).push(m)
        return {
          time: m.date as any,
          position: (isBuy ? 'belowBar' : 'aboveBar') as any,
          color: isBuy ? A_UP : (isReduce ? '#f59e0b' : A_DOWN),
          shape: (isBuy ? 'arrowUp' : 'arrowDown') as any,
          text: isBuy ? '买' : (isReduce ? '减' : '卖'),
          size: 0.6,   // 箭头改细, 别跟K线柱混淆
        }
      })
      .sort((a, b) => (a.time < b.time ? -1 : 1))
    candle.setMarkers(signalMarkers)
  }

  // 十字线: 更新顶部读数条 + 落在有买卖点的那天弹浮层
  chart.subscribeCrosshairMove((param) => {
    const date = timeToDateStr(param.time)
    // 1) 顶部读数条
    if (date && idxByDate[date] != null) setLegendAt(idxByDate[date])
    else setLegendAt(dataArr.length - 1)
    // 2) 买卖点浮层
    const tip = tipEl.value
    if (!tip) return
    const hits = date ? markersByDate[date] : undefined
    if (!hits || !param.point) { tip.style.display = 'none'; return }
    tip.innerHTML = `<div class="mk-date">${date}</div>` + hits.map(h => {
      const cls = h.direction === 'buy' ? 'buy' : (h.direction === 'reduce' ? 'reduce' : 'sell')
      const price = h.price != null ? ` ¥${h.price}` : ''
      const tm = h.time ? ` ${h.time}` : ''
      return `<div class="mk-row"><span class="mk-tag ${cls}">${dirText(h.direction)}</span>`
        + `<span class="mk-name">${h.signal_name || ''}</span>`
        + `<span class="mk-meta">${tm}${price}</span></div>`
    }).join('')
    tip.style.display = 'block'
    const w = chartEl.value?.clientWidth || 0
    const left = Math.min(param.point.x + 12, Math.max(0, w - tip.offsetWidth - 8))
    tip.style.left = `${left}px`
    tip.style.top = `8px`
  })

  // 点击某根K线 → 抛出该交易日, 外层跳分时图回放当天分时(点在空白/非交易日忽略)
  chart.subscribeClick((param) => {
    const date = timeToDateStr(param.time)
    if (date && idxByDate[date] != null) emit('dayClick', date)
  })

  // 默认只显示最近 defaultBars 根(数据多取了历史, 缩小/向左拖即可看更早); 不传或数据不足则铺满
  const total = candleData.length
  const dft = props.defaultBars ?? 0
  if (dft > 0 && total > dft) {
    chart.timeScale().setVisibleLogicalRange({ from: total - dft, to: total + 1 })
  } else {
    chart.timeScale().fitContent()
  }
  // 最高/最低价标签随可见区间刷新; 先按当前窗口打一次
  chart.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (r) refreshExtremes(r.from, r.to) })
  const r0 = chart.timeScale().getVisibleLogicalRange()
  if (r0) refreshExtremes(r0.from, r0.to)
  // 初始读数条显示最新一根
  setLegendAt(dataArr.length - 1)

  resizeObs = new ResizeObserver(() => {
    if (!chart || !chartEl.value) return
    chart.applyOptions({ width: chartEl.value.clientWidth })
    // v1.7.766: 宽度变化后按当前默认窗口重新铺满 —— barSpacing 固定像素, 只改 width 不重设区间
    //   会让K线停在旧位置, 拉宽右侧留白、缩窄被截。resize 只在宽度变时触发(滚动/缩放不触发),
    //   故这里按 defaultBars 重铺不会打断用户平时的左右拖看历史。
    const t = candleData.length
    const d = props.defaultBars ?? 0
    if (d > 0 && t > d) chart.timeScale().setVisibleLogicalRange({ from: t - d, to: t + 1 })
    else chart.timeScale().fitContent()
  })
  resizeObs.observe(chartEl.value)
}

onMounted(() => nextTick(render))
watch([() => props.data, () => props.markers], () => nextTick(render), { deep: true })
onUnmounted(destroy)
</script>

<template>
  <div class="kline-wrap">
    <!-- 顶部读数条: 随十字线显示当根 开高低收/涨跌幅 + 各均线值(像同花顺)。
         浮于图左上角(不占布局高度), 使日K图与分时图顶部对齐。 -->
    <div class="kl-legend">
      <template v-if="legend">
        <span class="kl-date">{{ legend.date }}</span>
        <span class="kl-ohlc" :class="legend.up ? 'up' : 'down'">
          开{{ fmt(legend.o) }} 高{{ fmt(legend.h) }} 低{{ fmt(legend.l) }} 收{{ fmt(legend.c) }}
        </span>
        <span v-if="legend.chgPct != null" class="kl-chg" :class="legend.chgPct >= 0 ? 'up' : 'down'">
          {{ legend.chgPct >= 0 ? '+' : '' }}{{ legend.chgPct.toFixed(2) }}%
        </span>
        <span class="kl-sep" />
        <span v-for="(m, i) in MA_DEFS" :key="m.key" class="kl-ma" :style="{ color: m.color }">
          {{ m.label }} {{ fmt(legend.ma[i]) }}
        </span>
      </template>
    </div>
    <div ref="chartEl" class="kline-chart" />
    <div ref="tipEl" class="mk-tip" style="display:none" />
  </div>
</template>

<style scoped>
.kl-legend {
  position: absolute;
  top: 3px;
  left: 6px;
  right: 6px;
  z-index: 4;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 10px;
  font-size: 11px;
  color: var(--text2);
  font-variant-numeric: tabular-nums;
  pointer-events: none;   /* 浮层不挡图的十字线/缩放交互 */
}
.kl-date { color: #999; }
.kl-ohlc, .kl-chg, .kl-ma { background: rgba(255,255,255,0.72); border-radius: 2px; padding: 0 2px; }
.kl-ohlc.up, .kl-chg.up { color: #cf222e; }
.kl-ohlc.down, .kl-chg.down { color: #1a7f37; }
.kl-chg { font-weight: 700; }
.kl-sep { width: 1px; height: 11px; background: #e5e5e5; }
.kl-ma { font-weight: 600; }
.kline-wrap { position: relative; }
.kline-chart { width: 100%; min-height: 240px; }
.kline-chart :deep(a[href*="tradingview"]) { display: none !important; }
/* 买卖点悬浮详情浮层 */
.mk-tip {
  position: absolute; z-index: 5; pointer-events: none;
  background: rgba(255,255,255,0.97); border: 1px solid #e5e5e5; border-radius: 6px;
  box-shadow: 0 2px 10px rgba(0,0,0,0.12); padding: 6px 9px; font-size: 12px;
  /* 自定义div浮层(非NaiveUI tooltip), 全局mobile.css兜底覆盖不到, 这里自防超边界 */
  max-width: clamp(180px, 90vw, 240px);
}
.mk-tip :deep(.mk-date) { font-size: 11px; color: #999; margin-bottom: 3px; }
.mk-tip :deep(.mk-row) { display: flex; align-items: baseline; gap: 5px; line-height: 1.6; }
.mk-tip :deep(.mk-tag) { flex-shrink: 0; color: #fff; font-weight: 600; border-radius: 3px; padding: 0 5px; font-size: 11px; }
.mk-tip :deep(.mk-tag.buy) { background: #cf222e; }
.mk-tip :deep(.mk-tag.sell) { background: #1a7f37; }
.mk-tip :deep(.mk-tag.reduce) { background: #f59e0b; }
.mk-tip :deep(.mk-name) { font-weight: 600; color: rgba(0,0,0,0.85); }
.mk-tip :deep(.mk-meta) { color: #888; }

/* 小屏: 图例8-10项允许折行并缩字号, 别撑爆图顶 */
@media (max-width: 768px) {
  .kl-legend { font-size: 10px; gap: 1px 6px; }
  .mk-tip { font-size: 11px; padding: 5px 7px; }
}
</style>
