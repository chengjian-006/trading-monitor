<script setup lang="ts">
import { computed, h, ref, onMounted } from 'vue'
import { NRadioGroup, NRadioButton, NSelect, NDrawer, NDrawerContent, NSpin, NEmpty, NTag, NDataTable, NTooltip, NCollapse, NCollapseItem } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  fetchSignalMatrix,
  fetchSignalOutcomeStats,
  fetchSignalHistory,
  type SignalMatrixRow,
  type SignalOutcomeStatsItem,
} from '../api/signals'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { useResponsive } from '../composables/useResponsive'
import type { Signal } from '../types'
import ModelWeeklyPanel from '../components/signal/ModelWeeklyPanel.vue'
import ModelBacktestPanel from '../components/signal/ModelBacktestPanel.vue'

const message = useGlobalMessage()

// ────────────────────────────────────────────────
// 信号元数据 (id → 中文名 / 分组 / 推送档位).
// 用于 0 次命中信号的占位行 + 当后端 signal_group 缺失时的 fallback.
// 与 backend/services/signal_specs.py 的 SIGNAL_GROUP_MAP 对齐.
// ────────────────────────────────────────────────
interface SignalMeta {
  id: string
  name: string
  group: 'entry' | 'exit' | 'risk' | 'regime' | 'sector' | 'quality'
  direction: 'buy' | 'sell' | 'reduce' | '-'
  priority: 'strong' | 'middle' | 'weak'  // 推送档位
}

const SIGNAL_META: SignalMeta[] = [
  // entry
  { id: 'BUY_WEAK_EXTREME',        name: '弱势极限（左侧）', group: 'entry',   direction: 'buy',    priority: 'strong' },
  { id: 'BUY_STRONG_START',           name: '强势起点（右侧）', group: 'entry',   direction: 'buy',    priority: 'strong' },
  // exit
  { id: 'SELL_BREAK_MA5',               name: '短线卖一 (跌破MA5)',  group: 'exit',   direction: 'sell',   priority: 'strong' },
  { id: 'SELL_BREAK_MA10',               name: '短线卖二 (跌破MA10)', group: 'exit',   direction: 'sell',   priority: 'strong' },
  { id: 'SELL_BREAK_MA20',               name: '短线卖三 (跌破MA20)', group: 'exit',   direction: 'sell',   priority: 'strong' },
  { id: 'SELL_TAKE_PROFIT',             name: '+7% 减仓',           group: 'exit',   direction: 'reduce', priority: 'strong' },
  { id: 'SELL_TRAIL_STOP',      name: '追踪止盈',           group: 'exit',   direction: 'reduce', priority: 'strong' },
  { id: 'SELL_RR_TARGET',         name: '盈亏比止盈 (2R)',    group: 'exit',   direction: 'reduce', priority: 'strong' },
  { id: 'SELL_TIME_STOP',              name: '时间止损',           group: 'exit',   direction: 'reduce', priority: 'strong' },
  // risk
  { id: 'SELL_LOSS_5',                name: '浮亏 -5% 强档预警',     group: 'risk',   direction: 'reduce', priority: 'strong' },
  { id: 'SELL_LOSS_8',                name: '浮亏 -8% 二次预警',   group: 'risk',   direction: 'reduce', priority: 'strong' },
  { id: 'SELL_LOSS_10',               name: '浮亏 -10% 严重超止损', group: 'risk',  direction: 'sell',   priority: 'strong' },
  // regime(大盘急跌三条已退役 v1.7.737-751, 大盘预警统一走市场风险三档; 组保留给历史数据兜底)
  // sector
  { id: 'SECTOR_CAPITAL_INFLOW',  name: '资金回流·板块',      group: 'sector', direction: 'buy',    priority: 'strong' },
  // quality
  { id: 'SCORE_STRENGTH',       name: '真假强势评分',       group: 'quality', direction: '-',     priority: 'weak' },
  { id: 'SCORE_THEME',       name: '主流题材',           group: 'quality', direction: '-',     priority: 'weak' },
]

const META_BY_ID: Record<string, SignalMeta> = Object.fromEntries(SIGNAL_META.map(m => [m.id, m]))

const GROUP_LABEL: Record<SignalMeta['group'], string> = {
  entry: '买点',
  exit:  '卖点/减仓',
  risk:  '持仓风控',
  regime:'大盘+资金',
  sector:'板块',
  quality:'质量评分',
}
const GROUP_ORDER: SignalMeta['group'][] = ['entry', 'exit', 'risk', 'sector', 'quality']

const DIR_LABEL: Record<string, string> = { buy: '买', sell: '卖', reduce: '减仓', add: '加仓', '-': '—' }
const DIR_TYPE: Record<string, 'error' | 'success' | 'warning' | 'default'> = {
  buy: 'error', sell: 'success', reduce: 'warning', add: 'error', '-': 'default',
}
const PRIORITY_LABEL: Record<SignalMeta['priority'], string> = { strong: '强', middle: '中', weak: '弱' }

// ────────────────────────────────────────────────
// 数据加载
// ────────────────────────────────────────────────
const { isMobile } = useResponsive()

const days = ref(14)
const filterGroup = ref<string | null>(null)
const filterDirection = ref<string | null>(null)

const matrixDates = ref<string[]>([])
const matrixRows = ref<SignalMatrixRow[]>([])
const outcomeMap = ref<Record<string, SignalOutcomeStatsItem>>({})
const loading = ref(false)

async function loadAll() {
  loading.value = true
  try {
    const [matrix, outcome] = await Promise.all([
      fetchSignalMatrix(days.value),
      fetchSignalOutcomeStats(90),
    ])
    matrixDates.value = matrix.dates
    matrixRows.value = matrix.rows
    outcomeMap.value = outcome
  } catch {
    message.error('加载预警矩阵失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)

function changeDays(v: number) {
  days.value = v
  loadAll()
}

// ────────────────────────────────────────────────
// 合并 matrix + 未触发信号占位 → 完整行集合
// ────────────────────────────────────────────────
interface FullRow {
  signal_id: string
  signal_name: string
  group: SignalMeta['group']
  direction: string
  priority: SignalMeta['priority']
  counts: number[]      // 与 matrixDates 等长, 全 0 占位行也保留
  total: number
}

const fullRows = computed<FullRow[]>(() => {
  const dateLen = matrixDates.value.length || 1
  const hit = new Map<string, SignalMatrixRow>()
  for (const r of matrixRows.value) hit.set(r.signal_id, r)

  const list: FullRow[] = []
  // 已知所有 signal_id 都构造一行 (含 0 次命中的占位)
  for (const meta of SIGNAL_META) {
    const got = hit.get(meta.id)
    list.push({
      signal_id: meta.id,
      signal_name: got?.signal_name || meta.name,
      group: meta.group,
      direction: got?.direction || meta.direction,
      priority: meta.priority,
      counts: got ? got.counts : new Array(dateLen).fill(0),
      total: got?.total ?? 0,
    })
    hit.delete(meta.id)
  }
  // DB 里出现但 SIGNAL_META 没登记的 (异常情况, 也展示)
  for (const r of hit.values()) {
    list.push({
      signal_id: r.signal_id,
      signal_name: r.signal_name,
      group: ((r.signal_group as any) || 'quality') as SignalMeta['group'],
      direction: r.direction,
      priority: 'middle',
      counts: r.counts,
      total: r.total,
    })
  }
  return list
})

const filteredRows = computed<FullRow[]>(() => {
  let list = fullRows.value
  if (filterGroup.value) list = list.filter(r => r.group === filterGroup.value)
  if (filterDirection.value) list = list.filter(r => r.direction === filterDirection.value)
  return list
})

// 按 group 分组 (保持 GROUP_ORDER 顺序), 每组内按 total 倒序
interface GroupBlock { group: SignalMeta['group']; rows: FullRow[] }
const groupedRows = computed<GroupBlock[]>(() => {
  const byGroup = new Map<SignalMeta['group'], FullRow[]>()
  for (const r of filteredRows.value) {
    const arr = byGroup.get(r.group) || []
    arr.push(r)
    byGroup.set(r.group, arr)
  }
  const out: GroupBlock[] = []
  for (const g of GROUP_ORDER) {
    const rs = byGroup.get(g)
    if (rs && rs.length) {
      rs.sort((a, b) => b.total - a.total)
      out.push({ group: g, rows: rs })
    }
  }
  // 兜底: 不在 GROUP_ORDER 的 group
  for (const [g, rs] of byGroup.entries()) {
    if (!GROUP_ORDER.includes(g)) {
      rs.sort((a, b) => b.total - a.total)
      out.push({ group: g, rows: rs })
    }
  }
  return out
})

// ────────────────────────────────────────────────
// 汇总条
// ────────────────────────────────────────────────
const summary = computed(() => {
  const dateLen = matrixDates.value.length
  if (!dateLen) return { todayCount: 0, todayStrong: 0, todayMiddle: 0, deltaPct: null as number | null, hottest: null as FullRow | null }
  const todayIdx = dateLen - 1
  const prevIdx = dateLen - 2
  let todayCount = 0, todayStrong = 0, todayMiddle = 0, prevCount = 0
  let hottest: FullRow | null = null
  let hottestToday = -1
  for (const r of fullRows.value) {
    const todayN = r.counts[todayIdx] || 0
    const prevN = prevIdx >= 0 ? (r.counts[prevIdx] || 0) : 0
    todayCount += todayN
    prevCount += prevN
    if (r.priority === 'strong') todayStrong += todayN
    if (r.priority === 'middle') todayMiddle += todayN
    if (todayN > hottestToday) { hottestToday = todayN; hottest = r }
  }
  const deltaPct = prevCount > 0 ? Math.round((todayCount - prevCount) / prevCount * 100) : null
  return { todayCount, todayStrong, todayMiddle, deltaPct, hottest: hottestToday > 0 ? hottest : null }
})

// ────────────────────────────────────────────────
// 单元格热力色: 0 灰 / 1-2 浅 / 3-5 中 / 6-10 较深 / 11+ 深
// ────────────────────────────────────────────────
function cellBg(n: number): string {
  if (n === 0) return '#f3f4f6'
  if (n <= 2) return '#dbeafe'
  if (n <= 5) return '#93c5fd'
  if (n <= 10) return '#3b82f6'
  return '#1d4ed8'
}
function cellColor(n: number): string {
  if (n === 0) return '#cbd5e1'
  if (n <= 5) return '#1e3a8a'
  return '#ffffff'
}

function formatDateShort(d: string): string {
  // "2026-05-28" → "05-28"
  return d.length >= 10 ? d.slice(5) : d
}
function isToday(d: string): boolean {
  return d === matrixDates.value[matrixDates.value.length - 1]
}

// ────────────────────────────────────────────────
// 明细 Drawer
// ────────────────────────────────────────────────
const drawerOpen = ref(false)
const drawerTitle = ref('')
const drawerLoading = ref(false)
const drawerRows = ref<Signal[]>([])
const drawerCtx = ref<{ signal_id: string; signal_name: string; date: string; count: number } | null>(null)

async function openDetail(row: FullRow, dateIdx: number) {
  const date = matrixDates.value[dateIdx]
  const count = row.counts[dateIdx]
  if (!count) return
  drawerCtx.value = { signal_id: row.signal_id, signal_name: row.signal_name, date, count }
  drawerTitle.value = `${row.signal_name} · ${date} · ${count} 笔`
  drawerOpen.value = true
  drawerLoading.value = true
  drawerRows.value = []
  try {
    const list = await fetchSignalHistory(500, { date })
    drawerRows.value = list.filter(s => s.signal_id === row.signal_id)
  } catch {
    message.error('加载明细失败')
  } finally {
    drawerLoading.value = false
  }
}

// 点"5日成功率"→ 列出该信号近90天每一笔的成败明细
const OUTCOME_RANK: Record<string, number> = { success: 0, fail: 1, neutral: 2 }
async function openRateDetail(row: FullRow) {
  const s = outcomeMap.value[row.signal_id]
  if (!s || s.evaluated === 0) return  // 无已评估样本, 不开
  drawerCtx.value = { signal_id: row.signal_id, signal_name: row.signal_name, date: '', count: 0 }
  drawerTitle.value = `${row.signal_name} · 近90天每笔结果 (成功 ${s.success}/${s.evaluated})`
  drawerOpen.value = true
  drawerLoading.value = true
  drawerRows.value = []
  try {
    const start = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10)
    const rows = await fetchSignalHistory(1000, { startDate: start, signalId: row.signal_id })
    // 已评估的(成功/失败/中性)排前, 待评估排后; 同组按时间倒序
    rows.sort((a, b) => {
      const ra = OUTCOME_RANK[a.outcome ?? ''] ?? 9
      const rb = OUTCOME_RANK[b.outcome ?? ''] ?? 9
      if (ra !== rb) return ra - rb
      return (b.triggered_at || '').localeCompare(a.triggered_at || '')
    })
    drawerRows.value = rows
  } catch {
    message.error('加载明细失败')
  } finally {
    drawerLoading.value = false
  }
}

function formatTime(raw: string): string {
  if (!raw) return '-'
  const s = raw.replace('T', ' ')
  return s.length >= 19 ? s.slice(11, 19) : s
}
function formatDay(raw: string): string {
  if (!raw) return '-'
  return raw.replace('T', ' ').slice(5, 10)  // MM-DD
}

const drawerColumns: DataTableColumns<Signal> = [
  { title: '日期', key: 'triggered_at', width: 64,
    render: (row) => h('span', { style: { fontFamily: 'monospace', fontSize: '12px' } }, formatDay(row.triggered_at)) },
  { title: '代码', key: 'code', width: 72,
    // v1.7.592: 点代码跳同花顺网页版个股页(分时+K线), 与涨停复盘页同口径
    render: (row) => h('span', {
      style: { fontFamily: 'monospace', color: 'var(--primary)', cursor: 'pointer', textDecoration: 'underline dotted' },
      title: `${row.name} ${row.code} · 打开同花顺分时/K线`,
      role: 'link', tabindex: 0,
      onClick: () => window.open(`https://stockpage.10jqka.com.cn/${row.code}/`, '_blank', 'noopener'),
      onKeydown: (e: KeyboardEvent) => { if (e.key === 'Enter') window.open(`https://stockpage.10jqka.com.cn/${row.code}/`, '_blank', 'noopener') },
    }, row.code) },
  { title: '名称', key: 'name', width: 84 },
  { title: '触发价', key: 'price', width: 64,
    render: (row) => h('span', { style: { fontFamily: 'monospace' } }, Number(row.price || 0).toFixed(2)) },
  { title: '5日收盘', key: 'p5', width: 72,
    render: (row) => {
      const v = row.outcome_p5_pct
      if (v == null) return h('span', { style: { color: 'var(--text3)', fontSize: '11px' } }, '—')
      const color = v >= 0 ? 'var(--red)' : 'var(--green)'
      return h('span', { style: { color, fontWeight: 600, fontFamily: 'monospace' } }, `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`)
    } },
  { title: '结果', key: 'outcome', width: 64,
    render: (row) => {
      const o = row.outcome
      if (!o) return h('span', { style: { fontSize: '11px', color: 'var(--warn-fg)' } }, '待评估')
      const cfg: Record<string, [string, string]> = {
        success: ['成功', 'var(--up-fg)'], fail: ['失败', 'var(--down-fg)'], neutral: ['中性', 'var(--fg-muted)'],
      }
      const [label, color] = cfg[o] || ['—', 'var(--fg-muted)']
      return h('span', { style: { color, fontWeight: 700, fontSize: '12px' } }, label)
    } },
  { title: '指标摘要', key: 'detail', minWidth: 200,
    render: (row) => h('span', { style: { fontSize: '12px', color: 'var(--text2)', lineHeight: '1.5' } }, row.detail || '—') },
]

// ────────────────────────────────────────────────
// 筛选器选项
// ────────────────────────────────────────────────
const groupOptions = GROUP_ORDER.map(g => ({ label: GROUP_LABEL[g], value: g }))
const directionOptions = [
  { label: '买', value: 'buy' },
  { label: '卖', value: 'sell' },
  { label: '减仓', value: 'reduce' },
  { label: '—（中性）', value: '-' },
]

// 5日成功率展示 (样本=近90天触发; 判定=触发后第5个交易日收盘)
function rateBadge(sid: string) {
  const s = outcomeMap.value[sid]
  if (!s || s.evaluated === 0) return { text: '—', color: 'var(--text3)' }
  const rate = s.success_rate
  // A股习惯: 成功率高=红(成功), 低=绿(失败), 中间(45~55%)橙
  const color = rate >= 55 ? 'var(--up-fg)' : rate >= 45 ? 'var(--warn-fg)' : 'var(--down-fg)'
  return { text: `${rate}%`, color, sub: `${s.success}/${s.evaluated}` }
}
</script>

<template>
  <div class="alert-overview">
    <!-- 顶部筛选 -->
    <div class="page-header">
      <div class="title-row">
        <h2>预警总览</h2>
        <div class="filters">
          <NRadioGroup :value="days" size="small" @update:value="(v: any) => changeDays(Number(v))">
            <NRadioButton :value="7">最近 7 天</NRadioButton>
            <NRadioButton :value="14">14 天</NRadioButton>
            <NRadioButton :value="30">30 天</NRadioButton>
          </NRadioGroup>
          <NSelect v-model:value="filterGroup" :options="groupOptions" placeholder="全部分组"
                   clearable size="small" class="filter-select" />
          <NSelect v-model:value="filterDirection" :options="directionOptions" placeholder="全部方向"
                   clearable size="small" class="filter-select" />
        </div>
      </div>

      <!-- 汇总条 -->
      <div class="summary-bar" v-if="matrixDates.length">
        <span class="sum-item">
          今日 <b>{{ matrixDates.length ? formatDateShort(matrixDates[matrixDates.length-1]) : '—' }}</b> 命中
          <b class="num">{{ summary.todayCount }}</b> 笔
        </span>
        <span class="sum-divider">·</span>
        <span class="sum-item">强档 <b>{{ summary.todayStrong }}</b></span>
        <span class="sum-divider">·</span>
        <span class="sum-item">中档 <b>{{ summary.todayMiddle }}</b></span>
        <span class="sum-divider">·</span>
        <span class="sum-item" v-if="summary.deltaPct !== null">
          较昨日
          <b :class="summary.deltaPct >= 0 ? 'delta-up' : 'delta-down'">
            {{ summary.deltaPct >= 0 ? '+' : '' }}{{ summary.deltaPct }}%
          </b>
        </span>
        <span class="sum-divider" v-if="summary.hottest">·</span>
        <span class="sum-item" v-if="summary.hottest">
          最活跃 <b>{{ summary.hottest.signal_name }}</b>
        </span>
      </div>
    </div>

    <!-- 模型战绩参考: 默认折叠, 让预警矩阵成首屏焦点 (v1.7.678 渐进披露) -->
    <NCollapse class="model-collapse" style="margin: 12px 0;">
      <NCollapseItem title="模型战绩参考 · 全市场回测 + 按周真实胜率" name="model">
        <!-- 各买入模型 全市场半年回测(含资金成本) — 厚样本判断当前行情适合哪个模型 -->
        <ModelBacktestPanel />
        <!-- 各买点模型 按周真实成功率(自选真实信号, 样本小) -->
        <ModelWeeklyPanel style="margin-top: 12px;" />
      </NCollapseItem>
    </NCollapse>

    <!-- 矩阵 -->
    <NSpin :show="loading">
      <div class="matrix-wrap" v-if="matrixDates.length">
        <!-- 移动端: 按组卡片列表(信号名/方向/次数/5日成功率), 去掉日期矩阵 -->
        <div v-if="isMobile" class="m-groups">
          <div v-for="block in groupedRows" :key="block.group" class="m-group">
            <div class="m-group-title">
              {{ GROUP_LABEL[block.group] }} <span class="m-gc">{{ block.rows.length }}</span>
            </div>
            <div
              v-for="row in block.rows"
              :key="row.signal_id"
              class="m-sig"
              :class="{ silent: row.total === 0 }"
            >
              <div class="m-sig-l">
                <span class="m-sig-name">{{ row.signal_name }}</span>
                <NTag size="tiny" :type="DIR_TYPE[row.direction] || 'default'" :bordered="false">
                  {{ DIR_LABEL[row.direction] || row.direction || '—' }}
                </NTag>
              </div>
              <div class="m-sig-r">
                <span class="m-total">{{ row.total }}次</span>
                <span class="m-rate" :style="{ color: rateBadge(row.signal_id).color }">
                  {{ rateBadge(row.signal_id).text }}
                  <span v-if="rateBadge(row.signal_id).sub" class="m-rate-sub">{{ rateBadge(row.signal_id).sub }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="matrix-scroll">
          <table class="matrix">
            <thead>
              <tr>
                <th class="col-signal">信号</th>
                <th class="col-dir">方向</th>
                <th class="col-pri">档</th>
                <th v-for="d in matrixDates" :key="d" class="col-date" :class="{ today: isToday(d) }">
                  {{ formatDateShort(d) }}
                </th>
                <th class="col-total">合计</th>
                <th class="col-rate">
                  <NTooltip>
                    <template #trigger>
                      <span style="cursor: help; border-bottom: 1px dashed var(--text3)">5日成功率</span>
                    </template>
                    <div style="max-width: 280px; line-height: 1.6">
                      每条信号按<b>触发后第5个交易日的收盘价</b>(相对触发价)判定:
                      买点 ≥+5% 成功 / ≤−3% 失败 / 中间中性;
                      卖点·减仓翻转(之后跌=避损成功)。
                      成功率 = 成功 ÷ 已评估(满5个交易日才评估, 未满=待评估不计)。
                      样本取<b>最近90天</b>触发的该信号。
                    </div>
                  </NTooltip>
                </th>
              </tr>
            </thead>
            <tbody v-for="block in groupedRows" :key="block.group">
              <tr class="group-row">
                <td :colspan="5 + matrixDates.length">
                  <span class="group-name">{{ GROUP_LABEL[block.group] }}</span>
                  <span class="group-count">（{{ block.rows.length }}）</span>
                </td>
              </tr>
              <tr v-for="row in block.rows" :key="row.signal_id" class="data-row" :class="{ silent: row.total === 0 }">
                <td class="col-signal">
                  <div class="sig-name">{{ row.signal_name }}</div>
                  <div class="sig-id">{{ row.signal_id }}</div>
                </td>
                <td class="col-dir">
                  <NTag size="tiny" :type="DIR_TYPE[row.direction] || 'default'" :bordered="false">
                    {{ DIR_LABEL[row.direction] || row.direction || '—' }}
                  </NTag>
                </td>
                <td class="col-pri">
                  <span class="pri-tag" :class="`pri-${row.priority}`">{{ PRIORITY_LABEL[row.priority] }}</span>
                </td>
                <td v-for="(n, idx) in row.counts" :key="matrixDates[idx]" class="cell-heat"
                    :style="{ background: cellBg(n), color: cellColor(n), cursor: n > 0 ? 'pointer' : 'default' }"
                    :class="{ today: isToday(matrixDates[idx]) }"
                    :role="n > 0 ? 'button' : undefined" :tabindex="n > 0 ? 0 : undefined"
                    :aria-label="n > 0 ? `${matrixDates[idx]} ${n}笔, 查看明细` : undefined"
                    @click="openDetail(row, idx)" @keydown.enter="n > 0 && openDetail(row, idx)">
                  <NTooltip v-if="n > 0">
                    <template #trigger>
                      <span>{{ n }}</span>
                    </template>
                    {{ matrixDates[idx] }} · {{ n }} 笔 (点击查明细)
                  </NTooltip>
                  <span v-else>·</span>
                </td>
                <td class="col-total">
                  <span class="total-num" :class="{ zero: row.total === 0 }">{{ row.total }}</span>
                </td>
                <td class="col-rate">
                  <NTooltip v-if="rateBadge(row.signal_id).sub">
                    <template #trigger>
                      <div class="rate-cell rate-clickable" role="button" tabindex="0"
                           aria-label="查看胜率明细" @click="openRateDetail(row)" @keydown.enter="openRateDetail(row)">
                        <span :style="{ color: rateBadge(row.signal_id).color, fontWeight: 600 }">
                          {{ rateBadge(row.signal_id).text }}
                        </span>
                        <span class="rate-sub">{{ rateBadge(row.signal_id).sub }}</span>
                      </div>
                    </template>
                    点击查看近90天每一笔的成败明细
                  </NTooltip>
                  <div v-else class="rate-cell">
                    <span :style="{ color: rateBadge(row.signal_id).color, fontWeight: 600 }">
                      {{ rateBadge(row.signal_id).text }}
                    </span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 色阶图例 -->
        <div class="legend">
          <span>命中数</span>
          <span class="chip" :style="{ background: cellBg(0), color: cellColor(0) }">0</span>
          <span class="chip" :style="{ background: cellBg(1), color: cellColor(1) }">1-2</span>
          <span class="chip" :style="{ background: cellBg(3), color: cellColor(3) }">3-5</span>
          <span class="chip" :style="{ background: cellBg(6), color: cellColor(6) }">6-10</span>
          <span class="chip" :style="{ background: cellBg(11), color: cellColor(11) }">11+</span>
        </div>
      </div>
      <NEmpty v-else-if="!loading" description="该期间暂无任何信号命中" style="margin: 60px 0" />
    </NSpin>

    <!-- 明细 Drawer -->
    <NDrawer v-model:show="drawerOpen" :width="780" placement="right">
      <NDrawerContent :title="drawerTitle" closable>
        <NSpin :show="drawerLoading">
          <NDataTable v-if="drawerRows.length"
                      :columns="drawerColumns" :data="drawerRows"
                      :bordered="false" size="small"
                      :row-key="(r: Signal) => r.id" />
          <NEmpty v-else-if="!drawerLoading" description="无明细" style="margin: 40px 0" />
        </NSpin>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.alert-overview { padding: 4px 0 24px; }

.page-header { margin-bottom: 14px; }
.title-row {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; flex-wrap: wrap; margin-bottom: 8px;
}
.title-row h2 { font-size: 18px; font-weight: 700; color: var(--text1); margin: 0; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
/* 自适应宽度: 宽屏定宽 140px, 窄屏(<768)放开占满整行不再并排超界 */
.filter-select { width: 140px; }
@media (max-width: 768px) {
  .filters { width: 100%; }
  .filter-select { flex: 1 1 140px; width: auto; min-width: 120px; }
}

.summary-bar {
  display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
  padding: 10px 14px; background: rgba(46, 158, 255, 0.05);
  border-left: 3px solid var(--primary); border-radius: 4px;
  font-size: 13px; color: var(--text2);
  font-variant-numeric: tabular-nums;
}
.sum-item b { color: var(--text1); font-weight: 700; }
.sum-item .num { color: var(--primary); font-size: 15px; }
.sum-divider { color: var(--text3); margin: 0 2px; }
.delta-up { color: var(--up-fg); }
.delta-down { color: var(--down-fg); }

.matrix-wrap { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 4px; }
.matrix-scroll { overflow-x: auto; }
.matrix {
  border-collapse: separate; border-spacing: 0;
  font-size: 12.5px; width: 100%; min-width: 760px;
  font-variant-numeric: tabular-nums;
}
.matrix th, .matrix td {
  padding: 6px 8px; text-align: center; border-bottom: 1px solid var(--border-muted);
}
.matrix thead th {
  position: sticky; top: 0; z-index: 2; background: var(--bg-sunken);
  font-weight: 600; color: var(--text2); border-bottom: 2px solid var(--border-default);
  white-space: nowrap;
}
.col-signal { width: 220px; text-align: left !important; padding-left: 12px !important; }
.col-dir { width: 50px; }
.col-pri { width: 36px; }
.col-date { width: 48px; font-family: monospace; }
.col-date.today { color: var(--primary); background: var(--accent-bg-muted) !important; }
.col-total { width: 56px; font-weight: 600; }
.col-rate { width: 84px; }

.group-row td {
  padding: 8px 12px !important; background: var(--bg-sunken);
  text-align: left !important;
  border-top: 2px solid var(--border-default); border-bottom: 1px solid var(--border-default);
}
.group-name { font-size: 12px; font-weight: 700; color: var(--text1); }
.group-count { font-size: 11px; color: var(--text3); margin-left: 2px; }

.data-row .col-signal {
  text-align: left !important; padding-left: 12px !important;
}
.sig-name { font-size: 13px; font-weight: 600; color: var(--text1); line-height: 1.3; }
.sig-id { font-family: monospace; font-size: 10.5px; color: var(--text3); margin-top: 1px; }
.data-row.silent .sig-name { color: var(--text3); font-weight: 500; }
.data-row.silent .sig-id { color: var(--text3); opacity: 0.6; }

.pri-tag {
  display: inline-block; padding: 1px 6px; border-radius: 8px;
  font-size: 10px; font-weight: 700; line-height: 1.4;
}
.pri-strong { background: var(--danger-bg-muted); color: var(--danger-fg); }
.pri-middle { background: var(--warn-bg-muted); color: var(--warn-fg); }
.pri-weak { background: var(--bg-sunken); color: var(--fg-muted); }

.cell-heat {
  font-family: monospace; font-weight: 600; font-size: 12px;
  transition: transform 0.1s, box-shadow 0.1s;
  user-select: none;
  touch-action: manipulation;
}
.cell-heat:hover {
  transform: scale(1.08); box-shadow: 0 0 0 2px var(--primary);
  z-index: 1; position: relative;
}
.cell-heat.today { box-shadow: inset 0 0 0 2px var(--primary); }

.total-num { font-family: monospace; color: var(--text1); }
.total-num.zero { color: var(--text3); font-weight: 400; }

.rate-cell { display: flex; flex-direction: column; align-items: center; line-height: 1.2; }
.rate-sub { font-size: 10px; color: var(--text3); font-family: monospace; }
.rate-clickable { cursor: pointer; border-radius: 4px; padding: 2px 4px; transition: background 0.15s; touch-action: manipulation; }
.rate-clickable:hover { background: rgba(46, 158, 255, 0.1); }

.legend {
  display: flex; gap: 6px; align-items: center;
  padding: 10px 14px; font-size: 11.5px; color: var(--text2);
  border-top: 1px solid var(--border-muted);
}
.chip {
  display: inline-block; min-width: 32px; padding: 2px 8px;
  font-family: monospace; font-size: 11px; font-weight: 600;
  border-radius: 3px; text-align: center;
}

/* ── 移动端: 信号成功率卡片 ── */
.m-groups { display: flex; flex-direction: column; gap: 14px; }
.m-group-title {
  font-size: 12px; color: var(--text2); font-weight: 600;
  margin-bottom: 6px; padding-left: 2px;
}
.m-group-title .m-gc { color: var(--text3); font-weight: 400; }
.m-sig {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 9px 10px; border: 1px solid var(--border-default); border-radius: 8px;
  margin-bottom: 6px; background: var(--bg-surface);
}
.m-sig.silent { opacity: 0.5; }
.m-sig-l { display: flex; align-items: center; gap: 6px; min-width: 0; }
.m-sig-name { font-size: 13px; color: var(--text1); font-weight: 500; }
.m-sig-r { display: flex; align-items: baseline; gap: 10px; flex-shrink: 0; }
.m-total { font-size: 12px; color: var(--text2); font-family: monospace; font-variant-numeric: tabular-nums; }
.m-rate { font-size: 14px; font-weight: 700; font-family: monospace; font-variant-numeric: tabular-nums; }
.m-rate-sub { font-size: 10px; color: var(--text3); font-weight: 400; margin-left: 2px; }
</style>
