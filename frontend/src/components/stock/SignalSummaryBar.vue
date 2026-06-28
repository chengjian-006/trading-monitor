<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import type { Signal } from '../../types'

const props = defineProps<{
  signalsByCode: Map<string, Signal[]>
}>()

const collapsed = ref(false)
const sectorExpanded = ref(false)
const SECTOR_DEFAULT_LIMIT = 6

onMounted(() => {
  const saved = localStorage.getItem('signal-summary-collapsed')
  if (saved === 'true') collapsed.value = true
})

function toggle() {
  collapsed.value = !collapsed.value
  localStorage.setItem('signal-summary-collapsed', String(collapsed.value))
}

function formatTime(dateStr: string) {
  if (!dateStr) return ''
  if (dateStr.includes(' ')) {
    return dateStr.split(' ')[1]?.slice(0, 5) || ''
  }
  if (/^\d{2}:\d{2}/.test(dateStr)) {
    return dateStr.slice(0, 5)
  }
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function tsOf(s: Signal): number {
  const raw = s.triggered_at || s.time || ''
  if (!raw) return 0
  const d = new Date(raw.includes(' ') ? raw.replace(' ', 'T') : raw)
  return isNaN(d.getTime()) ? 0 : d.getTime()
}

// 信号严重度数值 (越大越严重) — 用于个股排序 + 颜色映射
function severityLevel(s: Signal): number {
  if (s.direction === 'buy' || s.direction === 'add') return 1
  const name = s.signal_name || ''
  if (/浮亏-10|浮亏10|SELL_LOSS_10/.test(name)) return 9
  if (/浮亏-8|浮亏8|SELL_LOSS_8/.test(name)) return 8
  if (/急跌|大跌|PLUNGE/.test(name)) return 8
  if (/浮亏-5|浮亏5|SELL_LOSS_5/.test(name)) return 6
  if (/卖三|SS3/.test(name)) return 5
  if (/减仓|REDUCE|SR1/.test(name)) return 4
  if (/卖二|SS2/.test(name)) return 3
  if (/卖一|SS1/.test(name)) return 2
  if (/弱势极限|S0|WEAK_EXTREME/.test(name)) return 1
  return 1
}

function severityClass(s: Signal): string {
  const lv = severityLevel(s)
  if (s.direction === 'buy' || s.direction === 'add') return 'tag-buy'
  if (lv >= 8) return 'tag-sev-3'   // 浮亏-8/10 / 大跌
  if (lv >= 5) return 'tag-sev-2'   // 浮亏-5 / 卖三
  if (lv === 4) return 'tag-reduce'  // 减仓
  if (lv >= 2) return 'tag-sev-1'   // 卖一/二
  if (/弱势极限|S0|WEAK_EXTREME/.test(s.signal_name || '')) return 'tag-sev-info'
  return 'tag-sell'
}

function formatTimeShort(s: Signal): string {
  return formatTime(s.triggered_at || s.time || '')
}

function latestTs(signals: Signal[]): number {
  let mx = 0
  for (const s of signals) {
    const t = tsOf(s)
    if (t > mx) mx = t
  }
  return mx
}

function maxSeverity(signals: Signal[]): number {
  let m = 0
  for (const s of signals) {
    const lv = severityLevel(s)
    if (lv > m) m = lv
  }
  return m
}

interface Entry {
  code: string
  name: string
  signals: Signal[]  // 按时间 ASC 排
}

function splitGroup(isSector: boolean): Entry[] {
  const out: Entry[] = []
  for (const [code, signals] of props.signalsByCode) {
    const sector = code.startsWith('BK')
    if (sector !== isSector) continue
    const sorted = [...signals].sort((a, b) => tsOf(a) - tsOf(b))
    out.push({ code, name: sorted[sorted.length - 1]?.name || code, signals: sorted })
  }
  return out
}

// v1.7.x: 个股按"最高严重度 DESC + 最新时间 DESC"排, 让"必须立刻看"的浮在最上
const stockList = computed(() => {
  const list = splitGroup(false)
  list.sort((a, b) => {
    const sa = maxSeverity(a.signals)
    const sb = maxSeverity(b.signals)
    if (sb !== sa) return sb - sa
    return latestTs(b.signals) - latestTs(a.signals)
  })
  return list
})

// ── 板块卡片: 从 signal.detail 抽出龙头名, 按龙头去重合并 ──
interface SectorCard {
  key: string
  sectorNames: string[]     // 联名板块
  leaderName: string
  leaderPct: string         // 如 '+9.98%'
  latestTs: number
  latestTime: string
  rawCount: number          // 该卡聚合了多少条板块预警
}

// 从 detail 字符串里抽出龙头 — 格式: "龙头昊华能源+9.98% | 前10股均涨..."
function parseLeader(detail: string): { name: string; pct: string } {
  if (!detail) return { name: '', pct: '' }
  const m = detail.match(/龙头\s*([^\s+\-|]+)\s*([+\-]\d+(?:\.\d+)?%)/)
  return m ? { name: m[1], pct: m[2] } : { name: '', pct: '' }
}

const sectorCards = computed<SectorCard[]>(() => {
  const raw = splitGroup(true)
  if (!raw.length) return []

  // 每个板块取最新一条 signal 作代表
  type Item = { code: string; sectorName: string; latest: Signal; leaderName: string; leaderPct: string }
  const items: Item[] = raw.map(e => {
    const latest = e.signals[e.signals.length - 1]
    const { name, pct } = parseLeader(latest.detail || '')
    return { code: e.code, sectorName: e.name, latest, leaderName: name, leaderPct: pct }
  })

  // 按 leaderName 分组 (无龙头信息时用 code 独占一组)
  const buckets = new Map<string, Item[]>()
  for (const it of items) {
    const k = it.leaderName || it.code
    if (!buckets.has(k)) buckets.set(k, [])
    buckets.get(k)!.push(it)
  }

  const cards: SectorCard[] = []
  for (const [k, group] of buckets) {
    // 组内按时间 DESC, 第一条是最新
    group.sort((a, b) => tsOf(b.latest) - tsOf(a.latest))
    const head = group[0]
    cards.push({
      key: k,
      sectorNames: Array.from(new Set(group.map(g => g.sectorName))),
      leaderName: head.leaderName,
      leaderPct: head.leaderPct,
      latestTs: tsOf(head.latest),
      latestTime: formatTimeShort(head.latest),
      rawCount: group.length,
    })
  }
  // 卡片按最新时间 DESC
  cards.sort((a, b) => b.latestTs - a.latestTs)
  return cards
})

const visibleSectorCards = computed<SectorCard[]>(() =>
  sectorExpanded.value ? sectorCards.value : sectorCards.value.slice(0, SECTOR_DEFAULT_LIMIT)
)
const hiddenSectorCount = computed(() =>
  Math.max(0, sectorCards.value.length - SECTOR_DEFAULT_LIMIT)
)

// 板块卡颜色按"龙头涨幅"档分级
function sectorCardClass(c: SectorCard): string {
  const num = parseFloat(c.leaderPct)
  if (!isFinite(num)) return 'card-mid'
  if (num >= 9.5) return 'card-strong'  // 涨停级 → 深红边
  if (num >= 5) return 'card-mid'       // 中 → 橙边
  return 'card-soft'                    // 轻 → 灰边
}

const totalCodes = computed(() => props.signalsByCode.size)
const totalSignals = computed(() =>
  Array.from(props.signalsByCode.values()).reduce((n, arr) => n + arr.length, 0)
)
const stockSignalCount = computed(() => stockList.value.reduce((n, e) => n + e.signals.length, 0))
const sectorRawCount = computed(() =>
  sectorCards.value.reduce((n, c) => n + c.rawCount, 0)
)
</script>

<template>
  <div v-if="totalCodes > 0" class="signal-summary-bar">
    <div class="summary-header" role="button" tabindex="0"
      :aria-expanded="!collapsed" :aria-label="collapsed ? '展开今日预警' : '收起今日预警'"
      @click="toggle" @keydown.enter="toggle">
      <span class="summary-title">
        🔔 今日预警 ({{ totalCodes }}只/{{ totalSignals }}信号)
      </span>
      <span class="summary-toggle">{{ collapsed ? '展开 ▼' : '收起 ▲' }}</span>
    </div>

    <div v-if="!collapsed" class="summary-body">
      <!-- 个股行 -->
      <div v-if="stockList.length" class="row row-stocks">
        <span class="row-label row-label-stock">📈 个股 {{ stockList.length }}/{{ stockSignalCount }}</span>
        <div class="stocks-track">
          <span v-for="e in stockList" :key="e.code" class="stock-item">
            <span class="stock-name">{{ e.name }}</span>
            <span v-for="s in e.signals" :key="s.signal_name + s.triggered_at" class="sig-pair">
              <span class="sig-time">{{ formatTimeShort(s) }}</span>
              <span :class="['sig-tag', severityClass(s)]">{{ s.signal_name }}</span>
            </span>
          </span>
        </div>
      </div>

      <!-- 板块卡片网格 (按龙头去重 + 默认 Top 6) -->
      <div v-if="sectorCards.length" class="row row-sectors">
        <span class="row-label row-label-sector">
          📊 板块 {{ sectorCards.length }}{{ sectorCards.length < sectorRawCount ? ` (已合并自 ${sectorRawCount} 条)` : '' }}
        </span>
        <div class="sector-grid">
          <div v-for="c in visibleSectorCards" :key="c.key" :class="['sector-card', sectorCardClass(c)]">
            <div class="sc-head">
              <span class="sc-names" :title="c.sectorNames.join(' / ')">
                {{ c.sectorNames.length > 1 ? `${c.sectorNames[0]} +${c.sectorNames.length - 1}` : c.sectorNames[0] }}
              </span>
              <span class="sc-time">{{ c.latestTime }}</span>
            </div>
            <div class="sc-foot">
              <template v-if="c.leaderName">
                <span class="sc-leader">{{ c.leaderName }}</span>
                <span class="sc-pct">{{ c.leaderPct }}</span>
              </template>
              <span v-else class="sc-no-leader">资金回流</span>
            </div>
          </div>

          <button
            v-if="hiddenSectorCount > 0 && !sectorExpanded"
            class="sector-more"
            @click="sectorExpanded = true"
          >
            + {{ hiddenSectorCount }} 更多 ▾
          </button>
          <button
            v-else-if="sectorExpanded && sectorCards.length > SECTOR_DEFAULT_LIMIT"
            class="sector-more sector-collapse"
            @click="sectorExpanded = false"
          >
            收起 ▴
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.signal-summary-bar {
  background: #fffaf5;
  border: 1px solid rgba(255, 120, 0, 0.18);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 8px;
}
.summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  touch-action: manipulation;
}
.summary-title {
  font-size: 13px;
  font-weight: 600;
  color: #ff6b00;
}
.summary-toggle {
  font-size: 11px;
  color: #999;
}
.summary-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 8px;
}

/* ── 行布局 ── */
.row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.row-label {
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 10px;
  white-space: nowrap;
  margin-top: 3px;
}
.row-label-stock {
  background: rgba(217, 119, 6, 0.15);
  color: #b45309;
}
.row-label-sector {
  background: rgba(46, 128, 255, 0.12);
  color: #1d4ed8;
}

/* ── 个股行 ── */
.stocks-track {
  flex: 1 1 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: center;
  line-height: 1.7;
}
.stock-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  padding: 2px 8px 2px 6px;
  border-radius: 4px;
  background: #ffffff;
  border: 1px solid #fde0c4;
}
.stock-name {
  font-weight: 600;
  color: #b45309;
  margin-right: 2px;
  min-width: 0;
}
.sig-pair {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 2px;
}
.sig-time {
  font-family: monospace;
  font-size: 10px;
  color: #888;
  font-variant-numeric: tabular-nums;
}
.sig-tag {
  color: white;
  padding: 0 6px;
  border-radius: 3px;
  font-size: 10px;
  white-space: nowrap;
}
.sig-tag.tag-buy { background: #ff6b00; }
.sig-tag.tag-sell { background: #16a34a; }
.sig-tag.tag-sev-1 { background: #65a30d; }
.sig-tag.tag-sev-2 { background: #ea580c; }
.sig-tag.tag-sev-3 { background: #dc2626; }
.sig-tag.tag-sev-info { background: #2563eb; }
.sig-tag.tag-reduce { background: #eab308; color: #422006; }

/* ── 板块卡片网格 ── */
.sector-grid {
  flex: 1 1 auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 6px;
}
.sector-card {
  background: #fff;
  border-radius: 4px;
  padding: 5px 9px;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-left: 3px solid #94a3b8;
  border-top: 1px solid #e5e7eb;
  border-right: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
  transition: box-shadow 0.15s;
}
.sector-card:hover {
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
}
.sector-card.card-strong { border-left-color: #dc2626; background: linear-gradient(90deg, #fef2f2 0%, #fff 30%); }
.sector-card.card-mid    { border-left-color: #f59e0b; background: linear-gradient(90deg, #fffbeb 0%, #fff 30%); }
.sector-card.card-soft   { border-left-color: #94a3b8; }
.sc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.sc-names {
  font-weight: 600;
  color: #1f2937;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 auto;
  min-width: 0;
}
.sc-time {
  font-family: monospace;
  font-size: 10px;
  color: #9ca3af;
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
.sc-foot {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 11px;
}
.sc-leader {
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 auto;
  min-width: 0;
}
.sc-pct {
  font-family: monospace;
  font-weight: 700;
  color: #dc2626;
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
.sc-no-leader {
  font-size: 10px;
  color: #9ca3af;
  font-style: italic;
}

.sector-more {
  align-self: stretch;
  background: rgba(46, 128, 255, 0.06);
  border: 1px dashed rgba(46, 128, 255, 0.4);
  border-radius: 4px;
  font-size: 11px;
  color: #1d4ed8;
  cursor: pointer;
  font-weight: 500;
  letter-spacing: 0.5px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  touch-action: manipulation;
}
.sector-more:hover {
  background: rgba(46, 128, 255, 0.12);
}
.sector-more.sector-collapse {
  grid-column: 1 / -1;
  padding: 4px;
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .sector-grid {
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  }
  .sc-names { font-size: 11px; }
}
</style>
