<script setup lang="ts">
// 股票池右侧固定图表栏 (v1.7.759, 参照同花顺): 上分时 + 下日K + 顶部关键数字条。
// 复用现成图元(IntradayChart/KLineChart)与 kline 接口, 零新增图表引擎/后端调用。
// 数据: 分时走 snapshot→live 两段(同 StockCharts); 日K默认最近60根; 关键数字直接用选中行的
// Stock 对象(自选池已实时刷新, 与表格同源, 不额外拉数)。仅桌面渲染, 移动端维持点代码弹窗。
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { NIcon } from 'naive-ui'
import { ChevronForwardOutline, OpenOutline } from '@vicons/ionicons5'
import {
  fetchIntraday, fetchKline, fetchSignalMarkers, fetchKlineMarkersDaily,
  type IntradayPoint, type SignalMarker, type DailyMarker,
} from '../../api/kline'
import type { KLineBar, Stock } from '../../types'
import { formatYi } from '../../utils/formatAmount'
import { useUiStore } from '../../stores/ui'
import IntradayChart from '../chart/IntradayChart.vue'
import KLineChart from '../chart/KLineChart.vue'

const props = defineProps<{ stock: Stock | null }>()
const emit = defineEmits<{ collapse: [] }>()

const ui = useUiStore()

const KLINE_DAYS = 40   // 日K默认显示最近40个交易日(v1.7.766由60改40), 可在图上拖动/缩放看更早

const loading = ref(false)
const intradayData = ref<IntradayPoint[]>([])
const preClose = ref(0)
const markers = ref<SignalMarker[]>([])
const klineData = ref<KLineBar[]>([])
const klineMarkers = ref<DailyMarker[]>([])

const code = computed(() => props.stock?.code || '')
const name = computed(() => props.stock?.name || '')

// ── 关键数字条(现价/涨幅/成交额/换手/量比): 直接用选中行, 与表格同源 ──
const s = computed(() => props.stock)
const pctClass = computed(() => {
  const p = s.value?.pct_change
  return p == null ? '' : p >= 0 ? 'up' : 'down'
})
const amountHuge = computed(() => (s.value?.amount ?? 0) > 5e9)   // >50亿紫色, 与表格同口径
function fmtPct(v: number | null | undefined) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

// ── 分时: snapshot 秒返先出图, 再后台补 live 整体替换(同 StockCharts) ──
async function loadIntraday() {
  const c = code.value
  if (!c) return
  const [snap, mk] = await Promise.allSettled([
    fetchIntraday(c, '', 'snapshot'),
    fetchSignalMarkers(c),
  ])
  if (code.value !== c) return   // 期间切了票, 丢弃
  markers.value = mk.status === 'fulfilled' ? (mk.value as SignalMarker[]) : []
  if (snap.status === 'fulfilled' && snap.value.points.length) {
    intradayData.value = snap.value.points
    preClose.value = snap.value.pre_close || 0
    loading.value = false
  }
  try {
    const live = await fetchIntraday(c)
    if (code.value === c && live.points.length) {
      intradayData.value = live.points
      preClose.value = live.pre_close || preClose.value
    }
  } catch { /* 保留快照 */ }
  finally { if (code.value === c) loading.value = false }
}

async function loadKline() {
  const c = code.value
  if (!c) return
  const [k, m] = await Promise.allSettled([
    fetchKline(c, KLINE_DAYS),
    fetchKlineMarkersDaily(c, KLINE_DAYS),
  ])
  if (code.value !== c) return
  klineData.value = k.status === 'fulfilled' && Array.isArray(k.value) ? (k.value as KLineBar[]) : []
  klineMarkers.value = m.status === 'fulfilled' ? (m.value as DailyMarker[]) : []
}

function reload() {
  if (!code.value) {
    intradayData.value = []; klineData.value = []; markers.value = []; klineMarkers.value = []
    return
  }
  loading.value = true
  intradayData.value = []; preClose.value = 0
  loadIntraday()
  loadKline()
}

watch(code, reload)

// ── 盘中每15秒补刷分时(切走标签页/非交易时段不刷) ──
function isTradingNow(): boolean {
  const d = new Date()
  if (d.getDay() === 0 || d.getDay() === 6) return false
  const m = d.getHours() * 60 + d.getMinutes()
  return (m >= 570 && m <= 690) || (m >= 780 && m <= 900)   // 09:30-11:30 / 13:00-15:00
}
let timer: number | null = null
onMounted(() => {
  reload()
  timer = window.setInterval(() => {
    if (!document.hidden && isTradingNow() && code.value) loadIntraday()
  }, 15_000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })

function openFull() {
  if (code.value) ui.openStock(code.value, name.value)
}
</script>

<template>
  <aside class="pool-chart-panel">
    <div class="pcp-head">
      <button class="pcp-collapse" title="收起图表栏" aria-label="收起图表栏" @click="emit('collapse')">
        <NIcon :component="ChevronForwardOutline" />
      </button>
      <template v-if="stock">
        <span class="pcp-name">{{ name }}</span>
        <button class="pcp-code" title="打开完整详情(分时/日K/周K/大单)" @click="openFull">
          {{ code }}<NIcon :component="OpenOutline" />
        </button>
      </template>
      <span v-else class="pcp-empty-title">点左侧任意一行查看图表</span>
    </div>

    <template v-if="stock">
      <!-- 关键数字条 -->
      <div class="pcp-metrics">
        <div class="m-item"><span class="m-label">现价</span><b class="m-val" :class="pctClass">{{ s?.price != null ? s.price.toFixed(2) : '—' }}</b></div>
        <div class="m-item"><span class="m-label">涨幅</span><b class="m-val" :class="pctClass">{{ fmtPct(s?.pct_change) }}</b></div>
        <div class="m-item"><span class="m-label">成交额</span><b class="m-val" :class="{ 'amount-huge': amountHuge }">{{ formatYi(s?.amount) }}</b></div>
        <div class="m-item"><span class="m-label">换手</span><b class="m-val">{{ s?.turnover != null ? s.turnover.toFixed(2) + '%' : '—' }}</b></div>
        <div class="m-item"><span class="m-label">量比</span><b class="m-val">{{ s?.volume_ratio != null ? s.volume_ratio.toFixed(2) : '—' }}</b></div>
      </div>

      <!-- 分时图 -->
      <div class="pcp-chart-block">
        <div class="pcp-chart-label">分时</div>
        <IntradayChart :data="intradayData" :markers="markers" :pre-close="preClose" :height="190" />
      </div>

      <!-- 日K图(最近60个交易日, 可拖动缩放看更早) -->
      <div class="pcp-chart-block">
        <div class="pcp-chart-label">日K · 近{{ KLINE_DAYS }}日</div>
        <KLineChart :data="klineData" :markers="klineMarkers" :default-bars="KLINE_DAYS" :height="250" />
      </div>
    </template>

    <div v-else class="pcp-empty">
      <p>点左侧任意一行</p>
      <p class="pcp-empty-sub">右侧显示该股分时 + 日K</p>
    </div>
  </aside>
</template>

<style scoped>
/* v1.7.764: 宽度完全由父级(PoolView)内联 style 控制(可拖拽), 这里不再写死 width/flex,
   免得 scoped 的 flex 简写与父级内联 flex-basis 打架导致拖拽不生效。 */
.pool-chart-panel {
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border-default);
  background: var(--card);
  overflow-y: auto;
  overflow-x: hidden;
}
.pcp-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-default);
  position: sticky;
  top: 0;
  background: var(--card);
  z-index: 2;
}
.pcp-collapse {
  display: inline-flex;
  align-items: center;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--fg-subtle);
  font-size: 18px;
  padding: 2px;
}
.pcp-collapse:hover { color: var(--text1); }
.pcp-name { font-weight: 700; color: var(--text1); font-size: 14px; }
.pcp-code {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--accent-fg);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.pcp-code:hover { text-decoration: underline; }
.pcp-empty-title { color: var(--fg-subtle); font-size: 13px; }

.pcp-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-default);
}
.m-item { display: flex; align-items: baseline; gap: 4px; }
.m-label { font-size: 11px; color: var(--fg-subtle); }
.m-val { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--text1); }
.m-val.up { color: var(--up-fg); }
.m-val.down { color: var(--down-fg); }
.m-val.amount-huge { color: #7c3aed; }

.pcp-chart-block { padding: 6px 8px 2px; }
.pcp-chart-label { font-size: 11px; color: var(--fg-subtle); margin: 2px 0 2px 4px; }

.pcp-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--fg-subtle);
  gap: 4px;
}
.pcp-empty p { margin: 0; }
.pcp-empty-sub { font-size: 12px; }

</style>
