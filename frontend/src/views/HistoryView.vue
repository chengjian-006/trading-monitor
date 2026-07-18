<script setup lang="ts">
import { ref, onMounted, h, computed } from 'vue'
import { NTag, NSkeleton, NInput, NSelect, NButton, NIcon, NDatePicker, NModal, NSpin } from 'naive-ui'
import { RefreshOutline, SearchOutline, ChevronForwardOutline } from '@vicons/ionicons5'
import { fetchSignalHistory, fetchSignalStats, fetchSignalOutcomeStats, type SignalStatsItem, type SignalOutcomeStatsItem } from '../api/signals'
import { upsertSignalExecution, deleteSignalExecution, fetchSignalExecutions, type SignalExecution } from '../api/signal-executions'
import FilterPanel from '../components/common/FilterPanel.vue'
import { fetchIntraday, type IntradayPoint } from '../api/kline'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { useResponsive } from '../composables/useResponsive'
import IntradayChart from '../components/chart/IntradayChart.vue'
import ResponsiveTable from '../components/common/ResponsiveTable.vue'
import { MODELS } from '../data/models'
import type { Signal } from '../types'

const message = useGlobalMessage()
const { isMobile } = useResponsive()
const allSignals = ref<Signal[]>([])
const loading = ref(false)

const showIntradayModal = ref(false)
const intradayCode = ref('')
const intradayName = ref('')
const intradayData = ref<IntradayPoint[]>([])
const intradayPreClose = ref(0)
const intradayLoading = ref(false)

async function openIntraday(code: string, name: string) {
  intradayCode.value = code
  intradayName.value = name
  intradayData.value = []
  intradayPreClose.value = 0
  showIntradayModal.value = true
  intradayLoading.value = true
  try {
    const res = await fetchIntraday(code)
    intradayData.value = res.points || []
    intradayPreClose.value = res.pre_close || 0
  } catch {
    message.error('获取分时数据失败')
  } finally {
    intradayLoading.value = false
  }
}

const filterKeyword = ref('')
const filterDirection = ref<string | null>(null)
const filterModel = ref<string | null>(null)

const modelOptions = computed(() => {
  const seen = new Set<string>()
  const opts: { label: string; value: string }[] = []
  for (const s of allSignals.value) {
    const id = s.signal_id || s.signal_name
    if (id && !seen.has(id)) { seen.add(id); opts.push({ label: s.signal_name || id, value: id }) }
  }
  opts.sort((a, b) => a.label.localeCompare(b.label))
  return opts
})

// 最近 N 个交易日区间 [startTs, endTs] — 跳周末(节假日近似不剔除), 含最近交易日
function recentTradingRange(n: number): [number, number] {
  const days: Date[] = []
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  while (days.length < n) {
    const wd = d.getDay()
    if (wd !== 0 && wd !== 6) days.push(new Date(d))
    d.setDate(d.getDate() - 1)
  }
  return [days[n - 1].getTime(), days[0].getTime()]  // [第N个交易日, 最近交易日]
}
const filterRange = ref<[number, number] | null>(recentTradingRange(5))

const dirLabel: Record<string, string> = { buy: '买入', sell: '卖出', reduce: '减仓' }
const dirType: Record<string, 'success' | 'error' | 'warning'> = {
  buy: 'success', sell: 'error', reduce: 'warning',
}

const signals = computed(() => {
  let list = allSignals.value
  const kw = filterKeyword.value.trim()
  if (kw) {
    list = list.filter((s) => s.code.includes(kw) || s.name.includes(kw))
  }
  if (filterDirection.value) {
    list = list.filter((s) => s.direction === filterDirection.value)
  }
  if (filterModel.value) {
    list = list.filter((s) => (s.signal_id || s.signal_name) === filterModel.value)
  }
  return list
})

// triggered_at 兼容 "2026-07-11T09:30:00" 与 "2026-07-11 09:30:00" 两种写法
function parseTriggeredTs(raw: string): number | null {
  if (!raw) return null
  const t = new Date(raw.replace(' ', 'T')).getTime()
  return isNaN(t) ? null : t
}

// 相对时间(刚刚/分钟前/小时前/昨天/N天前), 与 FreshnessBadge 同口径
function relativeTime(raw: string): string {
  const t = parseTriggeredTs(raw)
  if (t == null) return ''
  const diff = Math.floor((Date.now() - t) / 1000)
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  const days = Math.floor(diff / 86400)
  if (days === 1) return '昨天'
  if (days < 30) return `${days}天前`
  return formatDate(t)
}

function firstReason(detail: string): string {
  return (detail || '').split('|')[0].trim()
}

// filterRange 内的交易日数(跳周末), 用于"近 N 日"标签
const rangeDayLabel = computed(() => {
  const range = filterRange.value
  if (!range) return '近期'
  let n = 0
  const d = new Date(range[0]); d.setHours(0, 0, 0, 0)
  const end = new Date(range[1]); end.setHours(0, 0, 0, 0)
  while (d <= end) {
    const wd = d.getDay()
    if (wd !== 0 && wd !== 6) n++
    d.setDate(d.getDate() + 1)
  }
  return n > 0 ? `近 ${n} 日` : '近期'
})

// ── 按模型聚合概览 ── 仅按 filterRange 日期筛(不管 filterModel/关键词/方向), 按 signal_id 分组
interface ModelGroup {
  key: string          // signal_id || signal_name — 与 filterModel 判定口径一致
  signal_id: string
  signal_name: string
  direction: string
  count: number
  recent: Signal[]     // 最近 3 条触发
  outcome: SignalOutcomeStatsItem | null
}

const modelGroups = computed<ModelGroup[]>(() => {
  let list = allSignals.value
  const range = filterRange.value
  if (range) {
    const startTs = new Date(range[0]).setHours(0, 0, 0, 0)
    const endTs = new Date(range[1]).setHours(23, 59, 59, 999)
    list = list.filter((s) => {
      const t = parseTriggeredTs(s.triggered_at)
      return t == null || (t >= startTs && t <= endTs)
    })
  }
  const map = new Map<string, ModelGroup>()
  for (const s of list) {
    const key = s.signal_id || s.signal_name
    if (!key) continue
    let g = map.get(key)
    if (!g) {
      g = {
        key, signal_id: s.signal_id, signal_name: s.signal_name || key,
        direction: s.direction, count: 0, recent: [],
        outcome: outcomeStats.value[s.signal_id] ?? null,
      }
      map.set(key, g)
    }
    g.count++
    g.recent.push(s)
  }
  const arr = Array.from(map.values())
  for (const g of arr) {
    g.recent.sort((a, b) => (parseTriggeredTs(b.triggered_at) ?? 0) - (parseTriggeredTs(a.triggered_at) ?? 0))
    g.recent = g.recent.slice(0, 3)
  }
  arr.sort((a, b) => b.count - a.count)
  return arr
})

const activeModelName = computed(() => {
  if (!filterModel.value) return ''
  const g = modelGroups.value.find((x) => x.key === filterModel.value)
  return g ? g.signal_name : filterModel.value
})

// 点模型卡: 设/取消主列表的模型筛选(复用现有 filterModel 逻辑)
function toggleModelFilter(key: string) {
  filterModel.value = filterModel.value === key ? null : key
}

// 概览区默认展开; 手机默认折叠(省空间)
const overviewExpanded = ref(!isMobile.value)

function formatDate(ts: number): string {
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function formatTime(raw: string): string {
  if (!raw) return '-'
  const s = raw.replace('T', ' ')
  return s.length > 16 ? s.slice(5, 19) : s.slice(5)
}

function getTagType(text: string): 'success' | 'error' | 'warning' | 'info' | 'default' {
  const pctMatch = text.match(/[+-]?\d+\.?\d*%/)
  if (pctMatch) {
    const val = parseFloat(pctMatch[0])
    if (val > 0) return 'error'
    if (val < 0) return 'success'
  }
  if (/跌|回撤|回调/.test(text)) return 'success'
  if (/MA\d|均线/.test(text)) return 'info'
  if (/量|放量|缩量/.test(text)) return 'warning'
  return 'default'
}

function renderDetail(row: Signal) {
  if (!row.detail) return h('span', { style: { color: 'var(--text3)', fontSize: '11px' } }, '-')
  const parts = row.detail.split('|').map(s => s.trim()).filter(Boolean)
  return h('div', { style: { display: 'flex', gap: '3px', flexWrap: 'wrap' } },
    parts.map(text => h(NTag, { size: 'tiny', type: getTagType(text), bordered: false }, () => text))
  )
}

const stats = ref<Record<string, SignalStatsItem>>({})
const outcomeStats = ref<Record<string, SignalOutcomeStatsItem>>({})
const statsExpanded = ref(false)

// 信号执行记录 (信号 → 我的操作闭环): signal_pk -> SignalExecution
const executionMap = ref<Record<number, SignalExecution>>({})

async function loadExecutionsFor(signalIds: number[]) {
  if (!signalIds.length) {
    executionMap.value = {}
    return
  }
  try {
    const list = await fetchSignalExecutions(signalIds)
    const map: Record<number, SignalExecution> = {}
    for (const ex of list) map[ex.signal_pk] = ex
    executionMap.value = map
  } catch {
    // silent — 执行记录加载失败不影响主表
  }
}

async function loadData() {
  loading.value = true
  try {
    const startDate = filterRange.value?.[0] ? formatDate(filterRange.value[0]) : undefined
    const endDate = filterRange.value?.[1] ? formatDate(filterRange.value[1]) : undefined
    const [signalsList, statsData, outcomeData] = await Promise.all([
      fetchSignalHistory(1000, { startDate, endDate }),
      fetchSignalStats(30),
      fetchSignalOutcomeStats(90),
    ])
    allSignals.value = signalsList
    stats.value = statsData
    outcomeStats.value = outcomeData
    // 并发拉这批信号的执行记录, 主表已渲染再补标记
    await loadExecutionsFor(signalsList.map(s => s.id).filter(Boolean))
  } catch {
    message.error('加载信号历史失败')
  } finally {
    loading.value = false
  }
}

// ── 信号执行标记 ──
const showExecModal = ref(false)
const execEditing = ref<{ signal_pk: number; code: string; signal_name: string; price: number; name: string } | null>(null)
const execPrice = ref<string>('')
const execQty = ref<string>('')
const execNotes = ref('')
const execSaving = ref(false)

function openExecModal(row: Signal) {
  const existing = executionMap.value[row.id]
  execEditing.value = {
    signal_pk: row.id, code: row.code, signal_name: row.signal_name,
    price: Number(row.price), name: row.name,
  }
  execPrice.value = existing?.actual_price != null ? String(existing.actual_price) : ''
  execQty.value = existing?.actual_qty != null ? String(existing.actual_qty) : ''
  execNotes.value = existing?.notes || ''
  showExecModal.value = true
}

async function saveExecution() {
  if (!execEditing.value) return
  execSaving.value = true
  try {
    const ed = execEditing.value
    const actualPrice = execPrice.value.trim() ? Number(execPrice.value) : null
    const actualQty = execQty.value.trim() ? parseInt(execQty.value, 10) : null
    if (actualPrice !== null && !(actualPrice > 0)) {
      message.error('实际价必须为正数, 留空表示按信号触发价')
      execSaving.value = false
      return
    }
    const res = await upsertSignalExecution({
      signal_pk: ed.signal_pk, code: ed.code, action: 'executed',
      actual_price: actualPrice, actual_qty: actualQty,
      notes: execNotes.value.trim() || null,
    })
    executionMap.value = {
      ...executionMap.value,
      [ed.signal_pk]: {
        id: res.id, user_id: 0, signal_pk: ed.signal_pk, code: ed.code,
        action: 'executed', actual_price: actualPrice, actual_qty: actualQty,
        notes: execNotes.value.trim() || null,
        created_at: '', updated_at: '',
      },
    }
    showExecModal.value = false
    message.success(`${ed.name} 已标记执行`)
  } catch (e: any) {
    message.error('保存失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    execSaving.value = false
  }
}

async function markSkipped(row: Signal) {
  try {
    const res = await upsertSignalExecution({
      signal_pk: row.id, code: row.code, action: 'skipped',
    })
    executionMap.value = {
      ...executionMap.value,
      [row.id]: {
        id: res.id, user_id: 0, signal_pk: row.id, code: row.code,
        action: 'skipped', actual_price: null, actual_qty: null, notes: null,
        created_at: '', updated_at: '',
      },
    }
    message.success(`${row.name} 已标记跳过`)
  } catch {
    message.error('标记失败')
  }
}

async function clearExecution(signalPk: number) {
  try {
    await deleteSignalExecution(signalPk)
    const next = { ...executionMap.value }
    delete next[signalPk]
    executionMap.value = next
  } catch {
    message.error('清除失败')
  }
}

const sortedStats = computed(() =>
  Object.values(stats.value).sort((a, b) => b.count - a.count)
)

// 合并展示: 同 signal_id 的"摸高最佳" + "实际收盘胜率" 一卡两面
const mergedStats = computed(() => {
  return sortedStats.value.map(s => ({
    ...s,
    outcome: outcomeStats.value[s.signal_id] ?? null,
  }))
})

function renderPerfCell(row: Signal) {
  const perf = (row as any).perf
  if (!perf) return h('span', { style: { color: 'var(--text3)', fontSize: '11px' } }, '-')
  // 最佳行: 沿用旧色阶 (≥5 红 / 0~5 绿 / <0 灰), 表达"信号能不能给到值得的涨幅"
  const fmtMax = (v: number | null) => {
    if (v == null) return null
    const color = v >= 5 ? 'var(--up-fg)' : v >= 0 ? 'var(--down-fg)' : 'var(--flat-fg)'
    const text = `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
    return h('span', { style: { color, fontWeight: 600, fontSize: '11px' } }, text)
  }
  // 收盘/至今: 严格按"实际盈亏" A股配色 (红涨绿跌)
  const fmtReal = (v: number | null) => {
    if (v == null) return null
    const color = v > 0 ? 'var(--up-fg)' : v < 0 ? 'var(--down-fg)' : 'var(--flat-fg)'
    const text = `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
    return h('span', { style: { color, fontWeight: 600, fontSize: '11px' } }, text)
  }
  const labelStyle = { fontSize: '10px', color: 'var(--text3)', marginRight: '4px' }
  const rowStyle = { display: 'flex', alignItems: 'baseline', gap: '2px' }
  const rows: any[] = []
  if (perf.p5_max != null)
    rows.push(h('div', { style: rowStyle }, [h('span', { style: labelStyle }, '5 日内最佳'), fmtMax(perf.p5_max)]))
  if (perf.p10_max != null)
    rows.push(h('div', { style: rowStyle }, [h('span', { style: labelStyle }, '10 日内最佳'), fmtMax(perf.p10_max)]))
  if (perf.p20_max != null)
    rows.push(h('div', { style: rowStyle }, [h('span', { style: labelStyle }, '20 日内最佳'), fmtMax(perf.p20_max)]))
  if (perf.p5_close != null)
    rows.push(h('div', { style: rowStyle }, [h('span', { style: labelStyle }, '5 日收盘'), fmtReal(perf.p5_close)]))
  if (perf.p10_close != null)
    rows.push(h('div', { style: rowStyle }, [h('span', { style: labelStyle }, '10 日收盘'), fmtReal(perf.p10_close)]))
  if (perf.p20_close != null)
    rows.push(h('div', { style: rowStyle }, [h('span', { style: labelStyle }, '20 日收盘'), fmtReal(perf.p20_close)]))
  if (perf.current_pct != null) {
    const days = perf.elapsed_days ?? 0
    rows.push(h('div', { style: rowStyle }, [h('span', { style: labelStyle }, `至今(${days}日)`), fmtReal(perf.current_pct)]))
  }
  return h('div', { style: { lineHeight: '1.3' } }, rows)
}

function handleReset() {
  filterKeyword.value = ''
  filterDirection.value = null
  filterModel.value = null
  filterRange.value = recentTradingRange(5)
}

function renderExecutionCell(row: Signal) {
  const ex = executionMap.value[row.id]
  if (!ex) {
    return h('div', { style: { display: 'flex', gap: '4px', alignItems: 'center' } }, [
      h(NButton, {
        size: 'tiny', type: 'primary',
        onClick: (e: any) => { e.stopPropagation?.(); openExecModal(row) },
      }, () => '✓ 已执行'),
      h(NButton, {
        size: 'tiny', quaternary: true,
        onClick: (e: any) => { e.stopPropagation?.(); markSkipped(row) },
      }, () => '跳过'),
    ])
  }
  if (ex.action === 'skipped') {
    return h('div', { style: { display: 'flex', alignItems: 'center', gap: '4px' } }, [
      h(NTag, { size: 'tiny', type: 'default', bordered: false }, () => '✗ 已跳过'),
      h('span', {
        role: 'button', tabindex: 0, 'aria-label': '清除执行记录',
        style: { fontSize: '10px', color: 'var(--text2)', cursor: 'pointer', textDecoration: 'underline' },
        onClick: () => clearExecution(row.id),
        onKeydown: (e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); clearExecution(row.id) } },
      }, '清除'),
    ])
  }
  // executed
  const entry = Number(row.price) || 0
  const ap = ex.actual_price
  let priceLine: string
  if (ap != null && entry > 0) {
    const diffPct = ((ap - entry) / entry) * 100
    const diffStr = diffPct === 0 ? '同信号价' : `${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(1)}%`
    priceLine = `${ap.toFixed(2)} (${diffStr})`
  } else if (ap != null) {
    priceLine = ap.toFixed(2)
  } else {
    priceLine = '按信号价'
  }
  const children: any[] = [
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '4px' } }, [
      h(NTag, { size: 'tiny', type: 'success', bordered: false }, () => '✓ 已执行'),
      h('span', {
        role: 'button', tabindex: 0, 'aria-label': '编辑执行记录',
        style: { fontSize: '10px', color: 'var(--primary)', cursor: 'pointer', textDecoration: 'underline' },
        onClick: () => openExecModal(row),
        onKeydown: (e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openExecModal(row) } },
      }, '编辑'),
      h('span', {
        role: 'button', tabindex: 0, 'aria-label': '清除执行记录',
        style: { fontSize: '10px', color: 'var(--text3)', cursor: 'pointer' },
        onClick: () => clearExecution(row.id),
        onKeydown: (e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); clearExecution(row.id) } },
      }, '清除'),
    ]),
    h('span', { style: { fontSize: '10px', color: 'var(--text2)', fontFamily: 'monospace' } }, priceLine),
  ]
  if (ex.notes) {
    children.push(h('span', {
      style: { fontSize: '10px', color: 'var(--text3)' }, title: ex.notes,
    }, ex.notes.length > 16 ? ex.notes.slice(0, 16) + '...' : ex.notes))
  }
  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: '1px', lineHeight: '1.2' } }, children)
}

onMounted(() => loadData())

const columns = computed(() => {
  return [
    { title: '触发时间', key: 'triggered_at', width: 130,
      render: (row: Signal) => h('span', { style: { fontSize: '12px', fontFamily: 'monospace' } }, formatTime(row.triggered_at)) },
    { title: '代码', key: 'code', width: 70,
      render: (row: Signal) => h('span', {
        style: { fontFamily: 'monospace', color: 'var(--primary)', cursor: 'pointer' },
        onClick: () => openIntraday(row.code, row.name),
      }, row.code) },
    { title: '名称', key: 'name', width: 80 },
    { title: '信号', key: 'signal_name', width: 144 },
    { title: '方向', key: 'direction', width: 60,
      render: (row: Signal) => h(NTag, {
        size: 'small', type: dirType[row.direction], bordered: false,
      }, () => dirLabel[row.direction] ?? row.direction) },
    { title: '价格', key: 'price', width: 70,
      render: (row: Signal) => h('span', { style: { fontFamily: 'monospace' } }, Number(row.price).toFixed(2)) },
    { title: '触发后表现', key: 'perf', width: 150,
      render: (row: Signal) => renderPerfCell(row) },
    { title: '详情', key: 'detail', minWidth: 200,
      render: (row: Signal) => renderDetail(row) },
    { title: '我的操作', key: 'execution', width: 200,
      render: (row: Signal) => renderExecutionCell(row) },
  ]
})

// 移动端卡片复用现有 cell 渲染(返回 VNode)的函数式组件
const PerfCell = (props: { row: Signal }) => renderPerfCell(props.row)
const DetailCell = (props: { row: Signal }) => renderDetail(props.row)
</script>


<template>
  <div>
    <FilterPanel>
    <div class="filter-bar">
      <div class="filter-fields">
        <div class="filter-item filter-item-range">
          <label>信号触发时间（默认最近5个交易日）</label>
          <NDatePicker v-model:value="filterRange" type="daterange" size="small" clearable to="body"
                       :default-value="recentTradingRange(5)" />
        </div>
        <div class="filter-item">
          <label>代码/名称</label>
          <NInput v-model:value="filterKeyword" placeholder="搜索..." size="small" clearable />
        </div>
        <div class="filter-item">
          <label>方向</label>
          <NSelect
            v-model:value="filterDirection"
            :options="[{ label: '买入', value: 'buy' }, { label: '卖出', value: 'sell' }, { label: '减仓', value: 'reduce' }]"
            size="small" clearable placeholder="全部" to="body"
          />
        </div>
        <div class="filter-item">
          <label>模型</label>
          <NSelect
            v-model:value="filterModel"
            :options="modelOptions"
            size="small" clearable placeholder="全部" to="body"
          />
        </div>
      </div>
      <div class="filter-actions">
        <NButton size="small" type="primary" @click="handleReset">
          <template #icon><NIcon><RefreshOutline /></NIcon></template>
          重置
        </NButton>
        <NButton size="small" type="primary" @click="loadData" :loading="loading">
          <template #icon><NIcon><SearchOutline /></NIcon></template>
          查询
        </NButton>
      </div>
    </div>
    </FilterPanel>

    <NSkeleton v-if="loading && allSignals.length === 0" :repeat="8" text />

    <Transition v-else name="content-fade" appear>
      <div>
        <div v-if="sortedStats.length" class="stats-bar">
          <div class="stats-title" :class="{ expanded: statsExpanded }" role="button" tabindex="0" :aria-expanded="statsExpanded" @click="statsExpanded = !statsExpanded" @keydown.enter="statsExpanded = !statsExpanded">
            <NIcon class="chevron" :size="12"><ChevronForwardOutline /></NIcon>
            <span>近 30 天信号表现</span>
            <span class="stats-title-hint">{{ sortedStats.length }} 个信号 · 点击{{ statsExpanded ? '折叠' : '展开' }}</span>
            <span v-if="statsExpanded" class="stats-title-note">（摸高：触发后 20 日内最高涨幅；实际：触发日按 price 买入、5 日收盘卖出的真实胜率）</span>
          </div>
          <div v-show="statsExpanded" class="stats-list">
            <div v-for="s in mergedStats" :key="s.signal_id" class="stats-card">
              <div class="stats-row stats-row-main">
                <span class="stats-name">{{ s.signal_name }}</span>
                <span class="stats-meta">触发 <b>{{ s.count }}</b> 笔</span>
                <span class="stats-meta">摸高 <b :class="s.avg_max_pct >= 0 ? 'up' : 'down'">{{ s.avg_max_pct >= 0 ? '+' : '' }}{{ s.avg_max_pct }}%</b></span>
              </div>
              <div class="stats-row stats-row-wins">
                <span class="row-tag">摸高</span>
                <span>赚 5% 机率 <b>{{ s.win_5pct }}%</b></span>
                <span class="stats-sep">·</span>
                <span>赚 10% 机率 <b>{{ s.win_10pct }}%</b></span>
                <span class="stats-sep">·</span>
                <span>赚 20% 机率 <b>{{ s.win_20pct }}%</b></span>
              </div>
              <div v-if="s.outcome && s.outcome.evaluated > 0" class="stats-row stats-row-outcome">
                <span class="row-tag row-tag-real">实际</span>
                <span>真实胜率 <b class="hi">{{ s.outcome.success_rate }}%</b>
                  <span class="ratio">({{ s.outcome.success }}/{{ s.outcome.evaluated }})</span>
                </span>
                <span class="stats-sep">·</span>
                <span v-if="s.outcome.avg_p1_pct != null">1日 <b :class="s.outcome.avg_p1_pct >= 0 ? 'up' : 'down'">{{ s.outcome.avg_p1_pct >= 0 ? '+' : '' }}{{ s.outcome.avg_p1_pct }}%</b></span>
                <span v-if="s.outcome.avg_p3_pct != null" class="stats-sep">·</span>
                <span v-if="s.outcome.avg_p3_pct != null">3日 <b :class="s.outcome.avg_p3_pct >= 0 ? 'up' : 'down'">{{ s.outcome.avg_p3_pct >= 0 ? '+' : '' }}{{ s.outcome.avg_p3_pct }}%</b></span>
                <span v-if="s.outcome.avg_p5_pct != null" class="stats-sep">·</span>
                <span v-if="s.outcome.avg_p5_pct != null">5日 <b :class="s.outcome.avg_p5_pct >= 0 ? 'up' : 'down'">{{ s.outcome.avg_p5_pct >= 0 ? '+' : '' }}{{ s.outcome.avg_p5_pct }}%</b></span>
                <span v-if="s.outcome.pending > 0" class="pending">尚 {{ s.outcome.pending }} 笔待评估</span>
              </div>
              <div v-else-if="s.outcome && s.outcome.pending > 0" class="stats-row stats-row-outcome">
                <span class="row-tag row-tag-real">实际</span>
                <span class="pending">{{ s.outcome.pending }} 笔等待 23:00 回填评估</span>
              </div>
            </div>
          </div>
        </div>
        <!-- 按模型聚合概览: 可折叠, 点卡片即筛选主列表 -->
        <div v-if="modelGroups.length" class="overview-bar">
          <div class="overview-title" :class="{ expanded: overviewExpanded }" role="button" tabindex="0" :aria-expanded="overviewExpanded" @click="overviewExpanded = !overviewExpanded" @keydown.enter="overviewExpanded = !overviewExpanded">
            <NIcon class="chevron" :size="12"><ChevronForwardOutline /></NIcon>
            <span>按模型聚合 · {{ rangeDayLabel }}</span>
            <span class="overview-title-hint">{{ modelGroups.length }} 个模型 · 点击{{ overviewExpanded ? '折叠' : '展开' }}</span>
            <span v-if="filterModel" class="overview-title-active">已筛选：{{ activeModelName }}</span>
          </div>
          <div v-show="overviewExpanded" class="overview-grid">
            <div
              v-for="g in modelGroups" :key="g.key"
              class="overview-card" :class="[`ov-${g.direction}`, { selected: filterModel === g.key }]"
              role="button" tabindex="0" :aria-pressed="filterModel === g.key"
              @click="toggleModelFilter(g.key)" @keydown.enter="toggleModelFilter(g.key)"
            >
              <div class="ov-head">
                <span class="ov-name">{{ g.signal_name }}</span>
                <NTag size="tiny" :type="dirType[g.direction] ?? 'info'" :bordered="false">
                  {{ dirLabel[g.direction] ?? g.direction }}
                </NTag>
              </div>
              <div class="ov-meta">
                <span class="ov-count">{{ rangeDayLabel }}触发 <b>{{ g.count }}</b> 笔</span>
                <span v-if="g.outcome && g.outcome.evaluated > 0" class="ov-wr">命中率 <b>{{ g.outcome.success_rate }}%</b></span>
              </div>
              <div class="ov-recent">
                <div v-for="r in g.recent" :key="r.id" class="ov-snap">
                  <span class="ov-snap-code">{{ r.code }}</span>
                  <span class="ov-snap-name">{{ r.name }}</span>
                  <span class="ov-snap-price">{{ Number(r.price).toFixed(2) }}</span>
                  <span class="ov-snap-reason" :title="firstReason(r.detail)">{{ firstReason(r.detail) }}</span>
                  <span class="ov-snap-time">{{ relativeTime(r.triggered_at) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="table-summary">共 {{ signals.length }} 条信号</div>
        <!-- 桌面/平板: 表格; 手机: 卡片(含触发后表现, 不砍信息) -->
        <ResponsiveTable
          :columns="columns"
          :data="signals"
          mobile-mode="card"
          :scroll-x="1060"
          :row-key="(row: Signal) => row.id"
          max-height="calc(100vh - 160px)"
        >
          <template #card="{ row }">
            <div class="hc-top">
              <span class="hc-sig">{{ row.signal_name }}</span>
              <NTag size="tiny" :type="dirType[row.direction]" :bordered="false">
                {{ dirLabel[row.direction] ?? row.direction }}
              </NTag>
            </div>
            <div class="hc-mid">
              <span class="hc-name">{{ row.name }}</span>
              <span class="hc-code" role="button" tabindex="0" :aria-label="`查看 ${row.name} 分时走势`" @click="openIntraday(row.code, row.name)" @keydown.enter="openIntraday(row.code, row.name)">{{ row.code }}</span>
              <span class="hc-time">{{ formatTime(row.triggered_at) }}</span>
              <span class="hc-price">{{ Number(row.price).toFixed(2) }}</span>
            </div>
            <div class="hc-perf"><PerfCell :row="row" /></div>
            <div class="hc-detail"><DetailCell :row="row" /></div>
          </template>
        </ResponsiveTable>
      </div>
    </Transition>

    <NModal v-model:show="showIntradayModal" preset="card" :title="`${intradayCode} ${intradayName} 分时走势`" style="max-width: 640px" :block-scroll="false">
      <div v-if="intradayLoading" style="display:flex;align-items:center;justify-content:center;padding:60px 0">
        <NSpin size="medium" />
      </div>
      <div v-else-if="intradayData.length">
        <IntradayChart :data="intradayData" :height="320" :pre-close="intradayPreClose" />
      </div>
      <div v-else style="text-align:center;padding:40px 0;color:var(--text2)">
        暂无分时数据（非交易时段或无数据）
      </div>
    </NModal>

    <NModal v-model:show="showExecModal" preset="card"
            :title="execEditing ? `${execEditing.name} · ${execEditing.signal_name} 标记执行` : '标记执行'"
            style="max-width: 460px" :block-scroll="false">
      <div v-if="execEditing" style="display:flex;flex-direction:column;gap:10px">
        <div style="color:var(--text2);font-size:12px;line-height:1.5">
          信号触发价 <b style="font-family:monospace">{{ execEditing.price.toFixed(2) }}</b>。
          填写你的实际成交价(选填); 留空表示按触发价计入"严格跟单"统计。
        </div>
        <div>
          <label style="display:block;font-size:12px;margin-bottom:4px;color:var(--text2)">实际成交价</label>
          <NInput v-model:value="execPrice" placeholder="例: 10.55 (留空=按信号触发价)" size="small" />
        </div>
        <div>
          <label style="display:block;font-size:12px;margin-bottom:4px;color:var(--text2)">实际数量(股)</label>
          <NInput v-model:value="execQty" placeholder="例: 1000 (选填)" size="small" />
        </div>
        <div>
          <label style="display:block;font-size:12px;margin-bottom:4px;color:var(--text2)">备注</label>
          <NInput v-model:value="execNotes" type="textarea" :rows="3"
                  placeholder="例: 等回踩 MA10 进场 / 跟随主力放量"
                  :maxlength="500" show-count />
        </div>
        <div style="margin-top:8px;display:flex;justify-content:flex-end;gap:8px">
          <NButton size="small" @click="showExecModal = false">取消</NButton>
          <NButton size="small" type="primary" :loading="execSaving" @click="saveExecution">保存</NButton>
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.filter-bar {
  background: var(--surface);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  padding: 16px 20px;
  margin-bottom: 16px;
  display: grid;
  grid-template-columns: 4fr 1fr;
  gap: 12px 24px;
  align-items: end;
  position: sticky;
  top: 0;
  z-index: 50;
  overflow: visible;
}
.filter-fields {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.filter-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 120px;
}
.filter-item label {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.6);
  white-space: nowrap;
}
.filter-item-range {
  flex: 2;
  min-width: 280px;
}
.filter-actions {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  justify-content: flex-end;
}
.table-summary {
  text-align: right;
  font-size: 12px;
  color: var(--text2);
  margin-bottom: 8px;
}
.stats-bar {
  background: var(--surface);
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
  padding: 8px 12px;
  margin-bottom: 10px;
}
.stats-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text1);
  cursor: pointer;
  user-select: none;
  padding: 2px 0;
  transition: color 0.15s;
  touch-action: manipulation;
}
.stats-title:hover {
  color: var(--primary);
}
.stats-title .chevron {
  color: var(--text3);
  transition: transform 0.2s ease;
}
.stats-title.expanded .chevron {
  transform: rotate(90deg);
  color: var(--primary);
}
.stats-title-hint {
  font-weight: 400;
  color: var(--text3);
  font-size: 11px;
}
.stats-title-note {
  font-weight: 400;
  color: var(--text3);
  font-size: 11px;
  margin-left: 4px;
}
.stats-list {
  margin-top: 6px;
}
.stats-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 6px;
}
.stats-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  background: rgba(0, 0, 0, 0.025);
  border-radius: 4px;
  border-left: 2px solid var(--primary);
  font-size: 11px;
  line-height: 1.4;
  min-width: 0;
}
.stats-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.stats-row-main .stats-name {
  font-weight: 600;
  color: var(--text1);
  font-size: 12px;
}
.stats-row-main .stats-meta {
  color: var(--text2);
}
.stats-row-main .stats-meta b {
  color: var(--text1);
  font-weight: 600;
}
.stats-row-wins {
  color: var(--text2);
  font-size: 10px;
  gap: 4px;
}
.stats-row-wins b {
  color: var(--primary);
  font-weight: 700;
}
.stats-row-wins .stats-sep {
  color: var(--text3);
}
.stats-card .up { color: var(--up-fg); font-weight: 600; }
.stats-card .down { color: var(--down-fg); font-weight: 600; }
.stats-row-outcome {
  color: var(--text2);
  font-size: 10px;
  gap: 4px;
}
.row-tag {
  display: inline-flex;
  align-items: center;
  font-size: 9px;
  font-weight: 600;
  padding: 0 4px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.06);
  color: var(--text2);
  margin-right: 2px;
}
.row-tag-real {
  background: var(--up-bg-muted);
  color: var(--up-fg);
}
.stats-row-outcome .hi { color: var(--up-fg); font-weight: 700; }
.stats-row-outcome .ratio { color: var(--text3); font-size: 9px; margin-left: 2px; }
.stats-row-outcome .pending { color: var(--warn-fg); background: var(--warn-bg-muted); padding: 0 4px; border-radius: 3px; }

/* ── 移动端信号卡片(内容填入 ResponsiveTable 的 #card 插槽, 卡片外框由 .rt-card 提供) ── */
.hc-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.hc-sig {
  font-size: 13px;
  font-weight: 600;
  color: var(--text1);
}
.hc-mid {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: 12px;
  color: var(--text2);
  margin-bottom: 6px;
}
.hc-name { color: var(--text1); font-weight: 500; }
.hc-code {
  font-family: monospace;
  color: var(--primary);
  cursor: pointer;
  touch-action: manipulation;
  font-variant-numeric: tabular-nums;
}
.hc-time { font-family: monospace; font-variant-numeric: tabular-nums; }
.hc-price { font-family: monospace; color: var(--text1); margin-left: auto; font-variant-numeric: tabular-nums; }
.hc-perf { font-size: 12px; margin-bottom: 4px; }
.hc-detail { font-size: 11px; color: var(--text2); line-height: 1.4; }

/* ── 按模型聚合概览 ── */
.overview-bar {
  background: var(--surface);
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
  padding: 8px 12px;
  margin-bottom: 10px;
}
.overview-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text1);
  cursor: pointer;
  user-select: none;
  padding: 2px 0;
  transition: color 0.15s;
  touch-action: manipulation;
  flex-wrap: wrap;
}
.overview-title:hover {
  color: var(--primary);
}
.overview-title .chevron {
  color: var(--text3);
  transition: transform 0.2s ease;
}
.overview-title.expanded .chevron {
  transform: rotate(90deg);
  color: var(--primary);
}
.overview-title-hint {
  font-weight: 400;
  color: var(--text3);
  font-size: 11px;
}
.overview-title-active {
  font-weight: 600;
  color: var(--primary);
  font-size: 11px;
  margin-left: 4px;
}
.overview-grid {
  margin-top: 6px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 8px;
}
.overview-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: rgba(0, 0, 0, 0.025);
  border-radius: 5px;
  border-left: 3px solid var(--text3);
  cursor: pointer;
  transition: background 0.12s, box-shadow 0.12s, border-color 0.12s;
  min-width: 0;
}
.overview-card:hover {
  background: rgba(0, 0, 0, 0.05);
}
.overview-card.ov-buy { border-left-color: var(--down-fg); }
.overview-card.ov-add { border-left-color: var(--down-fg); }
.overview-card.ov-sell { border-left-color: var(--up-fg); }
.overview-card.ov-reduce { border-left-color: var(--warn-fg); }
.overview-card.selected {
  background: var(--accent-bg-muted);
  box-shadow: 0 0 0 1px var(--primary) inset;
}
.ov-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ov-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-meta {
  display: flex;
  align-items: baseline;
  gap: 12px;
  font-size: 11px;
  color: var(--text2);
}
.ov-meta b { color: var(--text1); font-weight: 700; }
.ov-wr b { color: var(--primary); }
.ov-recent {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 2px;
}
.ov-snap {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 10px;
  color: var(--text2);
  line-height: 1.4;
  min-width: 0;
}
.ov-snap-code {
  font-family: monospace;
  color: var(--primary);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.ov-snap-name { color: var(--text1); flex-shrink: 0; }
.ov-snap-price {
  font-family: monospace;
  color: var(--text2);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.ov-snap-reason {
  color: var(--text3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.ov-snap-time {
  color: var(--text3);
  flex-shrink: 0;
  margin-left: auto;
}

@media (max-width: 768px) {
  /* 折叠开关已接管, 手机端取消 sticky 防与开关叠加 */
  .filter-bar {
    position: static;
    top: auto;
  }
  .overview-grid {
    grid-template-columns: 1fr;
  }
  .overview-card {
    padding: 8px;
  }
  .ov-snap-reason {
    flex-basis: 100%;
    order: 5;
  }
  .ov-snap-time {
    margin-left: 0;
  }
}
</style>
