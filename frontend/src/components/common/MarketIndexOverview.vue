<script setup lang="ts">
// 大盘指数概览 — 参照同花顺/东财盘面风格 (v1.7.x)
// 左: 指数 tab 切换 + 单张大分时图 + 最新/最高/最低/成交额 stat 行
// 右: 成交额数据栏 (今日实时成交额 / 预测全天 / 涨跌家数 / 涨停跌停)
// 替代原 2x2 四小图 MarketIndexPanel, PC/移动 均自适应
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { NPopover } from 'naive-ui'
import { formatYi, formatYiFromYi, formatYiDelta } from '../../utils/formatAmount'
import { fetchIndexTrends, fetchIndexDaily, fetchMarketOverview, fetchRegime, fetchTurnover, fetchVolumeSurge,
         type IndexTrendData, type IndexDailyData, type MarketOverview, type RegimeData, type TurnoverData, type VolumeSurgeItem } from '../../api/market-report'
import IndexChartCard from './IndexChartCard.vue'
import { estimateFullDayAmount } from '../../utils/intradayEstimator'

const router = useRouter()
const intradayMap = ref<Record<string, IndexTrendData>>({})
const dailyMap = ref<Record<string, IndexDailyData>>({})
const overview = ref<MarketOverview | null>(null)
const regime = ref<RegimeData | null>(null)
const turnover = ref<TurnoverData | null>(null)
const surge = ref<VolumeSurgeItem[]>([])

let intradayTimer: number | null = null
let dailyTimer: number | null = null

// 顺序固定: 上证 / 深证 / 创业板 / 科创50
const SYMBOLS: Array<{ key: string; defaultName: string }> = [
  { key: 'sh000001', defaultName: '上证指数' },
  { key: 'sz399001', defaultName: '深证成指' },
  { key: 'sz399006', defaultName: '创业板指' },
  { key: 'sh000688', defaultName: '科创50' },
  { key: 'sz399317', defaultName: '全A指数' },   // 国证A指(全部A股)
  { key: 'hkHSI', defaultName: '恒生指数' },      // 港股(腾讯分时/日K, 现价从全球指数区兜底)
  { key: 'hkHSTECH', defaultName: '恒生科技' },
]

const selected = ref('sh000001')

async function loadIntraday() {
  try {
    const [intr, ov, rg, tv, sg] = await Promise.all([
      fetchIndexTrends().catch(() => ({} as Record<string, IndexTrendData>)),
      fetchMarketOverview().catch(() => null),
      fetchRegime().catch(() => null),
      fetchTurnover().catch(() => null),
      fetchVolumeSurge().catch(() => []),
    ])
    if (intr && Object.keys(intr).length) intradayMap.value = intr
    if (ov) overview.value = ov
    if (rg) regime.value = rg
    if (tv) turnover.value = tv
    surge.value = sg
  } catch { /* silent */ }
}

async function loadDaily() {
  try {
    const d = await fetchIndexDaily(30)
    if (d) dailyMap.value = d
  } catch { /* silent */ }
}

// A股指数在 indices; 港股(恒生/恒生科技)现价涨跌在 global_indices(全球指数区), 这里统一兜底查找
function indexInfo(name: string) {
  return (overview.value?.indices || []).find(i => i.name === name)
      || (overview.value?.global_indices || []).find(i => i.name === name)
}
function priceOf(name: string): number {
  return (indexInfo(name) as any)?.price ?? 0
}
function pctOf(name: string): number {
  return (indexInfo(name) as any)?.pct_change ?? 0
}

const tabs = computed(() => SYMBOLS.map(s => {
  const intr = intradayMap.value[s.key] || null
  const name = intr?.name || s.defaultName
  return { key: s.key, name, pct: pctOf(name) }
}))

const sel = computed(() => {
  const intr = intradayMap.value[selected.value] || null
  const name = intr?.name || SYMBOLS.find(s => s.key === selected.value)?.defaultName || '—'
  const prices = (intr?.trends || []).map(t => t.price).filter(p => p != null) as number[]
  return {
    key: selected.value,
    name,
    intraday: intr,
    daily: dailyMap.value[selected.value] || null,
    price: priceOf(name),
    pct: pctOf(name),
    high: prices.length ? Math.max(...prices) : 0,
    low: prices.length ? Math.min(...prices) : 0,
    amount: intr?.amount ?? 0,
  }
})

function pctClass(p: number) { return p > 0 ? 'up' : p < 0 ? 'down' : '' }
function pctText(p: number) { return (p >= 0 ? '+' : '') + p.toFixed(2) + '%' }
function openStock(code: string, name: string) {
  router.push({ path: '/intraday', query: { code, name } })
}

// 成交额格式: 入参单位「亿」, ≥1万亿 显示万亿
function fmtYi(yi: number | null | undefined): string {
  if (yi == null || !isFinite(yi) || yi <= 0) return '—'
  return formatYiFromYi(yi)   // 成交额统一亿(2位)
}
// 个股成交额格式: 入参单位「元」, 统一亿(2位)
function fmtAmtYuan(yuan: number | null | undefined): string {
  if (yuan == null || !isFinite(yuan) || yuan <= 0) return '—'
  return formatYi(yuan)
}
// 带正负号的差额 (较上一日), 入参单位「亿」
function fmtDelta(yi: number | null): string {
  return formatYiDelta(yi)
}

const todayAmountYi = computed(() => regime.value?.raw?.total_amount_yi ?? turnover.value?.today_yi ?? 0)
const ma5Yi = computed(() => turnover.value?.ma5_yi ?? null)
const ma60Yi = computed(() => turnover.value?.ma60_yi ?? null)

// 已过交易时段占比(线性): 仅用于"已过 X%"展示 + 早盘/盘中判断, 不再用它外推全天
function tradingFraction(): number {
  const now = new Date()
  const m = now.getHours() * 60 + now.getMinutes()
  const AM_O = 570, AM_C = 690, PM_O = 780, PM_C = 900
  let elapsed: number
  if (m <= AM_O) elapsed = 0
  else if (m <= AM_C) elapsed = m - AM_O
  else if (m < PM_O) elapsed = 120
  else if (m <= PM_C) elapsed = 120 + (m - PM_O)
  else elapsed = 240
  return Math.min(Math.max(elapsed / 240, 0), 1)
}
// 当前已过交易时段占比(盘后=1, 盘前=0); 用于判断"实时累计"已走完多少
const elapsedFraction = computed(() => tradingFraction())
const projectedAmountYi = computed(() => {
  if (turnover.value?.projected_yi) return turnover.value.projected_yi  // 后端口径优先
  const t = todayAmountYi.value
  if (!t) return 0
  const f = elapsedFraction.value
  if (f < 0.05) return t          // 早盘极早, 占比太小不外推, 直接显示当前
  // 用 U型时点系数估算(11:30=55% 而非线性的50%), 避免上午前高后低导致高估; 估算器不可用时退回线性
  const est = estimateFullDayAmount(t)
  return Math.round(est && est > 0 ? est : t / f)
})
// 较上一日: 用"预测全天"对比"昨日全天"(同口径全天 vs 全天), 不再拿盘中累计去比昨日全天(盘中必为大额负数, 是误导)
const prevDeltaYi = computed(() => {
  const p = turnover.value?.prev_yi
  if (p == null) return null
  const f = elapsedFraction.value
  if (f < 0.05) return null       // 盘前/极早盘无有效预测, 不显示对比
  const full = projectedAmountYi.value
  if (!full) return null
  return full - p
})
// 盘中(占比<1)显示的是"预计"对比, 盘后是已成事实
const prevDeltaIsProjected = computed(() => elapsedFraction.value < 1)

const stats = computed(() => overview.value?.market_stats)

onMounted(() => {
  loadIntraday()
  loadDaily()
  intradayTimer = window.setInterval(loadIntraday, 60_000)
  dailyTimer = window.setInterval(loadDaily, 5 * 60_000)
})
onUnmounted(() => {
  if (intradayTimer) clearInterval(intradayTimer)
  if (dailyTimer) clearInterval(dailyTimer)
})
</script>

<template>
  <div class="index-overview">
    <!-- 左: 指数 tab + 大分时图 -->
    <div class="left">
      <div class="tab-row">
        <button
          v-for="t in tabs"
          :key="t.key"
          :class="['idx-tab', { active: selected === t.key }]"
          @click="selected = t.key"
        >
          <span class="t-name">{{ t.name }}</span>
          <span class="t-pct" :class="pctClass(t.pct)">{{ pctText(t.pct) }}</span>
        </button>
      </div>

      <div class="stat-line">
        <span class="s-item">最新 <b :class="pctClass(sel.pct)">{{ sel.price ? sel.price.toFixed(2) : '—' }}</b></span>
        <span class="s-item">最高 <b class="up">{{ sel.high ? sel.high.toFixed(2) : '—' }}</b></span>
        <span class="s-item">最低 <b class="down">{{ sel.low ? sel.low.toFixed(2) : '—' }}</b></span>
        <span class="s-item">成交额 <b>{{ fmtYi(sel.amount) }}</b></span>
      </div>

      <IndexChartCard
        :symbol-key="sel.key"
        :intraday="sel.intraday"
        :daily="sel.daily"
        :current-price="sel.price"
        :pct-change="sel.pct"
        :show-head="false"
        :height="260"
      />
    </div>

    <!-- 右: 成交额数据栏 -->
    <div class="rail">
      <div class="rail-block">
        <div class="rail-label">今日实时成交额</div>
        <div class="rail-big">{{ fmtYi(todayAmountYi) }}</div>
        <div v-if="elapsedFraction < 1 && elapsedFraction > 0" class="rail-sub muted">
          截至当前 · 交易时段已过 {{ Math.round(elapsedFraction * 100) }}%
        </div>
      </div>
      <div class="rail-block">
        <div class="rail-label">预测全天成交额</div>
        <div class="rail-big">{{ fmtYi(projectedAmountYi) }}</div>
        <div v-if="prevDeltaYi != null" class="rail-sub" :class="prevDeltaYi >= 0 ? 'up' : 'down'">
          {{ prevDeltaIsProjected ? '预计较上一日' : '较上一日' }} {{ fmtDelta(prevDeltaYi) }}
        </div>
      </div>
      <div class="rail-mini">
        <div class="m-row">
          <span class="m-k">5日均额</span>
          <span class="m-v">{{ fmtYi(ma5Yi) }}</span>
        </div>
        <div class="m-row">
          <span class="m-k">60日均额</span>
          <span class="m-v">{{ fmtYi(ma60Yi) }}</span>
        </div>
        <template v-if="stats">
          <div class="m-row">
            <span class="m-k">涨 / 跌家数</span>
            <span class="m-v"><b class="up">{{ stats.up_count }}</b> / <b class="down">{{ stats.down_count }}</b></span>
          </div>
          <div class="m-row">
            <span class="m-k">涨停 / 跌停</span>
            <span class="m-v"><b class="up">{{ stats.limit_up }}</b> / <b class="down">{{ stats.limit_down }}</b></span>
          </div>
        </template>
      </div>

      <NPopover trigger="click" placement="bottom-end" :disabled="!surge.length" style="max-height: 320px; overflow: auto">
        <template #trigger>
          <div class="surge-link" :class="{ disabled: !surge.length }">
            今日成交放量股<span v-if="surge.length" class="surge-cnt">{{ surge.length }}</span> ›
          </div>
        </template>
        <div class="surge-pop">
          <div class="surge-pop-hd">自选股池 · 当日量比前10</div>
          <div v-for="it in surge" :key="it.code" class="surge-row" role="button" tabindex="0" :aria-label="`${it.name} 分时`" @click="openStock(it.code, it.name)" @keydown.enter="openStock(it.code, it.name)">
            <span class="sr-name">{{ it.name }}</span>
            <span class="sr-vr">量比 {{ it.volume_ratio.toFixed(2) }}</span>
            <span class="sr-amt">额 {{ fmtAmtYuan(it.amount) }}</span>
            <span class="sr-pct" :class="pctClass(it.pct_change)">{{ pctText(it.pct_change) }}</span>
          </div>
        </div>
      </NPopover>
    </div>
  </div>
</template>

<style scoped>
.index-overview {
  background: #fff;
  border: 1px solid var(--border, #efeff5);
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  gap: 16px;
  align-items: stretch;
}
.left { flex: 1; min-width: 0; }
.rail {
  width: 200px;
  flex-shrink: 0;
  border-left: 1px solid var(--border, #efeff5);
  padding-left: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 指数 tab 行 */
.tab-row { display: flex; gap: 4px; flex-wrap: wrap; }
.idx-tab {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 5px 12px 6px;
  border: none;
  background: transparent;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  touch-action: manipulation;
  border-radius: 4px 4px 0 0;
  transition: background 0.15s;
}
.idx-tab:hover { background: rgba(0,0,0,0.03); }
.idx-tab.active { background: rgba(9,105,218,0.06); border-bottom-color: var(--primary, #0969da); }
.t-name { font-size: 13px; font-weight: 600; color: var(--text1, #1f2328); }
.t-pct { font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }

.stat-line {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin: 8px 0 4px;
  font-size: 12px;
  color: var(--text2, #656d76);
}
.stat-line b { font-family: monospace; font-weight: 700; color: var(--text1, #1f2328); margin-left: 2px; }

/* 右侧数据栏 */
.rail-block { }
.rail-label { font-size: 12px; color: var(--text2, #656d76); margin-bottom: 4px; }
.rail-big { font-size: 22px; font-weight: 800; color: var(--text1, #1f2328); font-family: monospace; }
.rail-sub { font-size: 12px; font-weight: 600; margin-top: 2px; font-variant-numeric: tabular-nums; }
.rail-sub.muted { font-weight: 400; color: #999; }
.rail-mini { margin-top: auto; display: flex; flex-direction: column; gap: 8px; padding-top: 10px; border-top: 1px dashed var(--border, #efeff5); }
.m-row { display: flex; justify-content: space-between; align-items: baseline; font-size: 12px; color: var(--text2, #656d76); }
.m-v b { font-family: monospace; font-weight: 700; }

.surge-link {
  font-size: 12px;
  color: var(--primary, #0969da);
  cursor: pointer;
  padding-top: 8px;
  border-top: 1px dashed var(--border, #efeff5);
  user-select: none;
  touch-action: manipulation;
}
.surge-link:hover { text-decoration: underline; }
.surge-link.disabled { color: var(--text2, #9aa0a6); cursor: default; text-decoration: none; }
.surge-cnt {
  display: inline-block;
  margin: 0 2px 0 4px;
  min-width: 16px;
  padding: 0 5px;
  border-radius: 8px;
  background: rgba(9,105,218,0.1);
  font-size: 11px;
  text-align: center;
}
.surge-pop { min-width: 248px; }
.surge-pop-hd { font-size: 11px; color: var(--text2, #656d76); margin-bottom: 6px; }
.surge-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 4px;
  border-radius: 4px;
  cursor: pointer;
  touch-action: manipulation;
}
.surge-row:hover { background: rgba(0,0,0,0.04); }
.sr-name { flex: 1; font-size: 13px; font-weight: 600; color: var(--text1, #1f2328); }
.sr-vr { font-size: 12px; color: var(--text2, #656d76); font-family: monospace; }
.sr-amt { font-size: 12px; color: var(--text2, #656d76); font-family: monospace; min-width: 64px; text-align: right; }
.sr-pct { font-size: 12px; font-weight: 700; min-width: 56px; text-align: right; font-variant-numeric: tabular-nums; }

.up { color: #e53e3e; }
.down { color: #16a34a; }

@media (max-width: 768px) {
  .index-overview { flex-direction: column; gap: 10px; padding: 8px 10px; }
  .rail {
    width: auto;
    border-left: none;
    border-top: 1px solid var(--border, #efeff5);
    padding-left: 0;
    padding-top: 10px;
    flex-direction: row;
    flex-wrap: wrap;
    gap: 12px 20px;
    align-items: flex-end;
  }
  .rail-big { font-size: 18px; }
  .rail-mini { margin-top: 0; border-top: none; padding-top: 0; flex: 1; min-width: 130px; }
  .idx-tab { padding: 4px 9px 5px; }
}
</style>
