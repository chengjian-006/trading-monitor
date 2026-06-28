<script setup lang="ts">
// 个股行情主体(速览 + 信号 + 分时 + 日K + 大单), 按 code/name 自取数。
// 整页(IntraDayView)与通用弹窗(StockDetailModal)共用此组件, 避免两套各自演化出不一致。
import { onMounted, ref, computed, watch } from 'vue'
import { NCard, NSpin, NTabs, NTabPane, NButton } from 'naive-ui'
import {
  fetchIntraday, fetchBigOrders, fetchSignalMarkers,
  fetchKline, fetchKlineWeek, fetchKlineMarkersDaily, fetchStockSummary,
  type IntradayPoint, type BigOrders, type BigOrderTick, type SignalMarker, type DailyMarker,
  type StockSummary,
} from '../../api/kline'
import type { KLineBar } from '../../types'
import IntradayChart from './IntradayChart.vue'
import KLineChart from './KLineChart.vue'
import StrategyText from '../stock/StrategyText.vue'
import { useStockStore } from '../../stores/stock'
import { useResponsive } from '../../composables/useResponsive'

const { isPhone } = useResponsive()

// compact: 弹框用——图更矮、大单明细默认折叠, 一屏更紧凑; 整页用默认(大图+大单展开)
const props = defineProps<{ code: string; name?: string; compact?: boolean }>()
// 头部速览数据上抛给外层(弹窗头部显示现价/涨跌幅)
const emit = defineEmits<{ summary: [StockSummary | null] }>()

const summary = ref<StockSummary | null>(null)
const activeChart = ref<'intraday' | 'kline' | 'week'>('intraday')
const boExpanded = ref(false)
const loading = ref(false)
const intradayData = ref<IntradayPoint[]>([])
const preClose = ref(0)
const bigOrders = ref<BigOrders | null>(null)
const markers = ref<SignalMarker[]>([])
// 历史分时回放: selectedDate='' = 今日(实时); 否则=点日K某天后回放该交易日归档分时
const selectedDate = ref('')

const klineLoading = ref(false)
const klineData = ref<KLineBar[]>([])
const weekLoading = ref(false)
const weekData = ref<KLineBar[]>([])
const klineMarkers = ref<DailyMarker[]>([])

// 我的策略(红框区): 从自选池按 code 取该票已设策略
const stockStore = useStockStore()
const myStrategy = computed(() => stockStore.stocks.find(s => s.code === props.code)?.strategy?.trim() || '')
// 我的策略默认折叠, 点标题展开/收起; 切换股票时重新折叠
const strategyOpen = ref(false)
watch(() => props.code, () => { strategyOpen.value = false })

function fmtAmount(yuan: number): string {
  const v = Math.abs(yuan)
  if (v >= 1e8) return `${(yuan / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(yuan / 1e4).toFixed(0)}万`
  return `${yuan.toFixed(0)}`
}

const bigOrderRecent = computed<(BigOrderTick & { dir: 'buy' | 'sell' })[]>(() => {
  const bo = bigOrders.value
  if (!bo) return []
  const merged = [
    ...bo.big_buys.map(t => ({ ...t, dir: 'buy' as const })),
    ...bo.big_sells.map(t => ({ ...t, dir: 'sell' as const })),
  ]
  merged.sort((a, b) => (a.time < b.time ? 1 : -1))
  return merged.slice(0, 20)
})

async function load() {
  if (!props.code) return
  const code = props.code
  const date = selectedDate.value
  loading.value = true
  intradayData.value = []
  preClose.value = 0
  bigOrders.value = null
  markers.value = []

  // 历史回放: 取该日归档分时 + 当日买卖点, 不补实时(历史日无实时)。大单仅当日故不取。
  if (date) {
    const [snap, mk] = await Promise.allSettled([
      fetchIntraday(code, date),
      fetchSignalMarkers(code, date),
    ])
    if (props.code !== code || selectedDate.value !== date) return   // 期间切了票/换了日期
    markers.value = mk.status === 'fulfilled' ? mk.value as SignalMarker[] : []
    if (snap.status === 'fulfilled') {
      intradayData.value = snap.value.points
      preClose.value = snap.value.pre_close || 0
    }
    loading.value = false
    return
  }

  // 今日: 分时 DB 优先 — 先并发拿快照(秒返)+信号标记+大单; 快照命中即出图撤 spinner, 再后台补实时整体替换
  const [snap, mk, bo] = await Promise.allSettled([
    fetchIntraday(code, '', 'snapshot'),
    fetchSignalMarkers(code),
    fetchBigOrders(code),
  ])
  if (props.code !== code || selectedDate.value !== date) return   // 期间切了票/选了历史日, 丢弃本次结果
  markers.value = mk.status === 'fulfilled' ? mk.value as SignalMarker[] : []
  bigOrders.value = bo.status === 'fulfilled' ? bo.value : null
  if (snap.status === 'fulfilled' && snap.value.points.length) {
    intradayData.value = snap.value.points
    preClose.value = snap.value.pre_close || 0
    loading.value = false           // 快照已可看, 先撤转圈
  }
  // 后台补实时: 成功则用权威分时(含数据源均价线+最新分钟)整体替换; 失败/空保留快照
  try {
    const live = await fetchIntraday(code)
    if (props.code === code && selectedDate.value === date && live.points.length) {
      intradayData.value = live.points
      preClose.value = live.pre_close || preClose.value
    }
  } catch {
    /* 保留快照 */
  } finally {
    if (props.code === code && selectedDate.value === date) loading.value = false   // 非自选无快照时由此撤 spinner
  }
}

// 点日K某根K线 → 跳分时Tab回放当天分时(点今天那根=回实时)
function onKlineDayClick(date: string) {
  const d = new Date()   // 用本地日期(非 UTC), 避免时区把"今天"算偏
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  selectedDate.value = date === today ? '' : date
  activeChart.value = 'intraday'
  load()
}

// 分时图上「回今日」: 从历史回放切回实时
function backToToday() {
  if (selectedDate.value) {
    selectedDate.value = ''
    load()
  }
}

async function loadKline() {
  if (!props.code) return
  klineLoading.value = true
  try {
    const [k, m] = await Promise.allSettled([
      fetchKline(props.code, 250),   // 多取历史(约1年), 默认窗口仍落最近40根, 缩小可看更早
      fetchKlineMarkersDaily(props.code, 150),
    ])
    klineData.value = k.status === 'fulfilled' && Array.isArray(k.value) ? k.value as KLineBar[] : []
    klineMarkers.value = m.status === 'fulfilled' ? m.value as DailyMarker[] : []
  } finally {
    klineLoading.value = false
  }
}

async function loadWeek() {
  if (!props.code) return
  weekLoading.value = true
  try {
    const r = await fetchKlineWeek(props.code, 80)
    weekData.value = Array.isArray(r) ? r : []
  } catch { weekData.value = [] }
  finally { weekLoading.value = false }
}

async function loadSummary() {
  if (!props.code) { summary.value = null; emit('summary', null); return }
  const s = await fetchStockSummary(props.code)
  summary.value = s
  emit('summary', s)
}

async function loadAll() {
  if (!props.code) return
  summary.value = null
  boExpanded.value = false
  selectedDate.value = ''   // 切票回到"今日"(直接赋值, 由下方显式调用 load)
  load()
  loadKline()
  loadSummary()
}

function pctText(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}
// 量能倍数 → 缩量/放量文案
const volText = computed(() => {
  const r = summary.value?.vol_ratio_avg10
  if (r == null) return null
  return r < 1 ? `缩量${r.toFixed(2)}x` : `放量${r.toFixed(2)}x`
})
// 图表高度: 桌面 480; 小屏降到 max(240,30vh) 一屏更友好; compact(弹窗)桌面也略矮
const chartHeight = computed(() => {
  if (isPhone.value) {
    const vh = typeof window !== 'undefined' ? window.innerHeight : 800
    return Math.max(240, Math.round(vh * 0.3))
  }
  return props.compact ? 420 : 480
})
// MA 位置标签取括号前的核心词(强势/震荡/偏弱/弱势/极弱)
const maTag = computed(() => {
  const p = summary.value?.ma_status
  if (!p) return null
  const m = p.match(/^[^（(]+/)
  return m ? m[0] : p
})

onMounted(loadAll)

watch(activeChart, (v) => {
  if (v === 'week' && weekData.value.length === 0) loadWeek()
})
// 弹框复用同一组件、切换不同股票时重新取数
watch(() => props.code, loadAll)
</script>

<template>
  <div class="stock-charts">
    <div v-if="!code" class="empty">缺少股票代码</div>
    <template v-else>
      <!-- 速览条: MA位置 / 量能 / 换手 / 振幅 / 5日涨幅 + 题材 -->
      <div v-if="summary" class="overview">
        <div class="ov-facts">
          <span v-if="maTag" class="ov-tag" :class="maTag">{{ maTag }}</span>
          <span v-if="volText" class="ov-fact">{{ volText }}</span>
          <span v-if="summary.turnover != null" class="ov-fact">换手 {{ summary.turnover.toFixed(1) }}%</span>
          <span v-if="summary.amplitude != null" class="ov-fact">振幅 {{ summary.amplitude.toFixed(1) }}%</span>
          <span v-if="summary.pct_5d != null" class="ov-fact">5日 <b :class="summary.pct_5d >= 0 ? 'up' : 'down'">{{ pctText(summary.pct_5d) }}</b></span>
        </div>
        <div v-if="summary.concept" class="ov-concept" :title="summary.concept">题材: {{ summary.concept }}</div>
      </div>

      <!-- 我的策略(红框区): 自选已设策略时显示, 默认折叠, 点标题展开 -->
      <div v-if="myStrategy" class="ov-strategy" :class="{ collapsed: !strategyOpen }">
        <button class="ovs-label" type="button" @click="strategyOpen = !strategyOpen">
          <span>📋 我的策略</span>
          <span class="ovs-arrow">{{ strategyOpen ? '收起 ▲' : '展开 ▼' }}</span>
        </button>
        <StrategyText v-show="strategyOpen" :text="myStrategy" />
      </div>

      <!-- 信号条: 临近买点(高亮) / 最新信号 -->
      <div v-if="summary && (summary.near_buy || summary.latest_signal)" class="sigbar">
        <span v-if="summary.near_buy" class="sig-near" :class="summary.near_buy.tier >= 2 ? 'hit' : 'close'">
          {{ summary.near_buy.tier >= 2 ? '⚡ 触发' : '○ 临近' }} {{ summary.near_buy.name || '买点' }}
          <template v-if="summary.near_buy.dist != null"> · 距线 {{ summary.near_buy.dist.toFixed(1) }}%</template>
        </span>
        <span v-if="summary.latest_signal" class="sig-last">
          最近信号 {{ summary.latest_signal.name }}（{{ summary.latest_signal.date }}）
        </span>
      </div>

      <!-- 分时 / 日K: 两个Tab切换, 默认分时。display-directive=if(默认): 切换时图按需挂载,
           避免 lightweight-charts 在隐藏容器(宽度0)里渲染异常。 -->
      <NCard size="small" :style="{ marginBottom: compact ? '0' : '14px' }">
        <NTabs v-model:value="activeChart" type="line" animated size="small" pane-style="padding-top:6px">
          <!-- 分时 -->
          <NTabPane name="intraday" tab="分时">
            <!-- 历史回放条: 仅在点日K进入历史日时出现, 一键回今日 -->
            <div v-if="selectedDate" class="intraday-datebar">
              <span class="idb-hint">📼 历史回放 · {{ selectedDate }}</span>
              <NButton size="tiny" tertiary @click="backToToday">← 回今日</NButton>
            </div>
            <div v-else class="intraday-datebar idb-tip">在「日K」点某根K线 → 回放当天分时</div>
            <div v-if="loading" style="display:flex;align-items:center;justify-content:center;padding:70px 0">
              <NSpin size="medium" />
            </div>
            <template v-else-if="intradayData.length">
              <IntradayChart :data="intradayData" :height="chartHeight" :markers="markers" :pre-close="preClose" />
              <div v-if="markers.length" class="marker-legend">
                <span class="lg up">▲ 买</span>
                <span class="lg down">▼ 卖</span>
                <span class="lg warn">▼ 减</span>
                <span class="lg-note">共 {{ markers.length }} 个买卖点（来自系统信号，已固化）</span>
              </div>
            </template>
            <div v-else class="empty" style="padding:40px 0">
              {{ selectedDate ? `${selectedDate} 无归档分时` : '暂无分时数据（非交易时段或无数据）' }}
            </div>
          </NTabPane>

          <!-- 日K -->
          <NTabPane name="kline" tab="日K">
            <div v-if="klineLoading" style="display:flex;align-items:center;justify-content:center;padding:70px 0">
              <NSpin size="medium" />
            </div>
            <template v-else-if="klineData.length">
              <div class="kl-hint">默认40日 · 滚轮缩放看更多 · 点某根K线看当天分时</div>
              <KLineChart :data="klineData" :height="chartHeight" :markers="klineMarkers" :default-bars="40" @day-click="onKlineDayClick" />
              <div v-if="klineMarkers.length" class="marker-legend">
                <span class="lg up">▲ 买</span>
                <span class="lg down">▼ 卖</span>
                <span class="lg warn">▼ 减</span>
                <span class="lg-note">近 150 天共 {{ klineMarkers.length }} 个买卖点（落在触发日）</span>
              </div>
            </template>
            <div v-else class="empty" style="padding:40px 0">暂无日K数据</div>
          </NTabPane>
          <!-- 周K -->
          <NTabPane name="week" tab="周K">
            <div v-if="weekLoading" style="display:flex;align-items:center;justify-content:center;padding:70px 0">
              <NSpin size="medium" />
            </div>
            <template v-else-if="weekData.length">
              <div class="kl-hint">默认40周 · 滚轮缩放看更多</div>
              <KLineChart :data="weekData" :height="chartHeight" :default-bars="40" />
            </template>
            <div v-else class="empty" style="padding:40px 0">暂无周K数据</div>
          </NTabPane>
        </NTabs>
      </NCard>

      <NCard v-if="!loading && !selectedDate" title="大单异动" size="small" :style="{ marginTop: compact ? '10px' : '0' }">
        <template #header-extra><span class="sub">≥1500万</span></template>
        <template v-if="bigOrders && (bigOrders.big_buy_count || bigOrders.big_sell_count)">
          <div class="bo-summary">
            <div class="bo-cell">
              <div class="bo-label">大买</div>
              <div class="bo-val up">{{ bigOrders.big_buy_count }}笔 / {{ fmtAmount(bigOrders.big_buy_amount) }}</div>
            </div>
            <div class="bo-cell">
              <div class="bo-label">大卖</div>
              <div class="bo-val down">{{ bigOrders.big_sell_count }}笔 / {{ fmtAmount(bigOrders.big_sell_amount) }}</div>
            </div>
            <div class="bo-cell">
              <div class="bo-label">净额</div>
              <div class="bo-val" :class="bigOrders.net_big_amount >= 0 ? 'up' : 'down'">
                {{ bigOrders.net_big_amount >= 0 ? '+' : '-' }}{{ fmtAmount(bigOrders.net_big_amount) }}
              </div>
            </div>
          </div>
          <!-- 紧凑模式默认折叠明细, 点开再看; 整页默认展开 -->
          <div v-if="compact && !boExpanded" class="bo-toggle" role="button" tabindex="0"
               @click="boExpanded = true" @keydown.enter="boExpanded = true">
            展开逐笔明细（{{ bigOrderRecent.length }}）▾
          </div>
          <div v-else class="bo-list">
            <div v-for="(t, i) in bigOrderRecent" :key="i" class="bo-row">
              <span class="bo-time">{{ t.time }}</span>
              <span class="bo-tag" :class="t.dir === 'buy' ? 'up' : 'down'">{{ t.dir === 'buy' ? '买' : '卖' }}</span>
              <span class="bo-price">{{ t.price.toFixed(2) }}</span>
              <span class="bo-hands">{{ t.hands }}手</span>
              <span class="bo-amt" :class="t.dir === 'buy' ? 'up' : 'down'">{{ fmtAmount(t.amount) }}</span>
            </div>
          </div>
        </template>
        <div v-else class="bo-empty">今日暂无大单（或非交易时段）</div>
      </NCard>
    </template>
  </div>
</template>

<style scoped>
.card-t { font-size: 14px; font-weight: 600; color: var(--text1); }
.kl-hint { font-size: 11px; color: var(--text2); margin-bottom: 4px; }
/* 历史分时回放条 */
.intraday-datebar { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; min-height: 22px; }
.intraday-datebar .idb-hint { font-size: 12px; color: #d9820a; font-weight: 600; }
.intraday-datebar.idb-tip { font-size: 11px; color: var(--text2); }
/* 弹窗紧凑态: 分时与日K左右并排; 窄屏回退上下堆叠 */
.chart-row.side-by-side { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 10px; align-items: start; }
@media (max-width: 768px) {
  .chart-row.side-by-side { grid-template-columns: 1fr; }
}
.empty { text-align: center; padding: 60px 0; color: var(--text2); }
.marker-legend { display: flex; align-items: center; gap: 12px; margin-top: 8px; font-size: 12px; }
.marker-legend .lg.up { color: #cf222e; }
.marker-legend .lg.down { color: #1a7f37; }
.marker-legend .lg.warn { color: #f59e0b; }
.marker-legend .lg-note { color: var(--text2); margin-left: auto; }
.sub { font-size: 11px; color: var(--text2); }
.bo-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 10px; }
.bo-cell { background: var(--card2, #f7f8fa); border-radius: 6px; padding: 8px 10px; text-align: center; }
.bo-label { font-size: 11px; color: var(--text2); margin-bottom: 2px; }
.bo-val { font-size: 14px; font-weight: 600; font-family: monospace; font-variant-numeric: tabular-nums; }
.bo-list { max-height: 280px; overflow-y: auto; overscroll-behavior: contain; }
.bo-row { display: grid; grid-template-columns: 56px 28px 1fr 64px 72px; align-items: center; gap: 6px; font-size: 12px; font-family: monospace; font-variant-numeric: tabular-nums; padding: 4px 0; border-bottom: 1px dashed var(--border, #f0f0f0); }
.bo-time { color: var(--text2); }
.bo-tag { text-align: center; border-radius: 3px; font-size: 11px; }
.bo-hands { text-align: right; color: var(--text2); }
.bo-amt { text-align: right; font-weight: 600; }
.up { color: #cf222e; }
.down { color: #1a7f37; }
.bo-tag.up { background: rgba(207, 34, 46, 0.1); }
.bo-tag.down { background: rgba(26, 127, 55, 0.1); }
.bo-empty { text-align: center; padding: 18px 0; color: var(--text2); font-size: 12px; }
.bo-toggle { text-align: center; padding: 8px 0 2px; color: #2e9eff; font-size: 12px; cursor: pointer; touch-action: manipulation; }
.bo-toggle:hover { text-decoration: underline; }

/* 速览条 */
.overview { background: var(--card2, #f7f8fa); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
.ov-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; font-size: 12px; color: var(--text1); font-variant-numeric: tabular-nums; }
.ov-fact { color: var(--text2); }
.ov-fact b { font-weight: 600; }
.ov-tag { font-weight: 700; padding: 1px 8px; border-radius: 4px; font-size: 12px; }
.ov-tag.强势, .ov-tag.偏强 { color: #cf222e; background: rgba(207,34,46,0.1); }
.ov-tag.震荡 { color: #d9820a; background: rgba(217,130,10,0.1); }
.ov-tag.偏弱, .ov-tag.弱势 { color: #2e9eff; background: rgba(46,158,255,0.1); }
.ov-tag.极弱 { color: #1a7f37; background: rgba(26,127,55,0.1); }
.ov-concept { margin-top: 6px; font-size: 11.5px; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 我的策略 红框区 */
.ov-strategy { margin-bottom: 10px; padding: 8px 10px; background: #fff8f0; border: 1px solid #ffd9b3; border-radius: 6px; }
.ov-strategy.collapsed { padding-bottom: 8px; }
.ov-strategy .ovs-label { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 0; border: none; background: none; cursor: pointer; font-size: 11px; font-weight: 700; color: #d9820a; margin-bottom: 3px; -webkit-tap-highlight-color: transparent; }
.ov-strategy.collapsed .ovs-label { margin-bottom: 0; }
.ov-strategy .ovs-arrow { font-size: 10.5px; font-weight: 600; color: #b8731a; }

/* 信号条 */
.sigbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; margin-bottom: 10px; font-size: 12px; }
.sig-near { font-weight: 600; padding: 2px 8px; border-radius: 4px; }
.sig-near.hit { color: #cf222e; background: rgba(207,34,46,0.12); }
.sig-near.close { color: #d9820a; background: rgba(217,130,10,0.12); }
.sig-last { color: var(--text2); }

/* 小屏: 大单逐笔列收窄(时间/手数缩列+字号), 速览标签收紧间距防溢出 */
@media (max-width: 768px) {
  .bo-row { grid-template-columns: 44px 24px 1fr 52px 60px; gap: 4px; font-size: 11px; }
  .bo-summary { gap: 6px; }
  .bo-cell { padding: 6px 6px; }
  .bo-val { font-size: 13px; }
  .ov-facts { gap: 5px 8px; font-size: 11.5px; }
  .ov-tag, .ov-fact { font-size: 11.5px; }
}
</style>
