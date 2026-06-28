<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, type IChartApi, type ISeriesApi } from 'lightweight-charts'
import type { IntradayPoint, SignalMarker } from '../../api/kline'

const props = defineProps<{
  data: IntradayPoint[]
  height?: number
  markers?: SignalMarker[]
  preClose?: number   // 真实昨收(着色基准, 与涨跌幅同源); 缺省/0 时退化为按当日首点(开盘)着色
}>()

const chartEl = ref<HTMLDivElement>()
const overlayEl = ref<HTMLDivElement>()
let chart: IChartApi | null = null
let priceSeries: ISeriesApi<'Area'> | null = null
let avgSeries: ISeriesApi<'Line'> | null = null
let volumeSeries: ISeriesApi<'Histogram'> | null = null
let resizeObs: ResizeObserver | null = null
// 买卖点覆盖层重定位(initChart 内赋值, resize 时复用); 见下方"买卖点标记"段
let repositionOverlay: (() => void) | null = null

function timeToSeconds(timeStr: string): number {
  const parts = timeStr.split(' ')
  const hhmm = parts.length > 1 ? parts[1] : parts[0]
  const [h, m] = hhmm.split(':').map(Number)
  return h * 3600 + m * 60
}

function initChart() {
  if (!chartEl.value) return
  if (chart) { chart.remove(); chart = null }
  if (!Array.isArray(props.data) || !props.data.length) return

  // 着色基准优先用真实昨收(与涨跌幅同源, 杜绝"分时绿、涨幅红"); 拿不到才退用当日首点(开盘)
  const preClose = props.preClose && props.preClose > 0 ? props.preClose : props.data[0].price
  const lastPrice = props.data[props.data.length - 1].price
  const up = lastPrice >= preClose

  const lineColor = up ? '#CF222E' : '#1A7F37'
  const areaTop = up ? 'rgba(207,34,46,0.12)' : 'rgba(26,127,55,0.12)'
  const areaBottom = 'transparent'

  const dataMap = new Map<number, IntradayPoint>()
  props.data.forEach(p => dataMap.set(timeToSeconds(p.time), p))

  const chartHeight = props.height || 300
  chart = createChart(chartEl.value, {
    width: chartEl.value.clientWidth,
    height: chartHeight,
    layout: { background: { type: 'solid' as any, color: '#fff' }, textColor: '#666', fontSize: 11 },
    // 十字光标的时间标签走 localization(横轴刻度的 tickMarkFormatter 管不到它);
    // time 值是"当日秒数"非 Unix 时间戳, 不设会显示成 1970-01-01
    localization: {
      timeFormatter: (t: number) => {
        const hh = Math.floor(t / 3600)
        const mm = Math.floor((t % 3600) / 60)
        return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
      },
    },
    grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
    // 左右轴必须用【完全相同】的 scaleMargins, 否则同一像素行在两轴上对应不同值 —
    //   价格走右轴、涨跌幅%走左轴, 之前右轴漏设 scaleMargins 吃了默认 top0.2/bottom0.1,
    //   把涨停价(最高点)压到距顶20%处, 左轴%却在距顶2%, 导致涨停(+9.95%)在左轴读成~+9.0%。
    //   底部留 0.24 给成交量副图(top0.78), 价格线不压到量柱上。
    rightPriceScale: { borderColor: '#e0e0e0', scaleMargins: { top: 0.04, bottom: 0.24 } },
    // 左轴显示涨跌幅%(相对昨收), 与右轴价格刻度一一对齐(0.00%=昨收线)
    leftPriceScale: { visible: true, borderColor: '#e0e0e0', scaleMargins: { top: 0.04, bottom: 0.24 } },
    timeScale: {
      visible: true,
      borderColor: '#e0e0e0',
      timeVisible: false,
      tickMarkFormatter: (t: number) => {
        const sec = t as number
        const hh = Math.floor(sec / 3600)
        const mm = Math.floor((sec % 3600) / 60)
        return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
      },
    },
    crosshair: { mode: 0 },
    handleScroll: { mouseWheel: false, pressedMouseMove: false },
    handleScale: false,
  })

  priceSeries = chart.addAreaSeries({
    lineColor,
    topColor: areaTop,
    bottomColor: areaBottom,
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    crosshairMarkerVisible: true,
  })

  avgSeries = chart.addLineSeries({
    color: '#f59e0b',
    lineWidth: 1,
    lineStyle: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  })

  // 涨跌幅%系列: 线本身透明(不画), 只为驱动左侧%刻度轴
  const pctSeries = chart.addLineSeries({
    priceScaleId: 'left',
    color: 'rgba(0,0,0,0)',
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    priceFormat: { type: 'custom', minMove: 0.01, formatter: (v: number) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%' },
  })

  // 成交量副图: 占下方约 22%, 按分钟涨跌染色 (A股 红涨绿跌)
  volumeSeries = chart.addHistogramSeries({
    priceScaleId: 'volume',
    priceFormat: { type: 'volume' },
    priceLineVisible: false,
    lastValueVisible: false,
  })
  chart.priceScale('volume').applyOptions({
    scaleMargins: { top: 0.78, bottom: 0 },
    borderVisible: false,
  })

  const priceData: any[] = []
  const avgData: any[] = []
  const pctData: any[] = []
  const volData: any[] = []
  let prevPrice = preClose
  for (const p of props.data) {
    const t = timeToSeconds(p.time)
    priceData.push({ time: t, value: p.price })
    avgData.push({ time: t, value: p.avg_price })
    pctData.push({ time: t, value: preClose > 0 ? (p.price - preClose) / preClose * 100 : 0 })
    const volColor = p.price >= prevPrice ? 'rgba(207,34,46,0.45)' : 'rgba(26,127,55,0.45)'
    volData.push({ time: t, value: p.volume || 0, color: volColor })
    prevPrice = p.price
  }

  priceSeries.setData(priceData)
  avgSeries.setData(avgData)
  pctSeries.setData(pctData)
  volumeSeries.setData(volData)

  // 买卖点标记: 原生 series marker(小箭头, 红买点画在红价格线上)几乎看不见, 改用 DOM 覆盖层 ——
  //   竖向虚线(贯穿价格区一眼定位) + 白描边圆锚点(精确贴在成交价) + 顶部药丸标签(买/卖/减 时刻 价)。
  //   lightweight-charts 坐标: timeToCoordinate 返回的 x 相对"主窗格"(不含左侧%刻度轴), 故 +左轴宽;
  //   本图禁用了平移/缩放, 坐标只随 resize/数据变化, 无需订阅滚动。同时刻同方向多信号合并计数(卖2)。
  const TOP_FRAC = 0.04, PRICE_BOT_FRAC = 0.76   // 竖线覆盖价格区(与 scaleMargins 对齐, 不压到量柱)
  repositionOverlay = () => {
    const ov = overlayEl.value
    if (!ov || !chart || !priceSeries) return
    ov.innerHTML = ''
    if (!props.markers || !props.markers.length) return
    const ts = chart.timeScale()
    let leftW = 0
    try { leftW = chart.priceScale('left').width() } catch { leftW = 0 }
    const H = ov.clientHeight || chartHeight
    const lineTop = H * TOP_FRAC
    const lineBot = H * PRICE_BOT_FRAC
    const W = ov.clientWidth

    type MG = { t: number; isBuy: boolean; isReduce: boolean; n: number; price: number | null; time: string; name: string }
    const groups = new Map<string, MG>()
    for (const m of props.markers) {
      if (m.direction === 'plunge') continue
      const t = timeToSeconds(m.time)
      const isBuy = m.direction === 'buy'
      const isReduce = m.direction === 'reduce'
      const side = isBuy ? 'b' : (isReduce ? 'r' : 's')
      const key = `${t}|${side}`
      const g = groups.get(key)
      if (g) g.n++
      else groups.set(key, { t, isBuy, isReduce, n: 1, price: m.price ?? (dataMap.get(t)?.price ?? null), time: m.time, name: m.signal_name || '' })
    }

    for (const g of groups.values()) {
      const cx = ts.timeToCoordinate(g.t as any)
      if (cx == null) continue
      const x = leftW + (cx as number)
      const color = g.isBuy ? '#cf222e' : (g.isReduce ? '#f59e0b' : '#1a7f37')
      const sideTxt = g.isBuy ? '买' : (g.isReduce ? '减' : '卖')
      const label = sideTxt + (g.n > 1 ? String(g.n) : '')
      const priceTxt = g.price != null ? ` ¥${g.price.toFixed(2)}` : ''

      // 竖向虚线(贯穿价格区)
      const line = document.createElement('div')
      line.className = 'sig-vline'
      line.style.left = `${x}px`
      line.style.top = `${lineTop}px`
      line.style.height = `${Math.max(0, lineBot - lineTop)}px`
      line.style.borderLeftColor = color
      ov.appendChild(line)

      // 圆锚点(贴在成交价)
      if (g.price != null) {
        const y = priceSeries.priceToCoordinate(g.price)
        if (y != null) {
          const dot = document.createElement('div')
          dot.className = 'sig-dot'
          dot.style.left = `${x}px`
          dot.style.top = `${y as number}px`
          dot.style.background = color
          ov.appendChild(dot)
        }
      }

      // 顶部药丸标签
      const pill = document.createElement('div')
      pill.className = 'sig-pill'
      pill.style.borderColor = color
      pill.style.color = color
      pill.style.top = `${Math.max(2, lineTop - 8)}px`
      pill.innerHTML = `<b>${label}</b>${g.time}${priceTxt}`
      pill.title = `${g.name}${g.name ? ' · ' : ''}${g.time}${priceTxt}`
      ov.appendChild(pill)
      // 居中并夹在容器内(防出右/左边界)
      const pw = pill.offsetWidth
      const left = Math.min(Math.max(2, x - pw / 2), Math.max(2, W - pw - 2))
      pill.style.left = `${left}px`
    }
  }

  // 坐标只有在图表完成布局/绘制后才有效(timeToCoordinate/priceToCoordinate 提前调会返回 null 或过期值,
  // 单帧 rAF 不保险)。fitContent 会触发可见区间变化, 订阅它在绘制后回调里重定位; 再加 rAF + 延时兜底。
  const onRange = () => requestAnimationFrame(() => repositionOverlay?.())
  chart.timeScale().subscribeVisibleLogicalRangeChange(onRange)
  chart.timeScale().fitContent()
  requestAnimationFrame(() => repositionOverlay?.())
  setTimeout(() => repositionOverlay?.(), 150)

  resizeObs = new ResizeObserver(() => {
    if (chart && chartEl.value) chart.applyOptions({ width: chartEl.value.clientWidth })
    requestAnimationFrame(() => repositionOverlay?.())
  })
  resizeObs.observe(chartEl.value)
}

onMounted(() => nextTick(initChart))
watch([() => props.data, () => props.markers, () => props.preClose], () => nextTick(initChart), { deep: true })
onUnmounted(() => {
  resizeObs?.disconnect()
  repositionOverlay = null
  if (overlayEl.value) overlayEl.value.innerHTML = ''
  chart?.remove()
  chart = null
})
</script>

<template>
  <div class="intraday-wrap">
    <div ref="chartEl" class="intraday-chart" />
    <div ref="overlayEl" class="signal-overlay" />
  </div>
</template>

<style scoped>
.intraday-wrap {
  position: relative;
  width: 100%;
}
.intraday-chart {
  width: 100%;
  min-height: 200px;
}
.intraday-chart :deep(a[href*="tradingview"]) {
  display: none !important;
}
/* 买卖点覆盖层: 盖满图表, 默认不挡十字光标; 仅药丸可交互(hover 看模型名) */
/* z-index 必须 >3: lightweight-charts 内部 canvas 用 z-index 1/2/3, 不设则覆盖层(z-auto)被画在
   canvas 下面、被不透明白底完全盖住(元素已创建但不可见) —— 这是买卖点"渲染了却看不到"的根因 */
.signal-overlay {
  position: absolute;
  inset: 0;
  z-index: 5;
  pointer-events: none;
  overflow: hidden;
}
.signal-overlay :deep(.sig-vline) {
  position: absolute;
  width: 0;
  border-left: 1.5px dashed;
  opacity: 0.55;
}
.signal-overlay :deep(.sig-dot) {
  position: absolute;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.18), 0 1px 3px rgba(0, 0, 0, 0.35);
  transform: translate(-50%, -50%);
}
.signal-overlay :deep(.sig-pill) {
  position: absolute;
  pointer-events: auto;
  white-space: nowrap;
  font-size: 11px;
  line-height: 1.1;
  padding: 3px 7px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
  font-weight: 600;
  cursor: default;
}
.signal-overlay :deep(.sig-pill b) {
  font-weight: 800;
  margin-right: 3px;
}
/* 移动端: 药丸更紧凑, 锚点略小, 防小屏拥挤 */
@media (max-width: 768px) {
  .signal-overlay :deep(.sig-pill) {
    font-size: 10px;
    padding: 2px 5px;
  }
  .signal-overlay :deep(.sig-dot) {
    width: 9px;
    height: 9px;
  }
}
</style>
