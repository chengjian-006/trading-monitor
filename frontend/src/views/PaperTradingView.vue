<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, h } from 'vue'
import { NDataTable, NButton, NInputNumber, NPopconfirm, NTag, NInput, NSelect, NDatePicker, NIcon } from 'naive-ui'
import { RefreshOutline, SearchOutline } from '@vicons/ionicons5'
import { usePaperStore } from '../stores/paper-trading'
import { resetPaperAccount, updatePaperSettings, type AccountKey } from '../api/paper-trading'
import FilterPanel from '../components/common/FilterPanel.vue'
import { fetchKline } from '../api/kline'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import PaperEquityChart from '../components/common/PaperEquityChart.vue'

const store = usePaperStore()
const message = useGlobalMessage()
const initCap = ref(1000000)
const maxPos = ref(10)

const isUnlimited = computed(() => (store.summary?.unlimited_bullets ?? 0) === 1)

function syncSettingsFromSummary() {
  if (store.summary) { initCap.value = store.summary.initial_capital; maxPos.value = store.summary.max_positions }
}

// ── 持仓「建仓至今」日K收盘序列(画买入后价格折线图用) ──
const klineMap = ref<Record<string, { d: string; c: number }[]>>({})
async function loadHoldingKlines() {
  const codes = [...new Set(store.positions.map((p: any) => p.code))]
  await Promise.all(codes.map(async (code) => {
    try {
      const bars = await fetchKline(code, 120)
      klineMap.value[code] = bars.map((b: any) => ({ d: String(b.date), c: Number(b.close) }))
    } catch { /* 拉不到就回落两点直线 */ }
  }))
}

let liveTimer: number | undefined
onMounted(async () => {
  await store.loadAll()
  syncSettingsFromSummary()
  loadHoldingKlines()
  liveTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') store.refreshLive().catch(() => {})
  }, 60_000)
})
onUnmounted(() => { if (liveTimer) window.clearInterval(liveTimer) })

async function onSwitchAccount(key: AccountKey) {
  store.setAccount(key)
  klineMap.value = {}
  await store.loadAll()
  syncSettingsFromSummary()
  loadHoldingKlines()
}

async function onSaveSettings() {
  try { await updatePaperSettings(initCap.value, maxPos.value, store.accountKey); message.success('设置已保存(本金在下次重置生效)'); await store.loadAll() }
  catch (e: any) { message.error('保存失败: ' + (e?.message || e)) }
}
async function onReset() {
  try { await resetPaperAccount(initCap.value, maxPos.value, store.accountKey); message.success('已重置当前账户'); await store.loadAll(); loadHoldingKlines() }
  catch (e: any) { message.error('重置失败: ' + (e?.message || e)) }
}

// ── 格式化 ──
function fmtTime(raw?: string): string { return !raw ? '-' : raw.replace('T', ' ').slice(0, 19) }
function pnlColor(v?: number | null): string | undefined { return v == null ? undefined : (v >= 0 ? 'var(--up-fg)' : 'var(--down-fg)') }
function money0(v?: number | null): string { return v == null ? '—' : Math.round(v).toLocaleString() }
function moneySign(v?: number | null): string { return v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(v).toLocaleString() }
function pct2(v?: number | null): string { return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%' }

// ── 英雄区 / 概览 ──
const s = computed(() => store.summary)
const retUp = computed(() => (s.value?.total_return_pct ?? 0) >= 0)

// ── 仓位分布(现金 vs 持仓市值) ──
const alloc = computed(() => {
  const cash = Math.max(0, s.value?.cash ?? 0)
  const mv = Math.max(0, s.value?.holdings_mv ?? 0)
  const tot = cash + mv
  const holdPct = tot > 0 ? (mv / tot) * 100 : 0
  return { cash, mv, holdPct, cashPct: 100 - holdPct }
})

// ── 战绩 ──
const winRate = computed(() => s.value?.win_rate ?? null)
const gaugeDash = computed(() => {
  const C = 2 * Math.PI * 50
  const wr = winRate.value ?? 0
  return { c: C, off: C * (1 - wr / 100) }
})
const winLoss = computed(() => {
  const closed = s.value?.closed_trades ?? 0
  const wr = s.value?.win_rate ?? null
  if (!closed || wr == null) return { win: 0, lose: 0, closed }
  const win = Math.round(closed * wr / 100)
  return { win, lose: closed - win, closed }
})
const avgWinLoss = computed(() => {
  const sells = store.trades.filter((t: any) => t.side === 'sell' && t.realized_pnl_pct != null)
  const wins = sells.filter((t: any) => Number(t.realized_pnl_pct) >= 0).map((t: any) => Number(t.realized_pnl_pct))
  const loses = sells.filter((t: any) => Number(t.realized_pnl_pct) < 0).map((t: any) => Number(t.realized_pnl_pct))
  const avg = (a: number[]) => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null
  return { win: avg(wins), lose: avg(loses) }
})

// ── 英雄区权益迷你走势(取资金曲线 total_equity) ──
const equitySpark = computed(() => {
  const vals = store.equity.map((e: any) => Number(e.total_equity)).filter((v: number) => Number.isFinite(v))
  if (vals.length < 2) return null
  const min = Math.min(...vals), max = Math.max(...vals), rng = max - min || 1
  const W = 400, H = 74, pad = 8
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * W
    const y = H - pad - ((v - min) / rng) * (H - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const up = vals[vals.length - 1] >= vals[0]
  return { pts: pts.join(' '), area: `M0,${H} L${pts.join(' L')} L${W},${H} Z`, up, endX: W, endY: pts[pts.length - 1].split(',')[1] }
})

// ── 持仓卡 + 买入后价格折线图 ──
const holdingCards = computed(() => store.positions.map((p: any) => {
  const cost = Number(p.avg_cost) || 0
  const now = Number(p.price) || 0
  const od = String(p.open_date || '').slice(0, 10)
  const bars = klineMap.value[p.code] || []
  let series = bars.filter((b) => !od || b.d >= od).map((b) => b.c)
  if (series.length) series[series.length - 1] = now || series[series.length - 1]
  if (series.length < 2) series = [cost || now, now || cost]   // 回落两点直线
  const min = Math.min(...series, cost || series[0]), max = Math.max(...series, cost || series[0])
  const rng = max - min || 1
  const W = 200, H = 84, pad = 10
  const y = (v: number) => H - pad - ((v - min) / rng) * (H - pad * 2)
  const xs = series.map((_, i) => 2 + (i / (series.length - 1)) * (W - 4))
  const pts = series.map((v, i) => `${xs[i].toFixed(1)},${y(v).toFixed(1)}`)
  const baseY = y(cost || series[0])
  const up = now >= (cost || now)
  const area = `M${pts[0]} L${pts.slice(1).join(' L')} L${xs[xs.length - 1].toFixed(1)},${baseY.toFixed(1)} L2,${baseY.toFixed(1)} Z`
  const daysHeld = Number(p.days_held ?? 0)
  return {
    code: p.code, name: p.name, floatPct: p.float_pct, cost, now, qty: p.qty, mv: p.mv,
    model: p.entry_model_name || p.entry_signal_id || '—', daysHeld,
    up, poly: pts.join(' '), area, baseY: baseY.toFixed(1),
  }
}))

// ── 成交流水表 + 查询区 ──
const tradesView = computed(() => store.trades.map((t: any) => {
  if (t.side !== 'buy' || t.status === 'failed') return t
  const pos = store.positions.find((p: any) => p.code === t.code)
  const px = pos ? Number(pos.price) : 0
  const buyPx = Number(t.price)
  if (!pos || !(px > 0) || !(buyPx > 0)) return t
  return { ...t, _float_pnl: (px - buyPx) * Number(pos.qty), _float_pct: (px / buyPx - 1) * 100 }
}))
const fltKw = ref('')
const fltSide = ref<'buy' | 'sell' | null>(null)
const fltRange = ref<[number, number] | null>(null)
const sideOptions = [{ label: '全部方向', value: null as any }, { label: '买入', value: 'buy' }, { label: '卖出', value: 'sell' }]
function tsToDay(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const filteredTrades = computed(() => {
  const kw = fltKw.value.trim().toLowerCase()
  const side = fltSide.value
  const from = fltRange.value ? tsToDay(fltRange.value[0]) : null
  const to = fltRange.value ? tsToDay(fltRange.value[1]) : null
  return tradesView.value.filter((t: any) => {
    if (kw && !(String(t.code).toLowerCase().includes(kw) || String(t.name || '').toLowerCase().includes(kw))) return false
    if (side && t.side !== side) return false
    if (from || to) {
      const day = String(t.trade_time || '').slice(0, 10)
      if (from && day < from) return false
      if (to && day > to) return false
    }
    return true
  })
})
function resetTradeFilter() { fltKw.value = ''; fltSide.value = null; fltRange.value = null }

const tradeCols = [
  { title: '时间', key: 'trade_time', width: 156, render: (r: any) => fmtTime(r.trade_time) },
  { title: '方向', key: 'side', width: 60, render: (r: any) => h('span', { class: r.side === 'buy' ? 'sd-b' : 'sd-s' }, r.side === 'buy' ? '买' : '卖') },
  { title: '名称', key: 'name', width: 130, render: (r: any) => h('span', null, [h('b', null, r.name), h('span', { class: 'tl-code' }, ' ' + r.code)]) },
  { title: '触发信号', key: 'signal_name', width: 130, render: (r: any) => r.signal_name ? h('span', { class: r.side === 'buy' ? 'sig-b' : 'sig-s' }, r.signal_name) : '—' },
  { title: '状态', key: 'status', width: 64, render: (r: any) => r.status === 'failed' ? h('span', { style: { color: 'var(--danger-fg)' } }, '失败') : h('span', { style: { color: 'var(--success-fg)' } }, '成功') },
  { title: '股数', key: 'qty', width: 78, align: 'right' as const, render: (r: any) => r.status === 'failed' ? '—' : Number(r.qty).toLocaleString() },
  { title: '成交价', key: 'price', width: 80, align: 'right' as const },
  { title: '成交额', key: '_amount', width: 96, align: 'right' as const, render: (r: any) => (r.status !== 'failed' && r.qty != null && r.price != null) ? (Number(r.qty) * Number(r.price)).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—' },
  { title: '手续费', key: 'fee', width: 72, align: 'right' as const, render: (r: any) => r.status === 'failed' ? '—' : r.fee },
  {
    title: '盈亏', key: 'realized_pnl', width: 150, align: 'right' as const, render: (r: any) => {
      const isSell = r.side === 'sell' && r.realized_pnl != null
      const v = isSell ? Number(r.realized_pnl) : r._float_pnl
      if (v == null) return h('span', { style: { color: 'var(--fg-subtle)' } }, '—')
      const pct = isSell ? (r.realized_pnl_pct != null ? Number(r.realized_pnl_pct) : null) : r._float_pct
      return h('span', null, [
        h('span', { style: { color: pnlColor(v) } }, moneySign(v) + (pct != null ? ` ${pct2(pct)}` : '')),
        h('span', { style: { color: 'var(--fg-subtle)', marginLeft: '4px', fontSize: '11px' } }, isSell ? '实' : '浮'),
      ])
    },
  },
]
const modelCols = [
  { title: '买点模型', key: 'model' },
  { title: '笔数', key: 'n', align: 'right' as const },
  { title: '胜', key: 'win', align: 'right' as const },
  { title: '总盈亏', key: 'pnl', align: 'right' as const, render: (r: any) => h('span', { style: { color: pnlColor(Number(r.pnl)) } }, moneySign(Number(r.pnl))) },
  { title: '平均%', key: 'avg_pct', align: 'right' as const, render: (r: any) => h('span', { style: { color: pnlColor(Number(r.avg_pct)) } }, pct2(Number(r.avg_pct))) },
]
</script>

<template>
  <div class="pt-wrap">
    <!-- 顶栏: 账户切换 + 刷新提示 -->
    <div class="pt-topbar">
      <div class="pt-title">模拟账户<span class="pt-sub">纸面交易 · 严格执行模型信号验证</span></div>
      <div class="pt-tabs" role="tablist">
        <button role="tab" :aria-selected="store.accountKey === 'default'" :class="['pt-tab', { on: store.accountKey === 'default' }]" @click="onSwitchAccount('default')">
          <span class="dot std"></span>标准仓{{ s ? ' · ' + Math.round(s.initial_capital / 10000) + '万' : '' }}
        </button>
        <button role="tab" :aria-selected="store.accountKey === 'unlimited'" :class="['pt-tab', 'unl', { on: store.accountKey === 'unlimited' }]" @click="onSwitchAccount('unlimited')">
          <span class="dot fire"></span>无限子弹
        </button>
      </div>
      <span class="pt-spacer"></span>
      <span class="pt-badge" v-if="s">每笔 {{ s.buy_position_pct }}% · 真实费用 · 60s 刷新</span>
    </div>

    <template v-if="s">
      <!-- 英雄区 -->
      <div class="pnl hero">
        <div class="hero-l">
          <div class="hero-eyebrow">账户总资产 / TOTAL EQUITY</div>
          <div class="hero-eq">¥{{ money0(s.total_equity) }}</div>
          <div class="hero-ret">
            <span class="ret-chip" :class="retUp ? 'up' : 'down'">{{ retUp ? '▲' : '▼' }} {{ pct2(s.total_return_pct) }}</span>
            <span class="ret-abs" :style="{ color: pnlColor(s.total_pnl) }">{{ moneySign(s.total_pnl) }}</span>
          </div>
          <div class="hero-base">初始本金 <b>¥{{ money0(s.initial_capital) }}</b><span v-if="isUnlimited"> · 无限子弹(可透支/可加仓)</span></div>
        </div>
        <div class="hero-r">
          <div class="spark" v-if="equitySpark">
            <svg viewBox="0 0 400 74" preserveAspectRatio="none">
              <defs><linearGradient :id="'sg' + store.accountKey" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" :stop-color="equitySpark.up ? 'rgba(224,52,42,.20)' : 'rgba(18,160,107,.20)'" />
                <stop offset="1" :stop-color="equitySpark.up ? 'rgba(224,52,42,0)' : 'rgba(18,160,107,0)'" /></linearGradient></defs>
              <path :d="equitySpark.area" :fill="'url(#sg' + store.accountKey + ')'" />
              <polyline fill="none" :stroke="equitySpark.up ? 'var(--up-fg)' : 'var(--down-fg)'" stroke-width="1.8" :points="equitySpark.pts" />
              <circle :cx="equitySpark.endX" :cy="equitySpark.endY" r="3.4" :fill="equitySpark.up ? 'var(--up-fg)' : 'var(--down-fg)'" />
            </svg>
          </div>
          <div class="spark-empty" v-else>资金曲线积累中</div>
          <div class="mini3">
            <div class="mini"><div class="l">当日盈亏</div><div class="v" :style="{ color: pnlColor(s.today_pnl) }">{{ moneySign(s.today_pnl) }}</div></div>
            <div class="mini"><div class="l">现金</div><div class="v">{{ money0(s.cash) }}</div></div>
            <div class="mini"><div class="l">持仓市值</div><div class="v">{{ money0(s.holdings_mv) }}</div></div>
          </div>
        </div>
      </div>

      <!-- 资金曲线 + 战绩 -->
      <div class="row2">
        <div class="pnl">
          <div class="pnl-head"><span class="tt">资金曲线</span><span class="mlbl">EQUITY CURVE</span></div>
          <div class="pnl-body"><PaperEquityChart :data="store.equity" /></div>
        </div>
        <div class="pnl">
          <div class="pnl-head"><span class="tt">交易战绩</span><span class="mlbl">PERFORMANCE</span><span class="sp"></span><span class="pnl-meta">{{ s.closed_trades ?? 0 }} 笔已平仓</span></div>
          <div class="pnl-body stat-body">
            <div class="gauge-wrap">
              <div class="gauge">
                <svg viewBox="0 0 120 120">
                  <circle cx="60" cy="60" r="50" fill="none" stroke="var(--border-muted)" stroke-width="12" />
                  <circle cx="60" cy="60" r="50" fill="none" stroke="var(--up-fg)" stroke-width="12" stroke-linecap="round"
                    :stroke-dasharray="gaugeDash.c" :stroke-dashoffset="gaugeDash.off" transform="rotate(-90 60 60)" />
                </svg>
                <div class="glbl"><b>{{ winRate == null ? '—' : winRate + '%' }}</b><span>胜率</span></div>
              </div>
              <div class="wl">
                <div class="wl-row"><span>盈利 <b class="up">{{ winLoss.win }}</b> · 亏损 <b class="down">{{ winLoss.lose }}</b> 笔</span>
                  <div class="wl-bar"><i class="w" :style="{ width: (winLoss.closed ? winLoss.win / winLoss.closed * 100 : 0) + '%' }"></i><i class="l" :style="{ width: (winLoss.closed ? winLoss.lose / winLoss.closed * 100 : 0) + '%' }"></i></div>
                </div>
                <div class="wl-avg">均盈 <b class="up">{{ avgWinLoss.win == null ? '—' : pct2(avgWinLoss.win) }}</b> · 均亏 <b class="down">{{ avgWinLoss.lose == null ? '—' : pct2(avgWinLoss.lose) }}</b></div>
              </div>
            </div>
            <div class="kpi4">
              <div class="kc"><div class="l">盈亏因子 PF</div><div class="v">{{ s.profit_factor ?? '—' }}</div></div>
              <div class="kc"><div class="l">交易笔数</div><div class="v">{{ s.closed_trades ?? 0 }}</div></div>
              <div class="kc"><div class="l">持仓数</div><div class="v">{{ s.position_count }}<small>{{ isUnlimited ? ' /不限' : ' /' + s.max_positions }}</small></div></div>
              <div class="kc"><div class="l">最大回撤</div><div class="v down">{{ s.max_drawdown_pct == null ? '—' : '-' + Math.abs(s.max_drawdown_pct).toFixed(1) + '%' }}</div></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 仓位分布 + 持仓卡 -->
      <div class="row3">
        <div class="pnl">
          <div class="pnl-head"><span class="tt">仓位分布</span><span class="mlbl">ALLOCATION</span></div>
          <div class="pnl-body alloc-body">
            <svg class="donut" viewBox="0 0 42 42">
              <circle cx="21" cy="21" r="15.9" fill="none" stroke="var(--border-muted)" stroke-width="6" />
              <circle cx="21" cy="21" r="15.9" fill="none" stroke="var(--accent-fg)" stroke-width="6"
                :stroke-dasharray="alloc.holdPct.toFixed(1) + ' ' + (100 - alloc.holdPct).toFixed(1)" stroke-dashoffset="25" />
              <text x="21" y="20" text-anchor="middle" font-size="6" font-weight="700" font-family="var(--font-mono)" fill="var(--fg-default)">{{ alloc.holdPct.toFixed(0) }}%</text>
              <text x="21" y="26" text-anchor="middle" font-size="2.6" fill="var(--fg-subtle)">已投</text>
            </svg>
            <div class="alloc-leg">
              <div class="al-item"><span class="al-dot" style="background:var(--accent-fg)"></span><span class="nm">持仓市值</span><span class="vv">{{ money0(alloc.mv) }}</span><span class="pp">{{ alloc.holdPct.toFixed(0) }}%</span></div>
              <div class="al-item"><span class="al-dot" style="background:var(--border-muted)"></span><span class="nm">可用现金</span><span class="vv">{{ money0(alloc.cash) }}</span><span class="pp">{{ alloc.cashPct.toFixed(0) }}%</span></div>
              <div class="al-item foot"><span class="nm">持仓 {{ s.position_count }} 只{{ isUnlimited ? '' : ' / 上限 ' + s.max_positions + ' 只' }}</span></div>
            </div>
          </div>
        </div>
        <div class="pnl">
          <div class="pnl-head"><span class="tt">当前持仓</span><span class="mlbl">HOLDINGS · {{ store.positions.length }}</span><span class="sp"></span><span class="pnl-meta">浮盈随现价 60s 联动</span></div>
          <div class="pnl-body">
            <div v-if="!store.positions.length" class="empty">当前无持仓 · 等模型买点触发自动建仓</div>
            <div v-else class="hold-grid">
              <div v-for="hc in holdingCards" :key="hc.code" class="hc" :class="hc.up ? 'win' : 'lose'">
                <div class="hc-top"><span class="hc-nm">{{ hc.name }}</span><span class="hc-cd">{{ hc.code }}</span><span class="hc-pct" :style="{ color: pnlColor(hc.floatPct) }">{{ pct2(hc.floatPct) }}</span></div>
                <div class="hc-grid">
                  <span class="l">成本</span><span class="v">{{ hc.cost.toFixed(2) }}</span><span class="l">现价</span><span class="v" :style="{ color: pnlColor(hc.floatPct) }">{{ hc.now.toFixed(2) }}</span>
                  <span class="l">股数</span><span class="v">{{ Number(hc.qty).toLocaleString() }}</span><span class="l">市值</span><span class="v">{{ money0(hc.mv) }}</span>
                </div>
                <div class="hc-spark">
                  <svg viewBox="0 0 200 84" preserveAspectRatio="none">
                    <line x1="0" :y1="hc.baseY" x2="200" :y2="hc.baseY" stroke="var(--border-hard)" stroke-width="1" stroke-dasharray="3 3" />
                    <path :d="hc.area" :fill="hc.up ? 'rgba(224,52,42,.12)' : 'rgba(18,160,107,.12)'" />
                    <polyline fill="none" :stroke="hc.up ? 'var(--up-fg)' : 'var(--down-fg)'" stroke-width="1.8" :points="hc.poly" />
                  </svg>
                  <span class="cap">建仓 {{ hc.cost.toFixed(2) }}</span><span class="capr" :style="{ color: pnlColor(hc.floatPct) }">现价 {{ hc.now.toFixed(2) }}</span>
                </div>
                <div class="hc-foot">建仓买点 <b>{{ hc.model }}</b><span v-if="hc.daysHeld"> · 持有 {{ hc.daysHeld }} 日</span> · 买入后走势</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 运作机制(单行) -->
      <div class="pnl">
        <div class="flow-body">
          <span class="flow-title">运作机制</span>
          <div class="flow">
            <div class="fstep buy"><span class="ic">🎯</span><span class="ft">触发买点</span></div><span class="farrow">→</span>
            <div class="fstep buy"><span class="ic">🛒</span><span class="ft">等额买入</span></div><span class="farrow">→</span>
            <div class="fstep"><span class="ic">📈</span><span class="ft">持有盯盘</span></div><span class="farrow">→</span>
            <div class="fstep sell"><span class="ic">🏁</span><span class="ft">触发卖点</span></div><span class="farrow">→</span>
            <div class="fstep rot"><span class="ic">🔄</span><span class="ft">回笼轮动</span></div>
          </div>
          <span class="flow-cap">全自动 · 严格执行模型信号 · 真实费用 · 不掺主观</span>
        </div>
      </div>

      <!-- 按买点模型胜率 -->
      <div class="pnl" v-if="store.modelStats.length">
        <div class="pnl-head"><span class="tt">按买点模型 · 已实现胜率</span><span class="mlbl">BY MODEL</span></div>
        <div class="pnl-body"><NDataTable :columns="modelCols" :data="store.modelStats" :row-key="(r:any) => r.model" :bordered="false" size="small" /></div>
      </div>

      <!-- 成交流水 + 查询区 -->
      <div class="pnl">
        <div class="pnl-head"><span class="tt">成交流水</span><span class="mlbl">TRADE LOG</span><span class="sp"></span><span class="pnl-meta">{{ filteredTrades.length }} / {{ tradesView.length }} 笔</span></div>
        <div class="pnl-body">
          <FilterPanel>
          <div class="filter-bar">
            <div class="filter-fields">
              <div class="filter-item"><label>关键词</label><NInput v-model:value="fltKw" size="small" clearable placeholder="代码/名称" /></div>
              <div class="filter-item"><label>方向</label><NSelect v-model:value="fltSide" :options="sideOptions" size="small" clearable placeholder="全部方向" /></div>
              <div class="filter-item" style="min-width:220px"><label>日期段</label><NDatePicker v-model:value="fltRange" type="daterange" size="small" clearable format="yyyy-MM-dd" to="body" /></div>
            </div>
            <div class="filter-actions">
              <NButton size="small" type="primary" @click="resetTradeFilter"><template #icon><NIcon><RefreshOutline /></NIcon></template>重置</NButton>
              <NButton size="small" type="primary"><template #icon><NIcon><SearchOutline /></NIcon></template>查询</NButton>
            </div>
          </div>
          </FilterPanel>
          <NDataTable :columns="tradeCols" :data="filteredTrades" :row-key="(r:any) => r.id" :bordered="false" size="small" :scroll-x="1000" />
        </div>
      </div>

      <!-- 账户设置 -->
      <div class="pnl">
        <div class="pnl-head"><span class="tt">账户设置</span><span class="mlbl">SETTINGS</span></div>
        <div class="pnl-body">
          <div class="settings-row">
            <span>初始资金</span><NInputNumber v-model:value="initCap" :min="10000" :step="10000" style="width:160px" />
            <template v-if="!isUnlimited"><span>最大持仓数</span><NInputNumber v-model:value="maxPos" :min="1" :max="50" style="width:120px" /></template>
            <span v-else class="muted">无限子弹账户不限持仓数</span>
            <NButton size="small" @click="onSaveSettings">保存设置</NButton>
            <NPopconfirm @positive-click="onReset">
              <template #trigger><NButton size="small" type="warning">重置账户</NButton></template>
              确认用初始资金 {{ initCap }} 重置(清空持仓/流水/曲线)?
            </NPopconfirm>
          </div>
        </div>
      </div>
    </template>
    <div v-else class="empty" style="padding:40px">加载中…</div>
  </div>
</template>

<style scoped>
.pt-wrap { display: flex; flex-direction: column; gap: 12px; padding: 4px 0; }

/* 顶栏 */
.pt-topbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.pt-title { font-size: 16px; font-weight: 700; letter-spacing: .01em; }
.pt-sub { font-size: 11px; color: var(--fg-subtle); font-weight: 400; margin-left: 8px; }
.pt-tabs { display: inline-flex; background: var(--bg-sunken); border: 1px solid var(--border-default); border-radius: 8px; padding: 2px; }
.pt-tab { appearance: none; border: 0; background: transparent; font: inherit; font-size: 12.5px; font-weight: 600; color: var(--fg-muted); padding: 5px 14px; border-radius: 6px; cursor: pointer; display: inline-flex; align-items: center; gap: 7px; }
.pt-tab.on { background: var(--bg-surface); color: var(--accent-fg); box-shadow: 0 1px 3px rgba(0,0,0,.1); }
.pt-tab.unl.on { color: var(--warn-fg); }
.pt-tab .dot { width: 7px; height: 7px; border-radius: 50%; opacity: .4; }
.pt-tab .dot.std { background: var(--accent-fg); } .pt-tab .dot.fire { background: linear-gradient(135deg, var(--warn-fg), var(--up-fg)); }
.pt-tab.on .dot { opacity: 1; }
.pt-spacer { margin-left: auto; }
.pt-badge { font-size: 11px; color: var(--fg-muted); background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 6px; padding: 4px 10px; }

/* 面板通用 */
.pnl { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 6px; overflow: hidden; }
.pnl-head { height: 32px; background: var(--bg-head); border-bottom: 1px solid var(--border-default); display: flex; align-items: center; gap: 8px; padding: 0 12px; }
.pnl-head .tt { font-size: 12px; font-weight: 600; letter-spacing: .02em; }
.pnl-head .mlbl { font-size: 10px; color: var(--fg-subtle); text-transform: uppercase; letter-spacing: .06em; }
.pnl-head .sp { margin-left: auto; } .pnl-meta { font-size: 11px; color: var(--fg-subtle); }
.pnl-body { padding: 14px; }
.num, .v, .vv { font-variant-numeric: tabular-nums; }

/* 英雄区 */
.hero { display: grid; grid-template-columns: 1.1fr 1fr; }
.hero-l { padding: 16px 18px; border-right: 1px solid var(--border-muted); display: flex; flex-direction: column; justify-content: center; }
.hero-eyebrow { font-size: 10px; color: var(--fg-subtle); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 6px; }
.hero-eq { font-family: var(--font-mono); font-size: 38px; font-weight: 700; line-height: 1; letter-spacing: -.02em; }
.hero-ret { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.ret-chip { font-family: var(--font-mono); font-size: 15px; font-weight: 700; border-radius: 4px; padding: 3px 10px; }
.ret-chip.up { color: var(--up-fg); background: var(--up-bg-muted); } .ret-chip.down { color: var(--down-fg); background: var(--down-bg-muted); }
.ret-abs { font-family: var(--font-mono); font-size: 13px; }
.hero-base { font-size: 11px; color: var(--fg-subtle); margin-top: 8px; }
.hero-base b { color: var(--fg-muted); font-family: var(--font-mono); }
.hero-r { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; justify-content: center; }
.spark svg { width: 100%; height: 74px; display: block; } .spark-empty { height: 74px; display: flex; align-items: center; justify-content: center; color: var(--fg-subtle); font-size: 12px; }
.mini3 { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--border-muted); border-radius: 4px; overflow: hidden; }
.mini { padding: 7px 10px; border-right: 1px solid var(--border-muted); } .mini:last-child { border-right: none; }
.mini .l { font-size: 10px; color: var(--fg-subtle); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 3px; }
.mini .v { font-family: var(--font-mono); font-size: 16px; font-weight: 700; }

.row2 { display: grid; grid-template-columns: 1.7fr 1fr; gap: 12px; }
.row3 { display: grid; grid-template-columns: .8fr 2fr; gap: 12px; }

/* 战绩 */
.stat-body { display: flex; flex-direction: column; gap: 14px; }
.gauge-wrap { display: flex; align-items: center; gap: 16px; }
.gauge { position: relative; width: 116px; height: 116px; flex-shrink: 0; }
.gauge .glbl { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.gauge .glbl b { font-family: var(--font-mono); font-size: 25px; font-weight: 700; line-height: 1; }
.gauge .glbl span { font-size: 10px; color: var(--fg-subtle); letter-spacing: .05em; margin-top: 2px; }
.wl { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.wl-row { font-size: 11px; } .wl-avg { font-size: 11px; color: var(--fg-muted); }
.wl-bar { height: 8px; border-radius: 4px; background: var(--bg-sunken); overflow: hidden; display: flex; margin-top: 4px; }
.wl-bar .w { background: var(--up-fg); } .wl-bar .l { background: var(--down-fg); }
.kpi4 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px; background: var(--border-muted); border: 1px solid var(--border-muted); border-radius: 4px; overflow: hidden; }
.kc { background: var(--bg-surface); padding: 9px 11px; }
.kc .l { font-size: 10px; color: var(--fg-subtle); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 3px; }
.kc .v { font-family: var(--font-mono); font-size: 18px; font-weight: 700; } .kc .v small { font-size: 11px; color: var(--fg-muted); font-weight: 500; }

/* 仓位分布 */
.alloc-body { display: flex; align-items: center; gap: 16px; }
.donut { width: 120px; height: 120px; flex-shrink: 0; }
.alloc-leg { display: flex; flex-direction: column; gap: 9px; flex: 1; }
.al-item { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.al-dot { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
.al-item .nm { color: var(--fg-muted); } .al-item .vv { margin-left: auto; font-family: var(--font-mono); font-weight: 700; }
.al-item .pp { font-family: var(--font-mono); color: var(--fg-subtle); font-size: 11px; width: 40px; text-align: right; }
.al-item.foot { border-top: 1px solid var(--border-muted); padding-top: 8px; } .al-item.foot .nm { color: var(--fg-subtle); }

/* 持仓卡 */
.hold-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.hc { border: 1px solid var(--border-muted); border-radius: 5px; padding: 11px; position: relative; overflow: hidden; }
.hc::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; }
.hc.win::before { background: var(--up-fg); } .hc.lose::before { background: var(--down-fg); }
.hc-top { display: flex; align-items: baseline; gap: 6px; margin-bottom: 8px; }
.hc-nm { font-weight: 600; font-size: 13px; } .hc-cd { font-family: var(--font-mono); font-size: 11px; color: var(--fg-subtle); }
.hc-pct { margin-left: auto; font-family: var(--font-mono); font-weight: 700; }
.hc-grid { display: grid; grid-template-columns: auto 1fr auto 1fr; gap: 5px 8px; font-size: 11px; margin-bottom: 9px; }
.hc-grid .l { color: var(--fg-subtle); } .hc-grid .v { font-family: var(--font-mono); text-align: right; font-weight: 600; }
.hc-spark { position: relative; margin-top: 4px; } .hc-spark svg { width: 100%; height: 82px; display: block; }
.hc-spark .cap { position: absolute; top: 0; left: 0; font-size: 10px; color: var(--fg-subtle); }
.hc-spark .capr { position: absolute; top: 0; right: 0; font-size: 10px; }
.hc-foot { font-size: 10px; color: var(--fg-subtle); margin-top: 7px; } .hc-foot b { color: var(--tide-deep); }

/* 机制单行 */
.flow-body { padding: 9px 14px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flow-title { font-size: 12px; font-weight: 600; padding-right: 12px; border-right: 1px solid var(--border-muted); white-space: nowrap; }
.flow { display: flex; align-items: center; gap: 0; flex-wrap: wrap; }
.fstep { display: flex; align-items: center; gap: 6px; padding: 4px 9px; border: 1px solid var(--border-muted); border-radius: 6px; background: var(--bg-sunken); }
.fstep .ic { font-size: 13px; } .fstep .ft { font-size: 12px; font-weight: 600; }
.fstep.buy { background: var(--up-bg-muted); border-color: rgba(224,52,42,.2); }
.fstep.sell { background: var(--down-bg-muted); border-color: rgba(18,160,107,.2); }
.fstep.rot { background: var(--tide-bg-muted); border-color: rgba(12,140,166,.2); }
.farrow { color: var(--fg-subtle); font-size: 13px; padding: 0 6px; }
.flow-cap { font-size: 11px; color: var(--fg-subtle); margin-left: auto; }

/* 查询区(对齐 LogView) */
.filter-bar { display: grid; grid-template-columns: 1fr auto; gap: 12px 24px; align-items: end; margin-bottom: 12px; }
.filter-fields { display: flex; gap: 12px; flex-wrap: wrap; }
.filter-item { display: flex; flex-direction: column; gap: 4px; min-width: 130px; }
.filter-item label { font-size: 12px; color: var(--fg-muted); white-space: nowrap; }
.filter-actions { display: flex; gap: 8px; align-items: flex-end; }

/* 流水表内联 render 类 */
:deep(.sd-b) { color: var(--up-fg); font-weight: 700; } :deep(.sd-s) { color: var(--down-fg); font-weight: 700; }
:deep(.tl-code) { font-family: var(--font-mono); color: var(--fg-subtle); font-size: 11px; }
:deep(.sig-b) { font-size: 11px; color: var(--tide-deep); background: var(--tide-bg-muted); border-radius: 3px; padding: 1px 6px; }
:deep(.sig-s) { font-size: 11px; color: var(--warn-fg); background: var(--warn-bg-muted); border-radius: 3px; padding: 1px 6px; }

.settings-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.muted { color: var(--fg-subtle); }
.up { color: var(--up-fg); } .down { color: var(--down-fg); }
.empty { text-align: center; color: var(--fg-subtle); font-size: 13px; padding: 24px; }

@media (max-width: 960px) {
  .hero, .row2, .row3 { grid-template-columns: 1fr; }
  .hero-l { border-right: none; border-bottom: 1px solid var(--border-muted); }
}
@media (max-width: 768px) {
  .pt-tabs { width: 100%; } .pt-tab { flex: 1; justify-content: center; }
  .filter-bar { grid-template-columns: 1fr; } .filter-fields { flex-direction: column; }
  .filter-item { min-width: 0; }
}
</style>
