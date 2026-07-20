<script setup lang="ts">
import { computed } from 'vue'
import { NPopover } from 'naive-ui'
import type { Signal } from '../../types'

// v1.7.725: 从"页面顶部整条横幅"改成"状态行上的铃铛角标 + 点开浮层"。
// 原因: 原横幅高度固定(标题独占一行 + 个股行/板块行各带一个标签列), 与内容多少无关 ——
// 只有 2 条预警时也占满一大条, 右侧大片空白。改成角标后占地恒定极小, 详情按需展开。
// 顺带去掉了原来的折叠状态(collapsed/localStorage)与板块 Top6 截断(浮层里直接列全部, 不用再"+N 更多")。

const props = defineProps<{
  signalsByCode: Map<string, Signal[]>
}>()

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

// v1.7.725: 原有 visibleSectorCards / hiddenSectorCount(板块默认只显 Top6 + "+N 更多")已删 ——
// 改成浮层展示后不再受横幅宽度约束, 浮层内直接列全部板块, 不需要截断再展开。

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
  <!-- v1.7.725: 整条横幅 → 铃铛角标 + 点开浮层。占地从"固定一大条"变成一枚角标。 -->
  <NPopover v-if="totalCodes > 0" trigger="click" placement="bottom-start" :width="400" raw>
    <template #trigger>
      <button class="alert-bell" :aria-label="`今日预警 ${totalCodes}只 ${totalSignals}信号，点击查看`">
        <span class="ab-ic">🔔</span>
        <span class="ab-count">{{ totalSignals }}</span>
      </button>
    </template>

    <div class="alert-pop">
      <div class="ap-head">今日预警 <b>{{ totalCodes }}</b> 只 / <b>{{ totalSignals }}</b> 信号</div>

      <!-- 个股 -->
      <div v-if="stockList.length" class="ap-sec">
        <div class="ap-label">📈 个股 {{ stockList.length }}/{{ stockSignalCount }}</div>
        <div v-for="e in stockList" :key="e.code" class="ap-row">
          <span class="ap-name">{{ e.name }}</span>
          <span class="ap-sigs">
            <span v-for="s in e.signals" :key="s.signal_name + s.triggered_at" class="ap-sig">
              <span class="ap-time">{{ formatTimeShort(s) }}</span>
              <span :class="['sig-tag', severityClass(s)]">{{ s.signal_name }}</span>
            </span>
          </span>
        </div>
      </div>

      <!-- 板块 (按龙头去重; 浮层内列全部, 不再截断) -->
      <div v-if="sectorCards.length" class="ap-sec">
        <div class="ap-label">
          📊 板块 {{ sectorCards.length }}{{ sectorCards.length < sectorRawCount ? ` (已合并自 ${sectorRawCount} 条)` : '' }}
        </div>
        <div v-for="c in sectorCards" :key="c.key" :class="['ap-row', 'ap-sector', sectorCardClass(c)]">
          <span class="ap-name" :title="c.sectorNames.join(' / ')">
            {{ c.sectorNames.length > 1 ? `${c.sectorNames[0]} +${c.sectorNames.length - 1}` : c.sectorNames[0] }}
          </span>
          <span class="ap-mid">
            <template v-if="c.leaderName">{{ c.leaderName }}</template>
            <template v-else>资金回流</template>
          </span>
          <span v-if="c.leaderName" class="ap-pct">{{ c.leaderPct }}</span>
          <span class="ap-time">{{ c.latestTime }}</span>
        </div>
      </div>
    </div>
  </NPopover>
</template>

<style scoped>
/* ── 铃铛角标 (v1.7.725) ──
   原来是页面顶部一整条横幅, 高度固定与内容量无关: 只有 2 条预警时也占满一大条、右侧大片空白。
   改成角标后占地恒定极小, 详情走浮层按需展开。 */
.alert-bell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px 2px 6px;
  border: 1px solid var(--warn-line, var(--border-default));
  border-radius: 999px;
  background: var(--warn-bg-muted);
  color: var(--warn-fg);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.6;
  cursor: pointer;
  white-space: nowrap;
  transition: filter .15s, transform .12s;
}
.alert-bell:hover { filter: brightness(1.06); transform: translateY(-1px); }
.alert-bell:active { transform: none; }
.ab-ic { font-size: 13px; }
.ab-count {
  min-width: 16px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

/* ── 浮层 ── */
.alert-pop {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  box-shadow: var(--shadow-float, 0 8px 28px rgba(0, 0, 0, .14));
  padding: 10px 12px 12px;
  max-height: 60vh;
  overflow-y: auto;
}
.ap-head {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--fg-default);
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--border-muted);
}
.ap-head b { color: var(--warn-fg); }
.ap-sec + .ap-sec { margin-top: 12px; }
.ap-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--fg-muted);
  letter-spacing: .03em;
  margin-bottom: 5px;
}
.ap-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 5px;
  font-size: 12px;
  line-height: 1.5;
}
.ap-row:hover { background: var(--bg-sunken, rgba(0, 0, 0, .03)); }
.ap-name {
  font-weight: 600;
  color: var(--fg-default);
  flex-shrink: 0;
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ap-sigs { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; min-width: 0; }
.ap-sig { display: inline-flex; align-items: center; gap: 3px; }
.ap-mid { color: var(--fg-muted); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ap-pct { font-weight: 700; color: var(--up-fg); font-variant-numeric: tabular-nums; flex-shrink: 0; }
.ap-time {
  margin-left: auto;
  color: var(--fg-subtle);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

/* 板块行按龙头涨幅分强弱, 沿用原卡片的三档语义(左侧色条替代原来的整卡描边) */
.ap-sector { border-left: 3px solid transparent; padding-left: 7px; }
.ap-sector.card-strong { border-left-color: var(--up-fg); }
.ap-sector.card-mid    { border-left-color: var(--warn-fg); }
.ap-sector.card-soft   { border-left-color: var(--fg-subtle); }

/* 信号标签配色沿用原横幅口径, 未改 */
.sig-tag {
  color: var(--on-emphasis);
  padding: 0 6px;
  border-radius: 3px;
  font-size: 10px;
  white-space: nowrap;
}
.sig-tag.tag-buy { background: var(--up-fg); }
.sig-tag.tag-sell { background: var(--down-fg); }
.sig-tag.tag-sev-1 { background: var(--down-fg); }
.sig-tag.tag-sev-2 { background: var(--warn-fg); }
.sig-tag.tag-sev-3 { background: var(--danger-fg); }
.sig-tag.tag-sev-info { background: var(--accent-fg); }
.sig-tag.tag-reduce { background: var(--warn-bg-muted); color: var(--warn-fg); }

@media (max-width: 768px) {
  .alert-pop { max-height: 55vh; }
  .ap-name { max-width: 72px; }
}
</style>
