<script setup lang="ts">
import { ref, computed, h } from 'vue'
import {
  NCard, NTabs, NTabPane, NInput, NButton, NUpload, NUploadDragger,
  NDataTable, NIcon, NStatistic, NSpin, NTag, NSpace, NInputNumber, NDatePicker, NSelect,
  type DataTableColumns,
} from 'naive-ui'
import { CloudUploadOutline, RefreshOutline } from '@vicons/ionicons5'
import {
  importText, importHistory, importExcel, compareToModel,
  type AnalysisResult, type CompareResult, type PairedTrade,
} from '../api/trade-analysis'
import { useResponsive } from '../composables/useResponsive'
import FilterPanel from '../components/common/FilterPanel.vue'

const { isMobile } = useResponsive()
function mobCols(cols: DataTableColumns, keep: string[]): DataTableColumns {
  return isMobile.value ? cols.filter((c: any) => keep.includes(c.key)) : cols
}

const RED = 'var(--up-fg)'
const GREEN = 'var(--down-fg)'

const loading = ref(false)
const pasteText = ref('')
const historyText = ref('')
const historyDate = ref<number>(Date.now())   // 历史成交导入日期, 默认今天, 可改成过去某天
const result = ref<AnalysisResult | null>(null)
const showImport = ref(true)          // 导入条：导入后折叠

async function handlePasteAnalyze() {
  if (!pasteText.value.trim()) {
    ;(window as any).$message?.warning('请先粘贴交割单内容')
    return
  }
  loading.value = true
  try {
    const res = await importText(pasteText.value)
    if (!res.ok) { ;(window as any).$message?.error(res.msg || '解析失败'); return }
    onImported(res)
  } catch { ;(window as any).$message?.error('请求失败') }
  finally { loading.value = false }
}

async function handleHistoryAnalyze() {
  if (!historyText.value.trim()) {
    ;(window as any).$message?.warning('请先粘贴历史成交内容')
    return
  }
  if (!historyDate.value) {
    ;(window as any).$message?.warning('请选择成交日期')
    return
  }
  const d = new Date(historyDate.value)
  const dateStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  loading.value = true
  try {
    const res = await importHistory(historyText.value, dateStr)
    if (!res.ok) { ;(window as any).$message?.error(res.msg || '解析失败'); return }
    onImported(res)
  } catch { ;(window as any).$message?.error('请求失败') }
  finally { loading.value = false }
}

async function handleFileUpload({ file }: { file: { file: File } }) {
  loading.value = true
  try {
    const res = await importExcel(file.file)
    if (!res.ok) { ;(window as any).$message?.error(res.msg || '解析失败'); return }
    onImported(res)
  } catch { ;(window as any).$message?.error('请求失败') }
  finally { loading.value = false }
}

function onImported(res: AnalysisResult) {
  result.value = res
  showImport.value = false
  ;(window as any).$message?.success(`解析${res.record_count}条记录，配对${res.summary?.total_trades}笔交易`)
  runCompare()   // 导入后自动跑模型对比
}

// ── KPI 计算（前端从 trades 算盈亏比/期望/最大回撤）──
const trades = computed<PairedTrade[]>(() => result.value?.trades || [])
const records = computed(() => result.value?.records || [])

// ── 成交流水查询区（客户端过滤已加载记录）──
const recKeyword = ref('')
const recDirection = ref<'buy' | 'sell' | null>(null)
const recDateRange = ref<[number, number] | null>(null)
const recDirectionOptions = [
  { label: '买入', value: 'buy' },
  { label: '卖出', value: 'sell' },
]
function tsToDay(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
// 实时过滤：控件 v-model 一改, computed 立即重算(无草稿态/无查询按钮)
const filteredRecords = computed(() => {
  const kw = recKeyword.value.trim().toLowerCase()
  const direction = recDirection.value
  const range = recDateRange.value
  const from = range ? tsToDay(range[0]) : null
  const to = range ? tsToDay(range[1]) : null
  return records.value.filter((r: any) => {
    if (kw) {
      const code = String(r.code ?? '').toLowerCase()
      const name = String(r.name ?? '').toLowerCase()
      if (!code.includes(kw) && !name.includes(kw)) return false
    }
    if (direction && r.direction !== direction) return false
    if (from && to) {
      const day = String(r.trade_date ?? '')
      if (day < from || day > to) return false
    }
    return true
  })
})
function resetRecFilter() {
  recKeyword.value = ''
  recDirection.value = null
  recDateRange.value = null
}

const winsArr = computed(() => trades.value.filter(t => t.profit > 0))
const lossArr = computed(() => trades.value.filter(t => t.profit <= 0))
const avgWin = computed(() => winsArr.value.length ? winsArr.value.reduce((s, t) => s + t.profit, 0) / winsArr.value.length : 0)
const avgLoss = computed(() => lossArr.value.length ? lossArr.value.reduce((s, t) => s + t.profit, 0) / lossArr.value.length : 0)
const profitFactor = computed(() => avgLoss.value < 0 ? avgWin.value / -avgLoss.value : (avgWin.value > 0 ? Infinity : 0))
const expectancy = computed(() => trades.value.length ? trades.value.reduce((s, t) => s + t.profit, 0) / trades.value.length : 0)

// 累计盈亏曲线（按卖出日升序）+ 最大回撤
const curve = computed(() => {
  const sorted = [...trades.value].sort((a, b) => (a.sell_date < b.sell_date ? -1 : 1))
  let cum = 0, peak = 0, maxDD = 0
  const pts = sorted.map((t) => { cum += t.profit; peak = Math.max(peak, cum); maxDD = Math.max(maxDD, peak - cum); return { date: t.sell_date, cum } })
  return { pts, maxDD, final: cum }
})
const maxDrawdown = computed(() => curve.value.maxDD)

const CW = 640, CH = 200, CPAD = 6
const curveGeom = computed(() => {
  const pts = curve.value.pts
  if (pts.length < 2) return { path: '', zeroY: CH / 2, has: false }
  const ys = pts.map(p => p.cum)
  const ymin = Math.min(0, ...ys), ymax = Math.max(0, ...ys)
  const span = (ymax - ymin) || 1
  const xstep = (CW - 2 * CPAD) / (pts.length - 1)
  const yOf = (v: number) => CH - CPAD - (v - ymin) / span * (CH - 2 * CPAD)
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${(CPAD + i * xstep).toFixed(1)},${yOf(p.cum).toFixed(1)}`).join(' ')
  return { path, zeroY: yOf(0), has: true }
})

// 盈亏分布直方
const distBuckets = computed(() => {
  const defs: [number, number, string][] = [
    [-Infinity, -10, '≤-10%'], [-10, -5, '-10~-5'], [-5, 0, '-5~0'],
    [0, 5, '0~5'], [5, 10, '5~10'], [10, 20, '10~20'], [20, Infinity, '>20%'],
  ]
  return defs.map(([lo, hi, label]) => ({
    label, win: lo >= 0,
    count: trades.value.filter(t => t.return_pct > lo && t.return_pct <= hi).length,
  }))
})
const distMax = computed(() => Math.max(1, ...distBuckets.value.map(b => b.count)))

// 按持仓周期胜率
const holdBuckets = computed(() => {
  const defs: [number, number, string][] = [[0, 3, '1-3天'], [3, 7, '4-7天'], [7, 15, '8-15天'], [15, Infinity, '15天+']]
  return defs.map(([lo, hi, label]) => {
    const sub = trades.value.filter(t => t.hold_days > lo && t.hold_days <= hi)
    const w = sub.filter(t => t.profit > 0).length
    return {
      label, count: sub.length,
      winRate: sub.length ? w / sub.length * 100 : 0,
      avg: sub.length ? sub.reduce((s, t) => s + t.return_pct, 0) / sub.length : 0,
    }
  })
})

// ── 个股汇总 ──
const stockList = computed(() => {
  if (!result.value?.by_stock) return []
  return Object.entries(result.value.by_stock).map(([code, s]) => ({ code, ...s }))
    .sort((a, b) => b.net_profit - a.net_profit)
})

const stockColumns: DataTableColumns = [
  { title: '代码', key: 'code', width: 80 },
  { title: '名称', key: 'name', width: 90 },
  { title: '次数', key: 'total_trades', width: 64, align: 'center' },
  { title: '胜/负', key: 'win', width: 70, align: 'center', render: (r: any) => `${r.win_count}/${r.loss_count}` },
  { title: '净盈亏', key: 'net_profit', width: 100, align: 'right', render: (r: any) => h('span', { style: { color: r.net_profit >= 0 ? RED : GREEN, fontWeight: '600' } }, r.net_profit.toFixed(2)) },
  { title: '费用', key: 'total_fee', width: 76, align: 'right', render: (r: any) => r.total_fee.toFixed(2) },
  { title: '平均收益', key: 'avg_return_pct', width: 86, align: 'right', render: (r: any) => h('span', { style: { color: r.avg_return_pct >= 0 ? RED : GREEN } }, r.avg_return_pct.toFixed(2) + '%') },
  { title: '均持仓', key: 'avg_hold_days', width: 72, align: 'center', render: (r: any) => r.avg_hold_days + '天' },
  { title: '持仓', key: 'still_holding', width: 64, align: 'center', render: (r: any) => r.still_holding > 0 ? h(NTag, { size: 'small', type: 'warning' }, () => r.still_holding) : '-' },
]

// ── 成交流水（原始记录，时间倒序，核对最新导入）──
const recordColumns: DataTableColumns = [
  { title: '日期', key: 'trade_date', width: 100 },
  { title: '时间', key: 'trade_time', width: 74 },
  { title: '代码', key: 'code', width: 70 },
  { title: '名称', key: 'name', width: 90 },
  { title: '方向', key: 'direction', width: 60, align: 'center', render: (r: any) => h(NTag, { size: 'small', type: r.direction === 'buy' ? 'error' : 'success', bordered: false }, () => r.direction === 'buy' ? '买入' : '卖出') },
  { title: '价格', key: 'price', width: 78, align: 'right', render: (r: any) => r.price.toFixed(3) },
  { title: '数量', key: 'quantity', width: 78, align: 'right' },
  { title: '金额', key: 'amount', width: 100, align: 'right', render: (r: any) => r.amount.toFixed(2) },
  { title: '费用', key: 'fee', width: 76, align: 'right', render: (r: any) => r.fee.toFixed(2) },
]

const tradeColumns: DataTableColumns = [
  { title: '代码', key: 'code', width: 70 },
  { title: '名称', key: 'name', width: 80 },
  { title: '买入日期', key: 'buy_date', width: 95 },
  { title: '买入价', key: 'buy_price', width: 76, align: 'right', render: (r: any) => r.buy_price.toFixed(3) },
  { title: '卖出日期', key: 'sell_date', width: 95 },
  { title: '卖出价', key: 'sell_price', width: 76, align: 'right', render: (r: any) => r.sell_price.toFixed(3) },
  { title: '数量', key: 'quantity', width: 70, align: 'right' },
  { title: '盈亏', key: 'profit', width: 90, align: 'right', render: (r: any) => h('span', { style: { color: r.profit >= 0 ? RED : GREEN } }, r.profit.toFixed(2)) },
  { title: '收益率', key: 'return_pct', width: 78, align: 'right', render: (r: any) => h('span', { style: { color: r.return_pct >= 0 ? RED : GREEN } }, r.return_pct.toFixed(2) + '%') },
  { title: '持仓天', key: 'hold_days', width: 66, align: 'center' },
]

// ── 实盘 vs 模型对比 ──
const comparing = ref(false)
const signalWindow = ref(5)
const compareResult = ref<CompareResult | null>(null)

const EMPTY_STAT = { count: 0, win_rate: 0, avg_return: 0 }
const pnlAligned = computed(() => compareResult.value?.pnl_contrast?.aligned ?? EMPTY_STAT)
const pnlDeviated = computed(() => compareResult.value?.pnl_contrast?.deviated ?? EMPTY_STAT)

async function runCompare() {
  comparing.value = true
  try {
    const res = await compareToModel(signalWindow.value)
    if (!res.ok) { ;(window as any).$message?.error(res.msg || '对比失败'); compareResult.value = null; return }
    compareResult.value = res
    const noK = res.meta?.stocks_no_kline?.length || 0
    ;(window as any).$message?.success(`对比完成：评估${res.meta?.stocks_evaluated}/${res.meta?.stocks_total}只` + (noK ? `，${noK}只无K线跳过` : ''))
  } catch { ;(window as any).$message?.error('对比失败（重跑历史K线较慢，请稍后重试）') }
  finally { comparing.value = false }
}

function pctColor(v: number | null) { return v == null ? 'var(--fg-subtle)' : (v >= 0 ? RED : GREEN) }
function renderPct(v: number | null) { return v == null ? '-' : h('span', { style: { color: pctColor(v) } }, v.toFixed(2) + '%') }

const VERDICT_TYPE: Record<string, any> = { 符合模型: 'success', 偏离模型: 'warning', 无法评估: 'default', 卖太晚: 'warning', 卖太早: 'info' }
const renderVerdict = (row: any) => h(NTag, { size: 'small', type: VERDICT_TYPE[row.verdict] || 'default' }, () => row.verdict)

const buyCmpColumns: DataTableColumns = [
  { title: '代码', key: 'code', width: 70 },
  { title: '名称', key: 'name', width: 80 },
  { title: '买入日期', key: 'buy_date', width: 95 },
  { title: '买入价', key: 'buy_price', width: 75, align: 'right', render: (r: any) => r.buy_price.toFixed(2) },
  { title: '判定', key: 'verdict', width: 90, align: 'center', render: renderVerdict },
  { title: '命中买点', key: 'matched_signal_name', width: 130, render: (r: any) => r.matched_signal_name || '-' },
  { title: '信号距买入', key: 'signal_gap', width: 90, align: 'center', render: (r: any) => r.signal_gap == null ? '-' : `${r.signal_gap}日前` },
  { title: '信号详情', key: 'detail', minWidth: 240, ellipsis: { tooltip: true }, render: (r: any) => r.detail || '-' },
]
const sellCmpColumns: DataTableColumns = [
  { title: '代码', key: 'code', width: 70 },
  { title: '名称', key: 'name', width: 80 },
  { title: '买入日期', key: 'buy_date', width: 95 },
  { title: '卖出日期', key: 'sell_date', width: 95 },
  { title: '实际收益', key: 'actual_return', width: 85, align: 'right', render: (r: any) => renderPct(r.actual_return) },
  { title: '持仓天', key: 'hold_days', width: 65, align: 'center' },
  { title: '判定', key: 'verdict', width: 85, align: 'center', render: renderVerdict },
  { title: '时点差', key: 'day_diff', width: 90, align: 'center', render: (r: any) => { const d = r.day_diff; if (d == null) return '-'; if (d === 0) return '同日'; return d > 0 ? `晚${d}日` : `早${-d}日` } },
  { title: '模型卖点', key: 'model_exit_date', width: 95 },
  { title: '模型原因', key: 'model_reason', width: 150, ellipsis: { tooltip: true }, render: (r: any) => r.model_reason || '-' },
  { title: '模型收益', key: 'model_return', width: 85, align: 'right', render: (r: any) => renderPct(r.model_return) },
]
const missedColumns: DataTableColumns = [
  { title: '代码', key: 'code', width: 70 },
  { title: '名称', key: 'name', width: 80 },
  { title: '信号日期', key: 'signal_date', width: 100 },
  { title: '买点', key: 'signal_name', width: 130 },
  { title: '后5日', key: 'forward_ret_5d', width: 85, align: 'right', render: (r: any) => renderPct(r.forward_ret_5d) },
  { title: '信号详情', key: 'detail', minWidth: 240, ellipsis: { tooltip: true }, render: (r: any) => r.detail || '-' },
]

const recordColumnsM = computed(() => mobCols(recordColumns, ['trade_date', 'name', 'direction', 'price', 'quantity']))
const stockColumnsM = computed(() => mobCols(stockColumns, ['code', 'name', 'total_trades', 'net_profit', 'avg_return_pct']))
const tradeColumnsM = computed(() => mobCols(tradeColumns, ['code', 'name', 'buy_date', 'profit', 'return_pct']))
const buyCmpColumnsM = computed(() => mobCols(buyCmpColumns, ['code', 'name', 'verdict', 'matched_signal_name']))
const sellCmpColumnsM = computed(() => mobCols(sellCmpColumns, ['code', 'name', 'verdict', 'actual_return', 'day_diff']))
const missedColumnsM = computed(() => mobCols(missedColumns, ['code', 'name', 'signal_name', 'forward_ret_5d']))

const fmtMoney = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(0)
const summary = computed(() => result.value?.summary)
</script>

<template>
  <div class="trade-analysis">
    <!-- ① 导入条 -->
    <NCard size="small" :title="result ? undefined : '交易分析 · 导入交割单'">
      <div v-if="result && !showImport" class="import-collapsed">
        <span class="ic-text">✓ 已导入 <b>{{ result.record_count }}</b> 条成交记录 · 配对 <b>{{ summary?.total_trades }}</b> 笔交易</span>
        <NButton size="small" tertiary @click="showImport = true">
          <template #icon><NIcon><RefreshOutline /></NIcon></template>
          重新导入
        </NButton>
      </div>
      <NTabs v-if="!result || showImport" type="segment" size="small">
        <NTabPane name="paste" tab="粘贴导入">
          <NInput v-model:value="pasteText" type="textarea"
            placeholder="从同花顺交割单复制内容后粘贴到这里（Ctrl+V）" :rows="6"
            style="margin-bottom: 12px; font-family: monospace; font-size: 12px;" />
          <NButton type="primary" :loading="loading" @click="handlePasteAnalyze">开始分析</NButton>
        </NTabPane>
        <NTabPane name="history" tab="历史成交">
          <div class="history-bar">
            <span class="history-label">成交日期</span>
            <NDatePicker v-model:value="historyDate" type="date" :is-date-disabled="(ts: number) => ts > Date.now()"
              style="width: 160px;" />
            <span class="history-hint">默认今天，补录历史可改</span>
          </div>
          <NInput v-model:value="historyText" type="textarea"
            placeholder="从平安证券「历史成交」复制内容后粘贴到这里（无需日期列，连同表头：成交时间 证券代码 操作 …）" :rows="6"
            style="margin: 12px 0; font-family: monospace; font-size: 12px;" />
          <NButton type="primary" :loading="loading" @click="handleHistoryAnalyze">开始分析</NButton>
          <div class="history-note">该日按本次粘贴替换（先清当日旧记录再写入），防与交割单同日重复计数。</div>
        </NTabPane>
        <NTabPane name="upload" tab="文件上传">
          <NUpload :default-upload="false" accept=".xlsx,.xls" :max="1"
            @change="({ file }: any) => file.status === 'pending' && handleFileUpload({ file })">
            <NUploadDragger>
              <div style="padding: 20px 0;">
                <NIcon :size="36" :depth="3"><CloudUploadOutline /></NIcon>
                <p style="margin: 8px 0 0; color: var(--fg-muted);">点击或拖拽上传 Excel 文件（.xlsx）</p>
              </div>
            </NUploadDragger>
          </NUpload>
        </NTabPane>
      </NTabs>
    </NCard>

    <NSpin :show="loading">
      <template v-if="summary">
        <!-- ② KPI 英雄区 -->
        <div class="kpi-hero">
          <div class="kpi-card primary">
            <div class="kpi-label">净盈亏</div>
            <div class="kpi-val" :style="{ color: summary.net_profit >= 0 ? RED : GREEN }">{{ fmtMoney(summary.net_profit) }}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">胜率</div>
            <div class="kpi-val">{{ summary.win_rate }}%</div>
            <div class="kpi-sub">{{ summary.win_count }}胜 / {{ summary.loss_count }}负</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">盈亏比</div>
            <div class="kpi-val">{{ profitFactor === Infinity ? '∞' : profitFactor.toFixed(2) }}</div>
            <div class="kpi-sub">均盈 {{ avgWin.toFixed(0) }} / 均亏 {{ avgLoss.toFixed(0) }}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">平均持仓</div>
            <div class="kpi-val">{{ summary.avg_hold_days }}<span class="kpi-unit">天</span></div>
          </div>
        </div>
        <div class="kpi-strip">
          <span>期望 <b :style="{ color: expectancy >= 0 ? RED : GREEN }">{{ fmtMoney(expectancy) }}</b>/笔</span>
          <span>最大回撤 <b :style="{ color: GREEN }">-{{ maxDrawdown.toFixed(0) }}</b></span>
          <span>总费用 {{ summary.total_fee.toFixed(0) }}</span>
          <span>最大盈 <b :style="{ color: RED }">{{ summary.max_profit_pct.toFixed(1) }}%</b></span>
          <span>最大亏 <b :style="{ color: GREEN }">{{ summary.max_loss_pct.toFixed(1) }}%</b></span>
          <span>交易 {{ summary.total_trades }} 笔</span>
        </div>

        <!-- ③ Tab 分区 -->
        <NCard size="small" class="tab-card">
          <NTabs type="line" size="medium" animated>
            <!-- 概览 -->
            <NTabPane name="overview" tab="概览">
              <div class="chart-grid">
                <div class="chart-box wide">
                  <div class="chart-title">累计盈亏曲线<span class="ct-note">按卖出日，毛盈亏</span></div>
                  <svg v-if="curveGeom.has" :viewBox="`0 0 ${CW} ${CH}`" class="curve-svg" preserveAspectRatio="none">
                    <line :x1="0" :x2="CW" :y1="curveGeom.zeroY" :y2="curveGeom.zeroY" style="stroke: var(--border-default)" stroke-width="1" stroke-dasharray="4 3" />
                    <path :d="curveGeom.path" fill="none" :style="{ stroke: curve.final >= 0 ? RED : GREEN }" stroke-width="2" />
                  </svg>
                  <div v-else class="chart-empty">交易笔数不足</div>
                </div>
                <div class="chart-box">
                  <div class="chart-title">盈亏分布<span class="ct-note">按收益率分档</span></div>
                  <div class="bar-rows">
                    <div v-for="b in distBuckets" :key="b.label" class="bar-row">
                      <span class="bar-label">{{ b.label }}</span>
                      <span class="bar-track"><span class="bar-fill" :style="{ width: (b.count / distMax * 100) + '%', background: b.win ? RED : GREEN }"></span></span>
                      <span class="bar-num">{{ b.count }}</span>
                    </div>
                  </div>
                </div>
                <div class="chart-box">
                  <div class="chart-title">按持仓周期<span class="ct-note">胜率 / 均收益</span></div>
                  <div class="bar-rows">
                    <div v-for="b in holdBuckets" :key="b.label" class="bar-row">
                      <span class="bar-label">{{ b.label }}</span>
                      <span class="bar-track"><span class="bar-fill" :style="{ width: b.winRate + '%', background: 'var(--accent-fg)' }"></span></span>
                      <span class="bar-num">{{ b.count ? b.winRate.toFixed(0) + '%' : '-' }}</span>
                      <span class="bar-avg" :style="{ color: b.avg >= 0 ? RED : GREEN }">{{ b.count ? (b.avg >= 0 ? '+' : '') + b.avg.toFixed(1) + '%' : '' }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </NTabPane>

            <!-- 个股 -->
            <NTabPane name="stock" tab="个股汇总">
              <NDataTable :columns="stockColumnsM" :data="stockList" :bordered="false" size="small" :pagination="false" max-height="460" />
            </NTabPane>

            <!-- 成交流水（核对导入）+ 交易回合 -->
            <NTabPane name="records" tab="成交流水">
              <p class="tab-hint">原始成交记录，<b>按时间倒序</b>（最新在最上）——用于核对最新导入的交割单是否正确、有无漏单/错单。</p>
              <FilterPanel>
              <div class="filter-bar">
                <div class="filter-fields">
                  <div class="filter-item">
                    <label>关键词</label>
                    <NInput
                      v-model:value="recKeyword"
                      size="small"
                      clearable
                      placeholder="代码/名称"
                    />
                  </div>
                  <div class="filter-item">
                    <label>方向</label>
                    <NSelect
                      v-model:value="recDirection"
                      :options="recDirectionOptions"
                      size="small"
                      clearable
                      placeholder="全部"
                    />
                  </div>
                  <div class="filter-item" style="min-width: 220px">
                    <label>日期段</label>
                    <NDatePicker
                      v-model:value="recDateRange"
                      type="daterange"
                      size="small"
                      clearable
                      format="yyyy-MM-dd"
                      placement="bottom"
                      to="body"
                    />
                  </div>
                </div>
                <div class="filter-actions">
                  <NButton size="small" type="primary" @click="resetRecFilter">
                    <template #icon><NIcon><RefreshOutline /></NIcon></template>
                    重置
                  </NButton>
                </div>
              </div>
              </FilterPanel>
              <NDataTable :columns="recordColumnsM" :data="filteredRecords" :bordered="false" size="small" :pagination="{ pageSize: 30 }" max-height="520" />
              <div class="sub-title">配对交易（买入→卖出回合）</div>
              <NDataTable :columns="tradeColumnsM" :data="trades" :bordered="false" size="small" :pagination="{ pageSize: 20 }" max-height="440" />
            </NTabPane>

            <!-- 模型对比 -->
            <NTabPane name="compare" tab="模型对比">
              <NSpace align="center" style="margin-bottom: 4px;">
                <span>信号有效期窗口</span>
                <NInputNumber v-model:value="signalWindow" :min="1" :max="15" size="small" style="width: 120px;" />
                <span style="color: var(--fg-subtle);">交易日</span>
                <NButton size="small" type="primary" :loading="comparing" @click="runCompare">重新对比</NButton>
              </NSpace>
              <p class="tab-hint">导入后已自动运行。用现役买卖点检测器（弱势极限 / 强势起点 / 回踩20MA缩量后突破昨高 + 模型卖出规则）在历史K线上重跑，判定每笔是否符合模型；买入往前看 {{ signalWindow }} 个交易日内有信号即算「符合」。</p>

              <NSpin :show="comparing">
                <div v-if="compareResult?.ok">
                  <div class="contrast-grid">
                    <div class="contrast-card aligned">
                      <div class="contrast-title">听模型（符合模型的买入）</div>
                      <div class="contrast-row">
                        <NStatistic label="笔数" tabular-nums>{{ pnlAligned.count }}</NStatistic>
                        <NStatistic label="胜率" tabular-nums>{{ pnlAligned.win_rate }}%</NStatistic>
                        <NStatistic label="平均收益" tabular-nums><span :style="{ color: pctColor(pnlAligned.avg_return) }">{{ pnlAligned.avg_return.toFixed(2) }}%</span></NStatistic>
                      </div>
                    </div>
                    <div class="contrast-card deviated">
                      <div class="contrast-title">凭感觉（偏离模型的买入）</div>
                      <div class="contrast-row">
                        <NStatistic label="笔数" tabular-nums>{{ pnlDeviated.count }}</NStatistic>
                        <NStatistic label="胜率" tabular-nums>{{ pnlDeviated.win_rate }}%</NStatistic>
                        <NStatistic label="平均收益" tabular-nums><span :style="{ color: pctColor(pnlDeviated.avg_return) }">{{ pnlDeviated.avg_return.toFixed(2) }}%</span></NStatistic>
                      </div>
                    </div>
                  </div>

                  <div class="sub-title">买点对比
                    <span class="st-meta">符合 {{ compareResult.buy_compare?.aligned }} · 偏离 {{ compareResult.buy_compare?.deviated }} · 无法评估 {{ compareResult.buy_compare?.not_evaluable }}（共 {{ compareResult.buy_compare?.total }} 笔）</span>
                  </div>
                  <NDataTable :columns="buyCmpColumnsM" :data="compareResult.buy_compare?.details || []" :bordered="false" size="small" :pagination="{ pageSize: 15 }" max-height="420" />

                  <div class="sub-title">卖点对比
                    <span class="st-meta">符合 {{ compareResult.sell_compare?.aligned }} · 卖太晚 {{ compareResult.sell_compare?.too_late }} · 卖太早 {{ compareResult.sell_compare?.too_early }} · 无法评估 {{ compareResult.sell_compare?.not_evaluable }}（共 {{ compareResult.sell_compare?.total }} 笔）</span>
                  </div>
                  <NDataTable :columns="sellCmpColumnsM" :data="compareResult.sell_compare?.details || []" :bordered="false" size="small" :pagination="{ pageSize: 15 }" max-height="420" />

                  <div class="sub-title">错过的信号
                    <span class="st-meta">模型给了买点但你没买（{{ compareResult.missed_signals?.length || 0 }} 个）</span>
                  </div>
                  <NDataTable :columns="missedColumnsM" :data="compareResult.missed_signals || []" :bordered="false" size="small" :pagination="{ pageSize: 15 }" max-height="420" />
                </div>
                <div v-else-if="!comparing" class="chart-empty">模型对比尚无结果，点「重新对比」运行。</div>
              </NSpin>
            </NTabPane>
          </NTabs>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.trade-analysis { padding: 16px; max-width: 1200px; }

.import-collapsed { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.ic-text { font-size: 13px; color: var(--fg-default); }
.ic-text b { color: var(--accent-fg); }

/* KPI 英雄区 */
.kpi-hero { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 14px; }
.kpi-card { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 10px; padding: 14px 16px; }
.kpi-card.primary { background: linear-gradient(135deg, var(--bg-surface) 60%, color-mix(in srgb, var(--up-fg) 7%, transparent)); border-color: color-mix(in srgb, var(--up-fg) 25%, transparent); }
.kpi-label { font-size: 12px; color: var(--fg-muted); margin-bottom: 4px; }
.kpi-val { font-size: 26px; font-weight: 700; font-family: 'DIN', monospace; line-height: 1.1; font-variant-numeric: tabular-nums; }
.kpi-unit { font-size: 14px; font-weight: 500; margin-left: 2px; }
.kpi-sub { font-size: 11px; color: var(--fg-subtle); margin-top: 3px; }
.kpi-strip { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 10px; padding: 8px 14px; background: var(--bg-default); border-radius: 8px; font-size: 12.5px; color: var(--fg-muted); font-variant-numeric: tabular-nums; }
.kpi-strip b { font-weight: 600; }

.tab-card { margin-top: 14px; }
.tab-hint { font-size: 12px; color: var(--fg-subtle); margin: 0 0 10px; line-height: 1.5; }
.tab-hint b { color: var(--fg-muted); }
.sub-title { font-size: 13px; font-weight: 600; color: var(--fg-muted); margin: 18px 0 8px; }
.st-meta { font-weight: 400; color: var(--fg-muted); font-size: 12px; margin-left: 8px; }

/* 图表 */
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chart-box { border: 1px solid var(--border-default); border-radius: 8px; padding: 12px; background: var(--bg-surface); }
.chart-box.wide { grid-column: 1 / -1; }
.chart-title { font-size: 13px; font-weight: 600; color: var(--fg-muted); margin-bottom: 10px; }
.ct-note { font-weight: 400; color: var(--fg-subtle); font-size: 11px; margin-left: 6px; }
.curve-svg { width: 100%; height: 200px; display: block; }
.chart-empty { color: var(--fg-subtle); font-size: 12px; text-align: center; padding: 40px 0; }

.bar-rows { display: flex; flex-direction: column; gap: 7px; }
.bar-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.bar-label { width: 56px; color: var(--fg-muted); flex-shrink: 0; text-align: right; }
.bar-track { flex: 1; height: 14px; background: var(--bg-default); border-radius: 4px; overflow: hidden; }
.bar-fill { display: block; height: 100%; border-radius: 4px; transition: width .3s; }
.bar-num { width: 42px; text-align: right; color: var(--fg-muted); font-variant-numeric: tabular-nums; flex-shrink: 0; }
.bar-avg { width: 52px; text-align: right; font-variant-numeric: tabular-nums; flex-shrink: 0; }

/* 模型对比对照卡 */
.contrast-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.contrast-card { border-radius: 8px; padding: 16px; border: 1px solid var(--border-default); }
.contrast-card.aligned { background: color-mix(in srgb, var(--success-fg) 8%, transparent); border-color: color-mix(in srgb, var(--success-fg) 32%, transparent); }
.contrast-card.deviated { background: color-mix(in srgb, var(--warn-fg) 9%, transparent); border-color: color-mix(in srgb, var(--warn-fg) 32%, transparent); }
.contrast-title { font-weight: 600; margin-bottom: 12px; }
.contrast-row { display: flex; gap: 24px; }

/* 成交流水查询区（与日志页 .filter-bar 视觉一致）*/
.filter-bar {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px 24px;
  align-items: end;
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
  color: var(--fg-muted);
  white-space: nowrap;
}
.filter-actions {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  justify-content: flex-end;
}

.history-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.history-label { font-size: 13px; color: var(--fg-default); }
.history-hint { font-size: 12px; color: var(--fg-subtle); }
.history-note { margin-top: 8px; font-size: 12px; color: var(--fg-subtle); }

@media (max-width: 768px) {
  .kpi-hero { grid-template-columns: repeat(2, 1fr); }
  .chart-grid { grid-template-columns: 1fr; }
  .contrast-grid { grid-template-columns: 1fr; }
  .filter-bar { grid-template-columns: 1fr; }
  .filter-actions { justify-content: flex-start; }
}
</style>
