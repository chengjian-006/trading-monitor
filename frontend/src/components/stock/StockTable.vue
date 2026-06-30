<script setup lang="ts">
import { NDataTable, NButton, NSpace, NIcon, NPopover, NPopconfirm } from 'naive-ui'
import { useGlobalMessage } from '../../composables/useGlobalMessage'
import { formatYi } from '../../utils/formatAmount'
import { h, computed, ref, toRef, onMounted, onUnmounted } from 'vue'
import { StarOutline, Star, CartOutline, Cart, TrashOutline, SwapVerticalOutline, CreateOutline, ReorderThreeOutline, ArrowUpOutline, ArrowDownOutline, NotificationsOutline, Notifications } from '@vicons/ionicons5'
import type { Stock, Signal } from '../../types'
import { useStockStore } from '../../stores/stock'
import { useSignalStore } from '../../stores/signal'
import MiniSparkline from '../chart/MiniSparkline.vue'
import { useUiStore } from '../../stores/ui'
import { useIntradaySparklines } from '../../composables/useIntradaySparklines'
import { useSmallAmountTags } from '../../composables/useSmallAmountTags'
import { useAmountRank } from '../../composables/useAmountRank'
import { useDecisionContext } from '../../composables/useDecisionContext'
import { exportPoolXlsx } from '../../utils/exportXlsx'
import { resonanceLevel } from '../../utils/poolFormat'
import StrategyText from './StrategyText.vue'
import StrategyEditModal from './StrategyEditModal.vue'
import StockAlertModal from './StockAlertModal.vue'
import { useStockAlerts } from '../../composables/useStockAlerts'

const stockStore = useStockStore()
const signalStore = useSignalStore()
const message = useGlobalMessage()
const ui = useUiStore()

import { useSignalGrouping } from '../../composables/useSignalGrouping'
const { signalsByCode } = useSignalGrouping(() => signalStore.signals)

// v1.7.x: 小额标签 + 决策上下文 已抽到 composables
const { smallAmountMap } = useSmallAmountTags(toRef(stockStore, 'stocks'))
const { computeDecision } = useDecisionContext()

const props = defineProps<{ stocks: Stock[]; showSparkline?: boolean }>()
const { sparklineMap } = useIntradaySparklines(() => props.stocks.map(s => s.code))
const { rankMap: amountRankMap } = useAmountRank()

// 拖拽排序: 记录正在拖的票, 松手落到目标行即重排
const dragCode = ref<string | null>(null)
function onRowDrop(targetCode: string) {
  const src = dragCode.value
  dragCode.value = null
  if (!src || src === targetCode) return
  stockStore.reorderTo(src, targetCode)
}

// 点代码 → 全局通用个股详情弹窗(内容复用 StockCharts, 与整页 /intraday 同源)
function openIntraday(code: string, name: string) {
  ui.openStock(code, name)
}

const expandedKeys = ref<string[]>([])

const SIGNAL_ADVICE: Record<string, string> = {
  buy: '可关注，等待确认后考虑建仓',
  add: '已持仓可考虑加仓',
  sell: '已持仓者考虑减仓或止损',
  reduce: '仓位较重可考虑减仓锁定利润',
}

function handleRowClick(row: Stock) {
  if (!signalsByCode.value.has(row.code)) return
  toggleExpand(row.code)
}

function toggleExpand(code: string) {
  if (expandedKeys.value.includes(code)) {
    expandedKeys.value = []
  } else {
    expandedKeys.value = [code]
  }
}

function handleExpandedKeysUpdate(keys: Array<string | number>) {
  expandedKeys.value = keys.map(String)
}

function renderDecisionCard(row: Stock, buySignals: Signal[]) {
  const verdict = computeDecision(row, buySignals)
  if (!verdict) return null
  return h('div', {
    style: {
      padding: '6px 10px',
      marginBottom: '6px',
      background: `${verdict.color}0F`,
      borderLeft: `3px solid ${verdict.color}`,
      borderRadius: '4px',
    },
  }, [
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', flexWrap: 'wrap' } }, [
      h('span', { style: { fontSize: '12px', color: 'var(--text2)' } }, '🎯 决策快查'),
      h('span', { style: { fontSize: '13px', fontWeight: 700, color: verdict.color } }, verdict.label),
      h('span', { style: { fontSize: '11px', color: verdict.color, fontWeight: 600 } }, `建议仓位 ${verdict.size}`),
    ]),
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: '1px' } },
      verdict.reasons.map(r => {
        const prefix = r.sign === 'pos' ? '✓ ' : r.sign === 'neg' ? '✗ ' : '· '
        const color = r.sign === 'pos' ? '#16a34a' : r.sign === 'neg' ? '#dc2626' : 'var(--text2)'
        return h('div', { style: { fontSize: '11px', color, lineHeight: '1.4' } },
          [h('span', { style: { fontWeight: 700, marginRight: '4px' } }, prefix), r.text])
      })
    ),
  ])
}

// 名称列 verdict 色块: 当行有买点信号时, 用 verdict 颜色给名称加左边一道色条
function getVerdictColor(row: Stock): string | null {
  const signals = signalsByCode.value.get(row.code)
  if (!signals) return null
  const buy = signals.filter((s: Signal) => s.direction === 'buy' || s.direction === 'add')
  if (!buy.length) return null
  const v = computeDecision(row, buy)
  return v ? v.color : null
}

function renderExpand(row: Stock) {
  const signals = signalsByCode.value.get(row.code)
  if (!signals || !signals.length) return null

  const cards: any[] = []
  const buySignals = signals.filter((s: Signal) => s.direction === 'buy' || s.direction === 'add')
  const decisionCard = renderDecisionCard(row, buySignals)
  if (decisionCard) cards.push(decisionCard)

  // ── 区域一: 个股实际情况 ──
  const firstSig = signals[0]
  if (firstSig.indicators) {
    const ind = firstSig.indicators
    const items: string[] = []
    if (ind.close != null) items.push(`现价 ${ind.close.toFixed(2)}`)
    if (ind.ma5 != null) items.push(`MA5 ${ind.ma5.toFixed(2)}`)
    if (ind.ma10 != null) items.push(`MA10 ${ind.ma10.toFixed(2)}`)
    if (ind.ma20 != null) items.push(`MA20 ${ind.ma20.toFixed(2)}`)
    if (ind.rsi != null) items.push(`RSI ${ind.rsi.toFixed(0)}`)
    if (ind.vol_ratio_5 != null) items.push(`量比 ${ind.vol_ratio_5.toFixed(2)}`)
    if (ind.pct_change != null) items.push(`涨幅 ${ind.pct_change >= 0 ? '+' : ''}${ind.pct_change.toFixed(2)}%`)
    if (items.length) {
      cards.push(h('div', {
        style: { padding: '5px 10px', marginBottom: '8px', background: '#f8fafc', borderRadius: '6px', border: '1px solid #e8ecf0' },
      }, [
        h('div', { style: { fontSize: '11px', fontWeight: 700, color: 'var(--text2)', marginBottom: '3px' } }, '📊 个股现状'),
        h('div', { style: { fontSize: '12px', color: 'var(--text1)', fontFamily: 'monospace', lineHeight: '1.5', display: 'flex', flexWrap: 'wrap', gap: '4px 14px' } },
          items.map((item: string) => h('span', { style: {} }, item)))
      ]))
    }
  }

  // ── 区域二: 模型规则 ──
  cards.push(h('div', { style: { fontSize: '11px', fontWeight: 700, color: 'var(--text2)', marginBottom: '4px' } }, '📐 模型信号'))
  cards.push(...signals.map((sig: Signal) => {
    const isBuy = sig.direction === 'buy' || sig.direction === 'add'
    const dirColor = isBuy ? 'var(--red)' : 'var(--green)'
    const dirIcon = isBuy ? '▲' : '▼'
    const borderColor = isBuy ? '#ff3b00' : '#16a34a'
    const parts: any[] = []

    // 头部：方向+信号名+时间
    parts.push(h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px', lineHeight: '1.3' } }, [
      h('span', { style: { fontWeight: 700, fontSize: '13px', color: dirColor } }, `${dirIcon} ${sig.signal_name}`),
      h('span', { style: { fontSize: '11px', color: 'var(--text2)' } }, (sig.triggered_at?.slice(11, 16) || sig.time?.slice(0, 5) || '') + ' 触发'),
    ]))

    // 详情（模型规则描述）
    if (sig.detail) {
      parts.push(h('div', { style: { fontSize: '12px', color: '#555', marginBottom: '2px', lineHeight: '1.35' } }, sig.detail))
    }

    // 建议
    const advice = SIGNAL_ADVICE[sig.direction]
    if (advice) {
      parts.push(h('div', { style: { fontSize: '11px', color: dirColor, fontWeight: 500, borderTop: '1px dashed #e0e0e0', paddingTop: '3px', marginTop: '2px', lineHeight: '1.3' } }, `💡 ${advice}`))
    }

    const isReduce = sig.direction === 'reduce'
    return h('div', {
      style: {
        padding: '5px 10px', marginBottom: '4px',
        borderLeft: `3px solid ${isReduce ? '#eab308' : borderColor}`,
        background: isReduce ? 'rgba(250, 204, 21, 0.14)' : (isBuy ? 'rgba(255, 59, 0, 0.04)' : 'rgba(22, 163, 74, 0.04)'),
        borderRadius: '4px',
      },
    }, parts)
  }))

  // ── 我的策略 ──
  const stockStrategy = props.stocks.find(s => s.code === row.code)?.strategy?.trim()
  if (stockStrategy) {
    cards.push(h('div', {
      style: {
        fontSize: '12px', color: '#5b21b6', fontWeight: 500,
        padding: '4px 10px', marginTop: '4px',
        background: 'rgba(124, 58, 237, 0.06)',
        borderLeft: '3px solid #7c3aed', borderRadius: '4px',
        whiteSpace: 'pre-wrap', lineHeight: '1.35',
      },
    }, `📋 我的策略: ${stockStrategy}`))
  }

  return h('div', { class: 'signal-expand' }, cards)
}

const sortState = ref<{ columnKey: string; order: string } | null>(null)

function handleSorterChange(sorter: any) {
  if (sorter && sorter.order) {
    sortState.value = { columnKey: sorter.columnKey, order: sorter.order }
  } else {
    sortState.value = null
  }
}

function resetSort() {
  sortState.value = null
}

// 导出当前股票池: 尊重当前排序(若有), 否则按池内自然顺序
async function exportXlsx() {
  const list = [...props.stocks]
  const ss = sortState.value
  if (ss && ss.order) {
    const k = ss.columnKey
    const getVal = (r: Stock): number | string => {
      if (k === 'amount_rank') return amountRankMap.value[r.code] ?? 999
      if (k === 'industry') return r.industry ?? ''
      const v = (r as any)[k]
      return v == null ? -Infinity : v
    }
    list.sort((a, b) => {
      const va = getVal(a), vb = getVal(b)
      const c = (typeof va === 'string' || typeof vb === 'string')
        ? String(va).localeCompare(String(vb))
        : (va as number) - (vb as number)
      return ss.order === 'descend' ? -c : c
    })
  }
  await exportPoolXlsx(list, amountRankMap.value, signalsByCode.value)
}

defineExpose({ sortState, resetSort, exportXlsx })

async function handleDelete(code: string, name: string) {
  // v1.7.x: 二次确认下沉到删除按钮的 NPopconfirm(持仓票动态显示更重的警告), 此处只执行删除
  try {
    await stockStore.removeStock(code)
    message.success(() => h('span', {}, [h('b', name), `（${code}）已删除`]))
  } catch {
    message.error(() => h('span', {}, ['删除 ', h('b', name), `（${code}）失败`]))
  }
}

async function handleToggleHold(row: Stock) {
  const next = row.status === 'hold' ? 'watch' : 'hold'
  try {
    await stockStore.updateStock(row.code, { status: next })
    row.status = next
  } catch {
    message.error('操作失败')
  }
}

async function handleToggleFocus(row: Stock) {
  const next = row.focused ? 0 : 1
  try {
    await stockStore.updateStock(row.code, { focused: String(next) })
    row.focused = next
  } catch {
    message.error('操作失败')
  }
}

// ── 操作策略编辑 ──
const showStrategyModal = ref(false)
const strategyEditCode = ref('')
const strategyEditName = ref('')
const strategyEditText = ref('')
const strategyRowRef = ref<Stock | null>(null)

function openStrategyModal(row: Stock) {
  strategyEditCode.value = row.code
  strategyEditName.value = row.name
  strategyEditText.value = row.strategy || ''
  strategyRowRef.value = row
  showStrategyModal.value = true
}

function onStrategySaved(_code: string, text: string) {
  if (strategyRowRef.value) strategyRowRef.value.strategy = text
}

// ── 自定义预警 ──
const { loadAlerts, reloadAlerts, summaryFor } = useStockAlerts()
const showAlertModal = ref(false)
const alertRow = ref<Stock | null>(null)
function openAlertModal(row: Stock) {
  alertRow.value = row
  showAlertModal.value = true
}


// 呼吸高亮(30分钟内有新信号)预聚合成 Set: 3s 行情刷新触发的每次重渲染
// 不再逐行遍历该票全部信号算时间差, 只查表。窗口过期靠每分钟 tick 重算。
const recentTick = ref(0)
let recentTimer: number | null = null
onMounted(() => { recentTimer = window.setInterval(() => { recentTick.value++ }, 60_000); loadAlerts() })
onUnmounted(() => { if (recentTimer) { clearInterval(recentTimer); recentTimer = null } })
const recentSignalCodes = computed(() => {
  void recentTick.value   // 每分钟失效重算, 否则超30分钟的高亮不会熄灭
  const now = Date.now()
  const thirtyMin = 30 * 60 * 1000
  const set = new Set<string>()
  for (const [code, signals] of signalsByCode.value) {
    if (signals.some(s => s.triggered_at && (now - new Date(s.triggered_at).getTime()) < thirtyMin)) {
      set.add(code)
    }
  }
  return set
})

// row-props 提成稳定函数引用(模板内联箭头函数每次父级重渲染都重建)
function rowProps(row: Stock) {
  const classes: string[] = []
  const isHold = row.status === 'hold'
  const hasSignal = signalsByCode.value.has(row.code)
  if (isHold) {
    classes.push('row-hold')
  } else if (recentSignalCodes.value.has(row.code)) {
    classes.push('row-signal-breathing')
  }
  if (hasSignal) {
    classes.push('row-clickable')
  }
  return {
    class: classes.join(' ') || undefined,
    onClick: () => handleRowClick(row),
    onDragover: (e: DragEvent) => e.preventDefault(),
    onDrop: (e: DragEvent) => { e.preventDefault(); onRowDrop(row.code) },
  }
}

function flashClass(code: string, field: 'price' | 'pct_change' | 'speed'): string {
  const f = stockStore.flashMap.get(code)
  if (!f || !f[field]) return ''
  return f[field] === 'up' ? 'flash-up' : 'flash-down'
}

// 行情新鲜度: 盘中 quote_updated_at 超 3 分钟 → 标"滞后"(行情可能没刷上, 数据自检/自愈会兜底)
function tradingNow(): boolean {
  const d = new Date()
  if (d.getDay() === 0 || d.getDay() === 6) return false
  const hm = d.getHours() * 60 + d.getMinutes()
  return (hm >= 9 * 60 + 30 && hm <= 11 * 60 + 30) || (hm >= 13 * 60 && hm <= 15 * 60)
}
function quoteStaleMin(row: Stock): number | null {
  if (!row.quote_updated_at) return null
  const t = new Date(row.quote_updated_at).getTime()
  if (isNaN(t)) return null
  return (Date.now() - t) / 60000
}
function isQuoteStale(row: Stock): boolean {
  if (!tradingNow()) return false
  const m = quoteStaleMin(row)
  return m != null && m > 3
}

function coloredPct(pct: number | null | undefined, suffix = '%') {
  if (pct == null) return '-'
  const color = pct >= 0 ? 'var(--red)' : 'var(--green)'
  const text = pct >= 0 ? `+${pct.toFixed(2)}${suffix}` : `${pct.toFixed(2)}${suffix}`
  return h('span', { style: { color, fontWeight: 600 } }, text)
}

// ── 持仓在最热题材板块内的强弱名次(排名+总数+分位色) ──
// 分位 pos∈[0,1]: 0=板块内涨幅第一(最强), 1=垫底(最弱)。排序也用它(非持仓/无数据排末尾)。
function boardPos(row: Stock): number {
  if (row.status !== 'hold' || row.board_rank == null || !row.board_total || row.board_total < 1) return 2
  if (row.board_total === 1) return 0
  return (row.board_rank - 1) / (row.board_total - 1)
}
function boardTier(pos: number): { color: string; tag: string } {
  if (pos <= 1 / 3) return { color: 'var(--red)', tag: '强' }
  if (pos <= 2 / 3) return { color: '#f59e0b', tag: '中' }
  return { color: 'var(--green)', tag: '弱' }
}
function renderBoardStrength(row: Stock) {
  if (row.status !== 'hold') return h('span', { style: { color: '#ddd' } }, '')
  const rank = row.board_rank, total = row.board_total
  if (rank == null || !total) return h('span', { style: { color: 'var(--text2)', fontSize: '11px' } }, '-')
  const pos = boardPos(row)
  const { color, tag } = boardTier(pos)
  const frontPct = Math.round((rank / total) * 100)               // 涨幅领先位置: 越小越强
  const headTag = rank <= 3 ? '龙头' : pos >= 0.7 ? '脱队' : `${tag}势`
  const tip = `${row.board_name || '题材'} · 板块内涨幅第 ${rank}/${total} 名 (前 ${frontPct}%) — ${headTag}`
  // 分位条: 整条按强红→弱绿渐变, 一个游标标出本票位置(左强右弱)
  const bar = h('div', {
    style: {
      position: 'relative', width: '54px', height: '4px', borderRadius: '2px',
      background: 'linear-gradient(90deg, var(--red), #f59e0b, var(--green))', margin: '2px auto 0',
    },
  }, h('span', {
    style: {
      position: 'absolute', top: '-2px', left: `calc(${(pos * 100).toFixed(0)}% - 2px)`,
      width: '4px', height: '8px', borderRadius: '1px', background: '#1f2937',
      boxShadow: '0 0 0 1px #fff',
    },
  }))
  return h('div', { title: tip, style: { cursor: 'help', lineHeight: '1.2' } }, [
    h('div', { style: { fontWeight: 700, color, fontVariantNumeric: 'tabular-nums', fontSize: '12px' } },
      `${rank}/${total}`),
    bar,
  ])
}

function formatAmount(val: number | null | undefined) {
  if (val == null || val === 0) return '-'
  return formatYi(val)   // 成交额统一亿(2位), 见 utils/formatAmount
}

// 流通市值(流通金额): free_cap 存储单位=万元(流通股本×现价/1e4)。
// 兜底源偶发返回"元"(>1e9)时归一化到万元(与下方市值档同口径)。1亿元 = 1e4 万元。
function formatFreeCap(val: number | null | undefined) {
  if (val == null || val === 0) return '-'
  const wan = val > 1e9 ? val / 1e4 : val          // 元 → 万元 归一化
  if (wan >= 1e4) return (wan / 1e4).toFixed(0) + '亿'   // ≥1亿用"亿"
  return wan.toFixed(0) + '万'                       // 不足1亿用"万元"
}

// ── v1.7.176: 涨停/跌停 + 市值档 标签判定 (前端按已有字段派生, 无需新数据) ──

// 涨跌停板幅度: 按板块/ST 规则取阈值 (%)
function limitPct(row: Stock): number {
  const code = row.code || ''
  const name = row.name || ''
  if (/ST/i.test(name)) return 5                              // ST/*ST: ±5%
  if (/^(688|689)/.test(code)) return 20                      // 科创板: ±20%
  if (/^(300|301)/.test(code)) return 20                      // 创业板: ±20%
  if (/^(8|4|92)/.test(code)) return 30                       // 北交所: ±30%
  return 10                                                   // 主板: ±10%
}
function isLimitUp(row: Stock): boolean {
  return row.pct_change != null && row.pct_change >= limitPct(row) - 0.5
}
function isLimitDown(row: Stock): boolean {
  return row.pct_change != null && row.pct_change <= -(limitPct(row) - 0.5)
}

// 市值档: 基于流通市值 free_cap. 存储单位为万元;
// 防 EM 兜底源偶发返回"元"(>1e9 才可能), 归一化到万元后再分档
interface CapTier { label: string; bg: string; color: string; desc: string }
function numSorter(key: keyof Stock) {
  return (a: Stock, b: Stock) => ((a[key] as number) ?? 0) - ((b[key] as number) ?? 0)
}

function strSorter(key: keyof Stock) {
  return (a: Stock, b: Stock) => String(a[key] ?? '').localeCompare(String(b[key] ?? ''))
}

function sortOrder(key: string): false | 'ascend' | 'descend' {
  return sortState.value?.columnKey === key ? sortState.value.order as any : false
}

const allColumns = computed(() => [
  {
    type: 'expand' as const,
    expandable: (row: Stock) => signalsByCode.value.has(row.code),
    renderExpand,
    width: 0,
    className: 'col-expand-hidden',
  },
  {
    title: '',
    key: 'drag',
    width: 30,
    align: 'center' as const,
    render: (row: Stock) => {
      const canDrag = !sortState.value
      return h('span', {
        draggable: canDrag,
        style: {
          cursor: canDrag ? 'grab' : 'not-allowed',
          color: canDrag ? '#bbb' : '#e5e5e5',
          display: 'inline-flex',
        },
        title: canDrag ? '拖动调整顺序' : '点了列排序时不能拖, 先「重置排序」回到自定义顺序',
        onClick: (e: Event) => e.stopPropagation(),
        onDragstart: (e: DragEvent) => {
          dragCode.value = row.code
          if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
        },
        onDragend: () => { dragCode.value = null },
      }, h(NIcon, { size: 15 }, { default: () => h(ReorderThreeOutline) }))
    },
  },
  {
    title: '序号',
    key: 'index',
    width: 44,
    render: (_row: Stock, index: number) => h('span', { style: { color: 'var(--text2)', fontSize: '12px' } }, index + 1),
  },
  {
    title: '代码',
    key: 'code',
    width: 96,
    render: (row: Stock) => {
      const isFocused = !!row.focused
      const isHold = row.status === 'hold'
      const color = isHold ? '#4096ff' : isFocused ? '#e63946' : '#333'
      const fontWeight = isHold ? 700 : 'normal'
      const signals = signalsByCode.value.get(row.code)
      const codeSpan = h('span', {
        style: {
          fontFamily: 'monospace', color, fontWeight, cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', height: '16px', lineHeight: '1',
          textDecoration: 'underline', textDecorationStyle: 'dotted', textUnderlineOffset: '2px',
        },
        title: '点击查看分时 + 日K',
        onClick: (e: Event) => { e.stopPropagation(); openIntraday(row.code, row.name) },
      }, row.code)
      // v1.7.x: 买入/卖出信号分别独立气泡 — 红=买入数, 绿=卖出数; 都没就不显示
      const badges: any[] = []
      if (signals && signals.length) {
        const buyCount = signals.filter(s => s.direction === 'buy' || s.direction === 'add').length
        const sellCount = signals.filter(s => s.direction === 'sell' || s.direction === 'reduce').length
        const mkBadge = (n: number, bg: string, title: string) => h('span', {
          title,
          style: {
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: '16px', height: '16px', borderRadius: '50%',
            background: bg, color: 'white', fontSize: '10px', fontWeight: '700',
            cursor: 'pointer', flex: '0 0 auto',
          }
        }, String(n))
        if (buyCount > 0) badges.push(mkBadge(buyCount, '#ff3b00', `${buyCount} 个买入信号`))
        if (sellCount > 0) badges.push(mkBadge(sellCount, '#16a34a', `${sellCount} 个卖出/减仓信号`))
        // 兜底: 既无买也无卖, 但有"-"中性信号 (如 PLUNGE_*, SECTOR_CAPITAL_INFLOW), 灰气泡显总数
        if (badges.length === 0) {
          badges.push(mkBadge(signals.length, '#94a3b8', `${signals.length} 个提示信号`))
        }
      }
      return h('div', {
        style: { display: 'flex', alignItems: 'center', gap: '3px' }
      }, [codeSpan, ...badges])
    },
  },
  {
    title: '名称',
    key: 'name',
    width: 132,
    ellipsis: { tooltip: true },
    render: (row: Stock) => {
      const isFocused = !!row.focused
      const isHold = row.status === 'hold'
      const isTrade = isHold && row.hold_source === 'trade'
      const hasSignal = signalsByCode.value.has(row.code)
      const color = isHold ? '#4096ff' : isFocused ? '#e63946' : hasSignal ? '#d35400' : 'inherit'
      const fontWeight = isHold ? 700 : 'normal'
      const prefix = (isFocused && !isHold) ? '*' : ''
      const clickStyle = { cursor: 'pointer' }
      const onDblclick = (e: Event) => { e.stopPropagation(); openIntraday(row.code, row.name) }
      const children: any[] = []
      if (prefix) {
        children.push(h('span', { style: { position: 'absolute', right: '100%', marginRight: '2px' } }, prefix))
      }
      // 双榜共振火苗: 人气排名 与 成交额排名 同时进前100, 颜色随强弱(深红/橙/浅红)
      const popR = row.popularity_rank
      const amtR = amountRankMap.value[row.code]
      const resoLevel = resonanceLevel(popR, amtR)
      if (resoLevel) {
        const bg = resoLevel === '超强' ? '#cf222e' : resoLevel === '强' ? '#ea7a0c' : '#fb7185'
        children.push(h('span', {
          title: `双榜共振${resoLevel} — 人气第${popR} · 成交额第${amtR}名 (两榜均进前100)`,
          style: {
            marginRight: '4px', fontSize: '10px', padding: '0 3px',
            background: bg, borderRadius: '3px', lineHeight: '16px',
            verticalAlign: 'middle', boxShadow: '0 1px 2px rgba(0,0,0,0.18)',
          },
        }, '🔥'))
      }
      children.push(row.name)
      // 涨停 / 跌停: 实心高对比徽章, 一眼可辨 (按板块/ST 阈值判定)
      if (isLimitUp(row)) {
        children.push(h('span', {
          title: `涨停 (今日 +${row.pct_change!.toFixed(2)}%, 板幅 ${limitPct(row)}%)`,
          style: {
            marginLeft: '4px', fontSize: '10px', padding: '0 4px',
            background: 'var(--red)', color: '#fff', borderRadius: '2px',
            fontWeight: '700', lineHeight: '16px', verticalAlign: 'middle',
            boxShadow: '0 1px 2px rgba(207,34,46,0.35)',
          },
        }, '涨停'))
      } else if (isLimitDown(row)) {
        children.push(h('span', {
          title: `跌停 (今日 ${row.pct_change!.toFixed(2)}%, 板幅 ${limitPct(row)}%)`,
          style: {
            marginLeft: '4px', fontSize: '10px', padding: '0 4px',
            background: 'var(--green)', color: '#fff', borderRadius: '2px',
            fontWeight: '700', lineHeight: '16px', verticalAlign: 'middle',
            boxShadow: '0 1px 2px rgba(26,127,55,0.35)',
          },
        }, '跌停'))
      }
      // 连板: 连续涨停天数 — 游资核心标签, 橙红渐变实心徽章, 首板/N连板
      if (row.limit_up_days != null && row.limit_up_days >= 1) {
        const n = row.limit_up_days
        const label = n >= 2 ? `${n}连板` : '首板'
        children.push(h('span', {
          title: n >= 2 ? `连续涨停 ${n} 个交易日 (高标龙头, 情绪高度)` : '首板 (昨日/最近一个交易日涨停)',
          style: {
            marginLeft: '4px', fontSize: '10px', padding: '0 4px',
            background: n >= 2 ? 'linear-gradient(135deg, #ff6b00, #ff2d00)' : '#fff1f0',
            color: n >= 2 ? '#fff' : '#cf1322', borderRadius: '2px',
            fontWeight: '700', lineHeight: '16px', verticalAlign: 'middle',
            border: n >= 2 ? 'none' : '1px solid #ffa39e',
          },
        }, label))
      }
      // 小额: 今日虚拟全天成交额 < 20亿 (盘中按时点系数外推, 盘后用实际成交额)
      // 每 10 分钟独立评估一次, 避免边缘票随 3s quote 闪烁
      const smallEst = smallAmountMap.value.get(row.code)
      if (smallEst != null) {
        const tip = `今日预估全天成交额 ${(smallEst / 1e8).toFixed(2)}亿 (<20亿) · 每10分钟评估`
        children.push(h('span', {
          title: tip,
          style: {
            marginLeft: '4px', fontSize: '10px', padding: '0 3px',
            background: '#fef3c7', color: '#b45309', borderRadius: '2px',
            fontWeight: 'normal', lineHeight: '16px', verticalAlign: 'middle',
          },
        }, '小额'))
      }
      // 高换: turnover ≥ 15% — 短线情绪票 / 注意筹码松动
      if (row.turnover != null && row.turnover >= 15) {
        children.push(h('span', {
          title: `换手率 ${row.turnover.toFixed(2)}% (≥15% 高换, 短线情绪票, 注意筹码松动)`,
          style: {
            marginLeft: '4px', fontSize: '10px', padding: '0 3px',
            background: '#fee2e2', color: '#b91c1c', borderRadius: '2px',
            fontWeight: 'normal', lineHeight: '16px', verticalAlign: 'middle',
          },
        }, '高换'))
      }
      // 异动: volume_ratio > 3 — 突然放量 (相对前 5 日均量)
      if (row.volume_ratio != null && row.volume_ratio >= 3) {
        children.push(h('span', {
          title: `量比 ${row.volume_ratio.toFixed(2)}x (≥3x 突然放量, 主力进场或恐慌出货, 看方向)`,
          style: {
            marginLeft: '4px', fontSize: '10px', padding: '0 3px',
            background: '#dbeafe', color: '#1d4ed8', borderRadius: '2px',
            fontWeight: 'normal', lineHeight: '16px', verticalAlign: 'middle',
          },
        }, '异动'))
      }
      // verdict 色块: 有买点信号时给名称加色条提示决策结论 (与展开行决策卡颜色一致)
      const verdictColor = getVerdictColor(row)
      const wrapperStyle: Record<string, string | number> = {
        position: 'relative',
        color,
        fontWeight,
        ...clickStyle,
      }
      if (verdictColor) {
        wrapperStyle.borderLeft = `3px solid ${verdictColor}`
        wrapperStyle.paddingLeft = '5px'
        wrapperStyle.marginLeft = '-2px'
      }
      return h('span', { style: wrapperStyle, onDblclick, title: verdictColor ? '该票有买点信号 — 展开行查看完整决策依据' : undefined }, children)
    },
  },
  ...(props.showSparkline ? [{
    title: '走势',
    key: 'sparkline',
    width: 84,
    render: (row: Stock) => {
      const data = sparklineMap.value[row.code]
      if (!data || !data.trends || data.trends.length < 2) {
        return h('span', { style: { color: 'var(--text2)', fontSize: '11px' } }, '-')
      }
      return h(MiniSparkline, { trends: data.trends, preClose: data.pre_close, pct: row.pct_change, width: 80, height: 36 })
    },
  }] : []),
  {
    title: '人气',
    key: 'popularity_rank',
    width: 54,
    align: 'center' as const,
    sorter: numSorter('popularity_rank'),
    sortOrder: sortOrder('popularity_rank'),
    render: (row: Stock) => {
      const rank = row.popularity_rank
      if (rank == null) return '-'
      if (rank > 100) return h('span', { style: { color: 'var(--text3)', fontSize: '12px', whiteSpace: 'nowrap' } }, '100名外')
      const color = rank <= 20 ? 'var(--red)' : rank <= 50 ? '#f59e0b' : 'var(--text2)'
      const fontWeight = rank <= 20 ? 700 : 'normal'
      return h('span', { style: { color, fontWeight } }, `${rank}`)
    },
  },
  {
    title: '成交额排名',
    key: 'amount_rank',
    width: 84,
    align: 'center' as const,
    sorter: (a: Stock, b: Stock) => (amountRankMap.value[a.code] || 999) - (amountRankMap.value[b.code] || 999),
    render: (row: Stock) => {
      const r = amountRankMap.value[row.code]
      if (r && r <= 100) {
        const color = r <= 20 ? '#cf222e' : r <= 50 ? '#d9820a' : '#555'
        return h('b', { style: { color, fontVariantNumeric: 'tabular-nums' }, title: '全市场成交额第 ' + r + ' 名' }, String(r))
      }
      return h('span', { style: { color: '#bbb', fontSize: '11px' } }, '100名外')
    },
  },
  {
    title: '现价',
    key: 'price',
    width: 68,
    sorter: numSorter('price'),
    sortOrder: sortOrder('price'),
    render: (row: Stock) => h('span', { class: flashClass(row.code, 'price') }, row.price != null ? row.price.toFixed(2) : '-'),
  },
  {
    title: '涨幅',
    key: 'pct_change',
    width: 72,
    sorter: numSorter('pct_change'),
    sortOrder: sortOrder('pct_change'),
    render: (row: Stock) => {
      const fc = flashClass(row.code, 'pct_change')
      const pct = row.pct_change
      if (pct == null) return '-'
      const color = pct >= 0 ? 'var(--red)' : 'var(--green)'
      const text = pct >= 0 ? `+${pct.toFixed(2)}%` : `${pct.toFixed(2)}%`
      const stale = isQuoteStale(row)
      const main = h('span', { class: fc, style: { color, fontWeight: 600, opacity: stale ? 0.45 : 1 } }, text)
      if (!stale) return main
      return h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '3px' } }, [
        main,
        h('span', {
          style: { fontSize: '9px', color: '#f0a020', border: '1px solid #f0a020', borderRadius: '3px', padding: '0 2px', lineHeight: '1.35' },
          title: `行情滞后约 ${Math.round(quoteStaleMin(row) || 0)} 分钟，显示值可能不是最新`,
        }, '滞后'),
      ])
    },
  },
  {
    title: '5日涨幅',
    key: 'pct_5d',
    width: 78,
    sorter: numSorter('pct_5d'),
    sortOrder: sortOrder('pct_5d'),
    render: (row: Stock) => {
      const pct = row.pct_5d
      if (pct == null) return h('span', { style: { color: 'var(--text2)', fontSize: '11px' } }, '-')
      const color = pct >= 0 ? 'var(--red)' : 'var(--green)'
      const text = pct >= 0 ? `+${pct.toFixed(2)}%` : `${pct.toFixed(2)}%`
      return h('span', { style: { color, fontWeight: 600 } }, text)
    },
  },
  {
    title: '涨速',
    key: 'speed',
    width: 68,
    sorter: numSorter('speed'),
    sortOrder: sortOrder('speed'),
    render: (row: Stock) => {
      const fc = flashClass(row.code, 'speed')
      const spd = row.speed
      if (spd == null) return '-'
      const color = spd >= 0 ? 'var(--red)' : 'var(--green)'
      const text = spd >= 0 ? `+${spd.toFixed(2)}%` : `${spd.toFixed(2)}%`
      return h('span', { class: fc, style: { color, fontWeight: 600 } }, text)
    },
  },
  {
    title: '成交额',
    key: 'amount',
    width: 72,
    sorter: numSorter('amount'),
    sortOrder: sortOrder('amount'),
    render: (row: Stock) => formatAmount(row.amount),
  },
  {
    title: '量比',
    key: 'volume_ratio',
    width: 50,
    sorter: numSorter('volume_ratio'),
    sortOrder: sortOrder('volume_ratio'),
    render: (row: Stock) => row.volume_ratio != null ? row.volume_ratio.toFixed(2) : '-',
  },
  {
    title: '流通市值',
    key: 'free_cap',
    width: 78,
    sorter: numSorter('free_cap'),
    sortOrder: sortOrder('free_cap'),
    render: (row: Stock) => formatFreeCap(row.free_cap),
  },
  {
    title: '换手',
    key: 'turnover',
    width: 58,
    sorter: numSorter('turnover'),
    sortOrder: sortOrder('turnover'),
    render: (row: Stock) => row.turnover != null ? row.turnover.toFixed(2) + '%' : '-',
  },
  {
    title: '≥MA20',
    key: 'ma20',
    width: 52,
    render: (row: any) => {
      if (row.ma20 == null || row.price == null) return '-'
      return row.price >= row.ma20 ? '✓' : '✗'
    },
  },
  {
    title: '行业',
    key: 'industry',
    width: 80,
    ellipsis: { tooltip: true },
    sorter: strSorter('industry'),
    sortOrder: sortOrder('industry'),
    render: (row: Stock) => {
      if (!row.industry) return '-'
      const children: any[] = [h('span', {}, row.industry)]
      if (row.sector_rank === 1) {
        children.push(h('span', {
          style: {
            marginLeft: '3px',
            padding: '0 3px',
            fontSize: '9px',
            fontWeight: '700',
            lineHeight: '14px',
            borderRadius: '2px',
            background: 'linear-gradient(135deg, #ff6b00, #ff3b00)',
            color: 'white',
            verticalAlign: 'middle',
          },
        }, '最强'))
      }
      return h('span', { style: { whiteSpace: 'nowrap' } }, children)
    },
  },
  {
    title: '概念',
    key: 'concepts',
    width: 130,
    render: (row: Stock) => {
      const list = (row.concepts || '').split(',').map(s => s.trim()).filter(Boolean)
      if (!list.length) return h('span', { style: { color: 'var(--text2)' } }, '-')
      const chips = list.slice(0, 2).map(c => h('span', {
        style: {
          fontSize: '10px', padding: '0 4px', marginRight: '3px',
          background: '#f0f5ff', color: '#2f54eb', borderRadius: '2px',
          lineHeight: '16px', border: '1px solid #adc6ff', whiteSpace: 'nowrap',
        },
      }, c))
      if (list.length > 2) {
        chips.push(h('span', {
          style: { fontSize: '10px', color: 'var(--text2)' },
        }, `+${list.length - 2}`))
      }
      // 全部概念在 tooltip 里看全
      return h('span', {
        title: list.join(' / '),
        style: { display: 'inline-flex', alignItems: 'center', flexWrap: 'nowrap', cursor: 'help' },
      }, chips)
    },
  },
  {
    title: '板块内强弱',
    key: 'board_rank',
    width: 96,
    align: 'center' as const,
    sorter: (a: Stock, b: Stock) => boardPos(a) - boardPos(b),
    sortOrder: sortOrder('board_rank'),
    render: renderBoardStrength,
  },
  {
    title: '策略',
    key: 'strategy',
    width: 140,
    ellipsis: true,
    render: (row: Stock) => {
      const text = row.strategy?.trim() || ''
      const handler = (e: Event) => { e.stopPropagation(); openStrategyModal(row) }
      if (!text) {
        return h('span', {
          style: { color: 'var(--text2)', cursor: 'pointer', fontSize: '12px' },
          onClick: handler,
        }, '+ 添加策略')
      }
      const cell = h('span', {
        style: { cursor: 'pointer', fontSize: '12px', color: '#7c3aed', fontWeight: 500 },
        onClick: handler,
      }, [
        h('span', {}, text.length > 14 ? text.slice(0, 14) + '…' : text),
        h(NIcon, { size: 12, style: { marginLeft: '3px', verticalAlign: 'middle', opacity: 0.6 } }, { default: () => h(CreateOutline) }),
      ])
      // 悬浮富卡(功能A): 完整策略 + 目标/止损/仓位高亮; 点击仍进编辑
      return h(NPopover, { trigger: 'hover', placement: 'top-start', delay: 200, style: { maxWidth: '320px' } }, {
        trigger: () => cell,
        header: () => h('div', { style: { fontWeight: 600, fontSize: '12px', color: 'var(--text2)' } }, `${row.code} ${row.name} · 操作策略`),
        default: () => h(StrategyText, { text }),
      })
    },
  },
  {
    title: '操作',
    key: 'action',
    width: 200,
    fixed: 'right' as const,
    render: (row: Stock) => h(NSpace, { size: 4, wrap: false, align: 'center' }, () => [
      (() => {
        const sm = summaryFor(row.code)
        const hasTriggered = !!sm && sm.triggered > 0
        const hasActive = !!sm && sm.active > 0
        const title = sm
          ? `自定义预警: ${sm.active} 条生效${hasTriggered ? ` · ${sm.triggered} 条已触发` : ''}`
          : '设置自定义预警(价格/涨跌幅/接近均线)'
        return h(NButton, {
          size: 'tiny',
          quaternary: true,
          type: hasTriggered ? 'warning' : (hasActive ? 'primary' : 'default'),
          title,
          'aria-label': title,
          onClick: (e: Event) => { e.stopPropagation(); openAlertModal(row) },
        }, {
          icon: () => h(NIcon, { size: 15 }, { default: () => h(sm ? Notifications : NotificationsOutline) }),
        })
      })(),
      h(NButton, {
        size: 'tiny',
        quaternary: true,
        disabled: !!sortState.value,
        title: sortState.value ? '点了列排序时不可用, 先「重置排序」' : '置顶',
        'aria-label': '置顶',
        onClick: (e: Event) => { e.stopPropagation(); stockStore.moveToEdge(row.code, 'top') },
      }, {
        icon: () => h(NIcon, { size: 14 }, { default: () => h(ArrowUpOutline) }),
      }),
      h(NButton, {
        size: 'tiny',
        quaternary: true,
        disabled: !!sortState.value,
        title: sortState.value ? '点了列排序时不可用, 先「重置排序」' : '置底',
        'aria-label': '置底',
        onClick: (e: Event) => { e.stopPropagation(); stockStore.moveToEdge(row.code, 'bottom') },
      }, {
        icon: () => h(NIcon, { size: 14 }, { default: () => h(ArrowDownOutline) }),
      }),
      h(NButton, {
        size: 'tiny',
        type: 'warning',
        secondary: !row.focused,
        title: row.focused ? '取消关注' : '关注',
        'aria-label': row.focused ? '取消关注' : '关注',
        onClick: () => handleToggleFocus(row),
      }, {
        icon: () => h(NIcon, { size: 14 }, { default: () => h(row.focused ? Star : StarOutline) }),
      }),
      h(NButton, {
        size: 'tiny',
        type: row.status === 'hold' && row.hold_source === 'trade' ? 'info' : 'error',
        secondary: row.status !== 'hold',
        title: row.status === 'hold' ? '标记为观察' : '标记为持仓',
        'aria-label': row.status === 'hold' ? '标记为观察' : '标记为持仓',
        onClick: () => handleToggleHold(row),
      }, {
        icon: () => h(NIcon, { size: 14 }, { default: () => h(row.status === 'hold' ? Cart : CartOutline) }),
      }),
      h(NPopconfirm, {
        onPositiveClick: () => handleDelete(row.code, row.name),
        positiveText: '删除',
        negativeText: '取消',
      }, {
        trigger: () => h(NButton, {
          size: 'tiny',
          type: 'default',
          secondary: true,
          title: '删除',
          'aria-label': '删除',
        }, {
          icon: () => h(NIcon, { size: 14 }, { default: () => h(TrashOutline) }),
        }),
        default: () => row.status === 'hold'
          ? `${row.name} 在【持仓】状态，删除会丢失持仓成本/入仓日期，后续无法触发止损/止盈等信号。确认删除?`
          : `确认删除 ${row.name}（${row.code}）?`,
      }),
    ]),
  },
])

const columns = computed(() => allColumns.value)
</script>

<template>
  <div class="stock-table-wrap">
    <StrategyEditModal
      v-model:show="showStrategyModal"
      :code="strategyEditCode"
      :name="strategyEditName"
      :text="strategyEditText"
      @saved="onStrategySaved"
    />
    <StockAlertModal
      v-model:show="showAlertModal"
      :code="alertRow?.code || ''"
      :name="alertRow?.name || ''"
      @changed="reloadAlerts"
    />
    <NDataTable
      :columns="columns"
      :data="stocks"
      :bordered="false"
      size="small"
      :resizable-columns="true"
      :row-key="(row: Stock) => row.code"
      :scroll-x="1452"
      flex-height
      style="flex: 1; min-height: 0"
      :expanded-row-keys="expandedKeys"
      :row-props="rowProps"
      @update:sorter="handleSorterChange"
      @update:expanded-row-keys="handleExpandedKeysUpdate"
    />
  </div>
</template>

<style>
/* 让表格占满父级剩余高度, 配合 NDataTable flex-height: 仅表体内部滚动、表头吸顶不随页面动 */
.stock-table-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
@keyframes flash-up-bg {
  0% { background: rgba(255, 59, 48, 0.35); }
  100% { background: transparent; }
}
@keyframes flash-down-bg {
  0% { background: rgba(22, 163, 74, 0.35); }
  100% { background: transparent; }
}
.flash-up {
  animation: flash-up-bg 1.2s ease-out;
  font-variant-numeric: tabular-nums;
}
.flash-down {
  animation: flash-down-bg 1.2s ease-out;
  font-variant-numeric: tabular-nums;
}
/* 表体数字列等宽对齐, 防 3s 行情刷新数字跳动 */
.stock-table-wrap :deep(.n-data-table-td) {
  font-variant-numeric: tabular-nums;
}
.signal-expand {
  padding: 8px 12px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
}
@media (max-width: 900px) {
  .signal-expand {
    grid-template-columns: 1fr;
  }
}
.row-hold {
  background: rgba(32, 128, 240, 0.08);
}
.row-hold td {
  color: #4096ff !important;
  font-weight: 700 !important;
}
@keyframes signal-breathing {
  0%, 100% { background: transparent; }
  50% { background: rgba(255, 120, 0, 0.10); }
}
.row-signal-breathing {
  animation: signal-breathing 2.5s ease-in-out infinite;
  box-shadow: inset 4px 0 0 0 #ff6b00;
}
.row-clickable {
  cursor: pointer;
  touch-action: manipulation;
}
/* 隐藏 expand 列：单纯靠点击行触发展开，不显示图标 */
.stock-table-wrap :deep(.n-data-table-th--expandable),
.stock-table-wrap :deep(.n-data-table-td--expandable) {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.stock-table-wrap :deep(.col-expand-hidden) {
  width: 0 !important;
  min-width: 0 !important;
  max-width: 0 !important;
  padding: 0 !important;
  border-right: none !important;
  overflow: hidden;
}
.stock-table-wrap :deep(.col-expand-hidden > *) {
  display: none !important;
}
.stock-table-wrap .n-data-table-expand-trigger {
  display: none !important;
}
/* 大单异动面板 */
.big-orders {
  margin-top: 14px;
  border-top: 1px solid var(--border, #eee);
  padding-top: 10px;
}
.big-orders .bo-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.big-orders .bo-sub {
  font-size: 11px;
  font-weight: 400;
  color: var(--text2);
  margin-left: 4px;
}
.big-orders .bo-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-bottom: 10px;
}
.big-orders .bo-cell {
  background: var(--card2, #f7f8fa);
  border-radius: 6px;
  padding: 6px 10px;
  text-align: center;
}
.big-orders .bo-label {
  font-size: 11px;
  color: var(--text2);
  margin-bottom: 2px;
}
.big-orders .bo-val {
  font-size: 13px;
  font-weight: 600;
  font-family: monospace;
}
.big-orders .bo-list {
  max-height: 180px;
  overflow-y: auto;
}
.big-orders .bo-row {
  display: grid;
  grid-template-columns: 56px 28px 1fr 64px 72px;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-family: monospace;
  padding: 3px 0;
  border-bottom: 1px dashed var(--border, #f0f0f0);
}
.big-orders .bo-time {
  color: var(--text2);
}
.big-orders .bo-tag {
  text-align: center;
  border-radius: 3px;
  font-size: 11px;
}
.big-orders .bo-hands {
  text-align: right;
  color: var(--text2);
}
.big-orders .bo-amt {
  text-align: right;
  font-weight: 600;
}
.big-orders .up {
  color: #cf222e;
}
.big-orders .down {
  color: #1a7f37;
}
.big-orders .bo-tag.up {
  background: rgba(207, 34, 46, 0.1);
}
.big-orders .bo-tag.down {
  background: rgba(26, 127, 55, 0.1);
}
.big-orders .bo-empty {
  text-align: center;
  padding: 16px 0;
  color: var(--text2);
  font-size: 12px;
}
</style>
