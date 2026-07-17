<script setup lang="ts">
// 市场情绪温度表 — 日期×题材 涨停家数矩阵 + 强势/热点板块提炼 (v1.7.x)
// 矩阵: 各交易日各题材涨停家数, 颜色深浅=涨停数, 看主线兴起/高潮/退潮。
// 提炼: 从矩阵算每题材的 热度分(时间衰减加权)+趋势(升温/持续/退潮)+代表股, 排出"当前主线榜"
//       + 详细榜, 并联动标记自选里踩在主线上的票。数据由 refresh_theme_heat 每5分钟累积。
import { ref, computed } from 'vue'
import { NSkeleton, NButton, NIcon } from 'naive-ui'
import { FlameOutline, RefreshOutline } from '@vicons/ionicons5'
import { fetchThemeHeat, type ThemeHeatData, type ThemeHeatSub } from '../../api/market-report'
import { useStockStore } from '../../stores/stock'
import { useUiStore } from '../../stores/ui'
import { useVisiblePolling } from '../../composables/useVisiblePolling'

const stockStore = useStockStore()
const ui = useUiStore()

const data = ref<ThemeHeatData | null>(null)
const loading = ref(false)
const loaded = ref(false)
const selectedTheme = ref<string | null>(null)
const activeCell = ref<{ d: string; theme: string } | null>(null)
const expanded = ref(false)                                       // 矩阵是否展开>10天的历史

const MAX_COLS = 12
const DEFAULT_ROWS = 10                                           // 默认只铺最近10个交易日, 余下折叠在「更多」

const dates = computed(() => data.value?.dates ?? [])
const chrono = computed(() => [...dates.value].sort())            // 日期正序(旧→新)
// 默认仅取最近10行(slice(-10), 保持旧→新顺序最新在底部); 点「更多」展开后铺全部
const displayDates = computed(() =>
  !expanded.value && chrono.value.length > DEFAULT_ROWS ? chrono.value.slice(-DEFAULT_ROWS) : chrono.value
)
const hiddenCount = computed(() => Math.max(0, chrono.value.length - DEFAULT_ROWS))
const cols = computed(() => (data.value?.themes ?? []).slice(0, MAX_COLS))

function cell(d: string, theme: string) {
  return data.value?.cells?.[d]?.[theme]
}
// 当日涨停总数: 行内各大类(含「其他」)相加。每只涨停股按涨停原因首标签只入一个细题材→唯一大类,
// 故相加=当日真实涨停总数, 不重复计数。遍历整行 cells(不止显示的列)保证即使列被截断也是真总数。
function rowTotal(d: string): number {
  const row = data.value?.cells?.[d]
  if (!row) return 0
  let s = 0
  for (const k in row) s += row[k]?.c || 0
  return s
}
// 表头窗口合计: 各大类窗口总板数之和 = 窗口内涨停总数
const grandTotal = computed(() => (data.value?.themes ?? []).reduce((a, t) => a + (t.total || 0), 0))
function fmtDate(d: string) {
  return d.length >= 8 ? `${d.slice(4, 6)}-${d.slice(6, 8)}` : d
}
function cellStyle(c: number | undefined, theme?: string) {
  if (!c) return {}
  // 「其他」是长尾杂烩, 数值天然偏大, 不参与主线热度对比 → 走淡色中性档, 避免抢眼
  if (theme === '其他') {
    return { background: 'color-mix(in srgb, var(--up-fg) 6%, transparent)', color: 'var(--fg-muted)' }
  }
  const mag = Math.min(c / 15, 1)
  // 热度渐变: 以 --up-fg 打底, 用 color-mix 透明度分档(浅14%→实底100%), 保留浅红→深红的涨停强度层次
  const pct = 14 + mag * 86
  return { background: `color-mix(in srgb, var(--up-fg) ${pct}%, transparent)`, color: mag > 0.55 ? 'var(--on-emphasis)' : 'var(--up-fg)' }
}
function cellTitle(d: string, theme: string) {
  const x = cell(d, theme)
  if (!x) return ''
  return `${fmtDate(d)} ${theme} ${x.c}只涨停${x.s ? '\n' + x.s : ''}`
}

// ── 强势/热点板块提炼 ──
interface ThemeStat {
  name: string; total: number; days_on: number; score: number
  trend: 'up' | 'down' | 'flat'; label: string; rep: string; members: string[]
}
const themeStats = computed<ThemeStat[]>(() => {
  const ch = chrono.value
  const n = ch.length
  return (data.value?.themes ?? []).filter(t => t.name !== '其他').map(t => {   // 「其他」是长尾杂烩, 不作主线/操作
    const series = ch.map(d => cell(d, t.name)?.c || 0)
    let score = 0
    series.forEach((c, i) => { score += c * Math.pow(0.8, (n - 1 - i)) })  // 越近权重越高
    const last3 = series.slice(-3).reduce((a, b) => a + b, 0)
    const prev3 = series.slice(-6, -3).reduce((a, b) => a + b, 0)
    let trend: 'up' | 'down' | 'flat' = 'flat'
    if (prev3 === 0 && last3 > 0) trend = 'up'
    else if (last3 > prev3 * 1.3) trend = 'up'
    else if (last3 < prev3 * 0.6) trend = 'down'
    const fresh = series.slice(-2).reduce((a, b) => a + b, 0) > 0
    const daysOn = t.days_on ?? series.filter(c => c > 0).length
    const total = t.total || 0
    let label = '♨️持续'
    if (trend === 'down' && total >= 3) label = '🌊退潮'
    else if (daysOn <= 3 && fresh) label = '🌱新起'
    else if (trend === 'up') label = '🔥升温'
    let rep = ''
    for (let i = ch.length - 1; i >= 0; i--) { const x = cell(ch[i], t.name); if (x?.s) { rep = x.s; break } }
    return { name: t.name, total, days_on: daysOn, score, trend, label, rep, members: t.members ?? [t.name] }
  }).sort((a, b) => b.score - a.score)
})
// 大类名(如"PCB·覆铜板")不会出现在个股 concepts 串里, 故按成员关键词命中任一即算踩线
function hitsConcepts(t: ThemeStat, concepts: string): boolean {
  const terms = t.members.length ? t.members : [t.name]
  return terms.some(m => concepts.includes(m))
}
const mainline = computed(() => themeStats.value.slice(0, 8))       // A: 当前主线榜

// B(方向B): 「给我的操作」—— 以我的自选票为主体, 按它最热的那条主线 × 持仓/观察 推出动作
interface OpItem { code: string; name: string; pct: number | null; theme: string; label: string; action: string }
interface RefillItem { theme: string; label: string; rep: string }
const opGroups = computed(() => {
  const reduce: OpItem[] = []   // 🔴 退潮 + 持仓 → 减/止盈
  const buy: OpItem[] = []      // 🟢 升温/新起/持续 + 观察 → 低吸/关注
  const hold: OpItem[] = []     // 🔵 升温/持续 + 持仓 → 持有
  const avoid: OpItem[] = []    // ⚠️ 退潮 + 观察 → 别追(置底弱化)
  const stats = themeStats.value                                   // 已按热度分降序
  const seen = new Set<string>()
  for (const s of stockStore.stocks) {
    const concepts = s.concepts || ''
    if (!concepts || seen.has(s.code)) continue
    const t = stats.find(st => hitsConcepts(st, concepts))         // 取该票最热的主线(成员关键词命中)
    if (!t) continue
    seen.add(s.code)
    const isEbb = t.trend === 'down' || t.label.includes('退潮')
    const isFresh = t.label.includes('升温') || t.label.includes('新起')
    const isHold = s.status === 'hold'
    const base = { code: s.code, name: s.name, pct: s.pct_change ?? null, theme: t.name, label: t.label }
    if (isHold && isEbb) reduce.push({ ...base, action: '减仓/止盈' })
    else if (isHold) hold.push({ ...base, action: '沿强势线持有' })
    else if (isEbb) avoid.push({ ...base, action: '退潮·别追' })
    else buy.push({ ...base, action: isFresh ? '等回踩低吸' : '关注' })
  }
  // ⚪ 补票候选: 热度前6强主线(非退潮) 且 池中完全无票 → 列代表股
  const refill: RefillItem[] = stats.slice(0, 6)
    .filter(t => !(t.trend === 'down' || t.label.includes('退潮')))
    .filter(t => !stockStore.stocks.some(s => hitsConcepts(t, s.concepts || '')))
    .map(t => ({ theme: t.name, label: t.label, rep: t.rep }))
  return { reduce, buy, hold, avoid, refill }
})
const hasOps = computed(() => {
  const g = opGroups.value
  return !!(g.reduce.length || g.buy.length || g.hold.length || g.avoid.length || g.refill.length)
})

// 右栏每组限量 + 折叠: 防止「给我的操作」列表无界拉长把版面顶乱(改内容不动坏布局)
const OP_LIMIT = 6
const opExpanded = ref<Record<string, boolean>>({})
function opShown<T>(key: string, list: T[]): T[] {
  return opExpanded.value[key] ? list : list.slice(0, OP_LIMIT)
}
function opMoreLabel(key: string, total: number): string {
  return opExpanded.value[key] ? '收起 ↑' : `展开更多 · 还有 ${total - OP_LIMIT} ↓`
}
function toggleOp(key: string) { opExpanded.value[key] = !opExpanded.value[key] }

const trendArrow = (t: string) => (t === 'up' ? '↑' : t === 'down' ? '↓' : '→')
// 涨红跌绿: 走设计 Token —— 涨/热=var(--up-fg) / 跌/退潮=var(--down-fg) / 中性=var(--fg-subtle)
const trendColor = (t: string) => (t === 'up' ? 'var(--up-fg)' : t === 'down' ? 'var(--down-fg)' : 'var(--fg-subtle)')

// 联动: 点题材高亮矩阵列
function pickTheme(name: string) {
  selectedTheme.value = selectedTheme.value === name ? null : name
}

// 点击矩阵单元格 → 展开该日该题材的涨停个股(代表股)
function onCellClick(d: string, theme: string) {
  if (!cell(d, theme)?.c) return                                  // 空格不可点
  const cur = activeCell.value
  activeCell.value = cur && cur.d === d && cur.theme === theme ? null : { d, theme }
}
function isActiveCell(d: string, theme: string) {
  const a = activeCell.value
  return !!a && a.d === d && a.theme === theme
}
// 个股名命中自选则可点开详情, 其余纯文本
function stocksOf(s: string): { name: string; code?: string }[] {
  return (s || '').split(/[,，]/).map(n => n.trim()).filter(Boolean).map(name => {
    const hit = stockStore.stocks.find(x => x.name === name)
    return hit ? { name, code: hit.code } : { name }
  })
}
// 下钻: 点大类格子 → 展开内部细题材分布(覆铜板 3 / PCB 4 / 电子布 1 …), 无 sub 时回退为整格样本
const activeCellSubs = computed<ThemeHeatSub[]>(() => {
  const a = activeCell.value
  if (!a) return []
  const c = cell(a.d, a.theme)
  if (!c) return []
  if (c.sub && c.sub.length) return c.sub
  return [{ theme: a.theme, c: c.c, s: c.s }]
})

async function load() {
  loading.value = true
  try {
    data.value = await fetchThemeHeat(40)
    const a = activeCell.value                                    // 刷新后若选中格已无数据则清空
    if (a && !cell(a.d, a.theme)?.c) activeCell.value = null
  } finally {
    loading.value = false
    loaded.value = true
  }
}

useVisiblePolling(load, 180_000)   // 切走标签页暂停, 切回立即补刷
</script>

<template>
  <div class="theme-heat">
    <div class="head">
      <div class="title">
        <NIcon :component="FlameOutline" :size="16" />
        <span>市场情绪温度表</span>
        <span class="meta">题材涨停热度 · 颜色越深涨停越多<template v-if="dates.length"> · 近{{ dates.length }}日</template></span>
      </div>
      <NButton quaternary circle size="tiny" :loading="loading" title="刷新" aria-label="刷新" @click="load">
        <template #icon><NIcon :component="RefreshOutline" :size="14" /></template>
      </NButton>
    </div>

    <NSkeleton v-if="!loaded" text :repeat="4" style="margin-top: 10px" />

    <div v-else-if="!dates.length" class="empty">
      题材热度数据从今日起每 5 分钟累积（同花顺涨停题材，无历史源故不能回填）。
      交易日积累后这里按「日期 × 题材」展示各题材当日涨停家数，颜色越深越热，可追踪主线兴起/退潮。
    </div>

    <template v-else>
      <!-- A: 当前主线榜 chips -->
      <div class="mainline">
        <span class="ml-tag">当前主线</span>
        <span
          v-for="t in mainline" :key="t.name"
          class="ml-chip" :class="{ active: selectedTheme === t.name, ebb: t.trend === 'down' }"
          :title="`${t.name} · 累计${t.total}板 / ${t.days_on}天在榜 · ${t.label}`"
          @click="pickTheme(t.name)"
        >
          <b class="ml-name">{{ t.name }}</b>
          <span class="ml-trend" :style="{ color: trendColor(t.trend) }">{{ trendArrow(t.trend) }}{{ t.total }}</span>
        </span>
      </div>

      <div class="body">
        <div class="left">
      <!-- 矩阵 -->
      <div class="matrix-wrap">
        <table class="matrix">
          <thead>
            <tr>
              <th class="th-date">日期</th>
              <th v-for="t in cols" :key="t.name" class="th-theme" :class="{ 'col-hl': selectedTheme === t.name }"
                  :title="`${t.name} · 窗口共 ${t.total} 板 / ${t.days_on} 天在榜`" @click="pickTheme(t.name)">
                <div class="th-name">{{ t.name }}</div>
                <div class="th-total">{{ t.total }}</div>
              </th>
              <th class="th-theme th-total-col" title="当日涨停总数 = 各题材相加(每只涨停股按首标签只计一次, 不重复)">
                <div class="th-name">涨停总数</div>
                <div class="th-total">{{ grandTotal }}</div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in displayDates" :key="d">
              <td class="td-date">{{ fmtDate(d) }}</td>
              <td v-for="t in cols" :key="t.name" class="cell"
                  :class="{ 'col-hl': selectedTheme === t.name, clickable: !!cell(d, t.name)?.c, 'cell-active': isActiveCell(d, t.name) }"
                  :style="cellStyle(cell(d, t.name)?.c, t.name)" :title="cellTitle(d, t.name)" @click="onCellClick(d, t.name)">
                {{ cell(d, t.name)?.c || '' }}
              </td>
              <td class="cell total-cell" :title="`${fmtDate(d)} 涨停总数 ${rowTotal(d)} 只`">{{ rowTotal(d) || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 超过10个交易日: 折叠余下历史, 点「更多」展开 -->
      <div v-if="hiddenCount > 0" class="more-row">
        <span class="more-btn" @click="expanded = !expanded">
          {{ expanded ? '收起 ↑' : `更多 · 还有 ${hiddenCount} 个交易日 ↓` }}
        </span>
      </div>

      <!-- 单元格下钻: 点大类格子展开内部细题材分布 + 各细题材代表股 -->
      <div v-if="activeCell && cell(activeCell.d, activeCell.theme)?.c" class="cell-detail">
        <div class="cd-title">{{ fmtDate(activeCell.d) }} · {{ activeCell.theme }} · 共{{ cell(activeCell.d, activeCell.theme)?.c }}只涨停 · 细分{{ activeCellSubs.length }}个题材
          <span class="cd-close" title="收起" @click="activeCell = null">×</span>
        </div>
        <div v-for="sub in activeCellSubs" :key="sub.theme" class="cd-sub">
          <span class="cd-sub-name">{{ sub.theme }}</span>
          <span class="cd-sub-cnt">{{ sub.c }}只</span>
          <template v-for="s in stocksOf(sub.s)" :key="s.name">
            <span v-if="s.code" class="cd-stock linked" role="button" tabindex="0" @click="ui.openStock(s.code!, s.name)" @keydown.enter="ui.openStock(s.code!, s.name)">{{ s.name }}</span>
            <span v-else class="cd-stock">{{ s.name }}</span>
          </template>
        </div>
      </div>
        </div><!-- /.left -->

      <!-- B(方向B): 强势主线 · 给我的操作 -->
      <div class="rank">
        <div class="rank-title">强势主线 · 给我的操作<span class="rk-meta">板块状态 × 你的持仓 → 动作</span></div>
        <div v-if="!hasOps" class="op-empty">你的自选暂未踩在当前主线上</div>
        <template v-else>
          <!-- 该减/止盈 -->
          <div v-if="opGroups.reduce.length" class="op-group">
            <div class="op-head reduce">该减 / 止盈<span class="op-sub">主线退潮 · 你持仓</span></div>
            <div v-for="it in opShown('reduce', opGroups.reduce)" :key="it.code" class="op-row">
              <span class="op-theme" @click="pickTheme(it.theme)">{{ it.label }} {{ it.theme }}</span>
              <span class="op-stock" role="button" tabindex="0" @click="ui.openStock(it.code, it.name)" @keydown.enter="ui.openStock(it.code, it.name)">{{ it.name }}</span>
              <span v-if="it.pct != null" class="op-pct" :class="it.pct >= 0 ? 'up' : 'down'">{{ it.pct >= 0 ? '+' : '' }}{{ it.pct.toFixed(2) }}%</span>
              <span class="op-action reduce">→ {{ it.action }}</span>
            </div>
            <div v-if="opGroups.reduce.length > OP_LIMIT" class="op-more" @click="toggleOp('reduce')">{{ opMoreLabel('reduce', opGroups.reduce.length) }}</div>
          </div>
          <!-- 可低吸/关注 -->
          <div v-if="opGroups.buy.length" class="op-group">
            <div class="op-head buy">可低吸 / 关注<span class="op-sub">主线升温 · 你未持仓</span></div>
            <div v-for="it in opShown('buy', opGroups.buy)" :key="it.code" class="op-row">
              <span class="op-theme" @click="pickTheme(it.theme)">{{ it.label }} {{ it.theme }}</span>
              <span class="op-stock" role="button" tabindex="0" @click="ui.openStock(it.code, it.name)" @keydown.enter="ui.openStock(it.code, it.name)">{{ it.name }}</span>
              <span v-if="it.pct != null" class="op-pct" :class="it.pct >= 0 ? 'up' : 'down'">{{ it.pct >= 0 ? '+' : '' }}{{ it.pct.toFixed(2) }}%</span>
              <span class="op-action buy">→ {{ it.action }}</span>
            </div>
            <div v-if="opGroups.buy.length > OP_LIMIT" class="op-more" @click="toggleOp('buy')">{{ opMoreLabel('buy', opGroups.buy.length) }}</div>
          </div>
          <!-- 持有 -->
          <div v-if="opGroups.hold.length" class="op-group">
            <div class="op-head hold">持有<span class="op-sub">主线持续 · 你持仓</span></div>
            <div v-for="it in opShown('hold', opGroups.hold)" :key="it.code" class="op-row">
              <span class="op-theme" @click="pickTheme(it.theme)">{{ it.label }} {{ it.theme }}</span>
              <span class="op-stock" role="button" tabindex="0" @click="ui.openStock(it.code, it.name)" @keydown.enter="ui.openStock(it.code, it.name)">{{ it.name }}</span>
              <span v-if="it.pct != null" class="op-pct" :class="it.pct >= 0 ? 'up' : 'down'">{{ it.pct >= 0 ? '+' : '' }}{{ it.pct.toFixed(2) }}%</span>
              <span class="op-action hold">→ {{ it.action }}</span>
            </div>
            <div v-if="opGroups.hold.length > OP_LIMIT" class="op-more" @click="toggleOp('hold')">{{ opMoreLabel('hold', opGroups.hold.length) }}</div>
          </div>
          <!-- 补票候选 -->
          <div v-if="opGroups.refill.length" class="op-group">
            <div class="op-head refill">补票候选<span class="op-sub">主线强 · 你无票</span></div>
            <div v-for="it in opShown('refill', opGroups.refill)" :key="it.theme" class="op-row">
              <span class="op-theme" @click="pickTheme(it.theme)">{{ it.label }} {{ it.theme }}</span>
              <span class="op-rep" :title="it.rep">代表: {{ it.rep || '—' }}</span>
            </div>
            <div v-if="opGroups.refill.length > OP_LIMIT" class="op-more" @click="toggleOp('refill')">{{ opMoreLabel('refill', opGroups.refill.length) }}</div>
          </div>
          <!-- 退潮别追 (置底弱化) -->
          <div v-if="opGroups.avoid.length" class="op-group">
            <div class="op-head avoid">退潮别追<span class="op-sub">观察票 · 主线退潮</span></div>
            <div v-for="it in opShown('avoid', opGroups.avoid)" :key="it.code" class="op-row muted">
              <span class="op-theme" @click="pickTheme(it.theme)">{{ it.label }} {{ it.theme }}</span>
              <span class="op-stock" role="button" tabindex="0" @click="ui.openStock(it.code, it.name)" @keydown.enter="ui.openStock(it.code, it.name)">{{ it.name }}</span>
              <span v-if="it.pct != null" class="op-pct" :class="it.pct >= 0 ? 'up' : 'down'">{{ it.pct >= 0 ? '+' : '' }}{{ it.pct.toFixed(2) }}%</span>
            </div>
            <div v-if="opGroups.avoid.length > OP_LIMIT" class="op-more" @click="toggleOp('avoid')">{{ opMoreLabel('avoid', opGroups.avoid.length) }}</div>
          </div>
        </template>
      </div>
      </div><!-- /.body -->
    </template>
  </div>
</template>

<style scoped>
/* 配色统一对齐全站(EmotionPanel/SectorRotationPanel): 涨红跌绿 + 中性灰, 用 var(--x, 回退) 写法。
   强调色(可点/选中)统一蓝 var(--accent-fg); 红只留给"热力/风险", 不再用蓝紫绿橙四色彩虹分组。 */
.theme-heat { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: 6px; padding: 8px 12px; }
.head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.title { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 600; color: var(--fg-default); }
.title .meta { font-size: 11px; font-weight: 400; color: var(--fg-subtle); margin-left: 4px; }
.empty { margin-top: 12px; text-align: center; color: var(--fg-subtle); font-size: 12px; line-height: 1.7; padding: 14px; }

/* A 当前主线榜 */
.mainline { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 10px 0 4px; }
.ml-tag { font-size: 11px; font-weight: 700; color: var(--on-emphasis); background: var(--up-fg); border-radius: 4px; padding: 2px 7px; }
.ml-chip { display: inline-flex; align-items: baseline; gap: 4px; padding: 2px 8px; border-radius: 12px; background: color-mix(in srgb, var(--up-fg) 6%, transparent); border: 1px solid color-mix(in srgb, var(--up-fg) 22%, transparent); font-size: 12px; cursor: pointer; touch-action: manipulation; transition: border-color .12s; }
.ml-chip:hover { border-color: var(--up-fg); }
.ml-chip.active { background: var(--up-fg); border-color: var(--up-fg); }
.ml-chip.active .ml-name, .ml-chip.active .ml-trend { color: var(--on-emphasis) !important; }
.ml-chip.ebb { background: color-mix(in srgb, var(--down-fg) 6%, transparent); border-color: color-mix(in srgb, var(--down-fg) 28%, transparent); }
.ml-name { font-weight: 600; color: var(--fg-default); }
.ml-trend { font-size: 11px; font-variant-numeric: tabular-nums; }

.body { display: flex; gap: 16px; align-items: flex-start; margin-top: 6px; }
.left { flex: 0 1 auto; min-width: 0; }
.matrix-wrap { overflow-x: auto; }
.matrix { border-collapse: separate; border-spacing: 2px; font-size: 11px; }
.matrix th, .matrix td { text-align: center; }
.th-date { position: sticky; left: 0; background: var(--bg-surface); z-index: 1; }
.th-theme { min-width: 52px; padding: 2px 4px; cursor: pointer; touch-action: manipulation; }
.th-name { font-size: 11px; font-weight: 600; color: var(--fg-default); white-space: nowrap; }
.th-total { font-size: 10px; color: var(--fg-subtle); font-weight: 400; font-variant-numeric: tabular-nums; }
.td-date { position: sticky; left: 0; background: var(--bg-surface); z-index: 1; font-size: 11px; color: var(--fg-muted); padding-right: 6px; white-space: nowrap; text-align: right; }
.cell { min-width: 48px; height: 24px; border-radius: 3px; font-weight: 700; color: var(--fg-subtle); font-variant-numeric: tabular-nums; cursor: default; }
.cell.clickable { cursor: pointer; touch-action: manipulation; }
/* 选中态统一为单一强调色(蓝): 列高亮=浅蓝软描边, 单元格下钻=实蓝+轻投影区分层级; 红留给热力本身 */
.col-hl { outline: 2px solid color-mix(in srgb, var(--accent-fg) 50%, transparent); outline-offset: -2px; }
.cell-active { outline: 2px solid var(--accent-fg); outline-offset: -2px; box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent-fg) 18%, transparent); }
/* 涨停总数列: 合计列, 中性底色与热力格区分, 左侧分隔线 */
.th-total-col { border-left: 2px solid var(--border-default); cursor: default; }
.total-cell { background: var(--bg-sunken); color: var(--fg-default); font-weight: 800; border-left: 2px solid var(--border-default); font-variant-numeric: tabular-nums; }

/* 更多/收起 展开按钮 */
.more-row { margin-top: 6px; text-align: center; }
.more-btn { display: inline-block; font-size: 11px; color: var(--accent-fg); cursor: pointer; padding: 2px 10px; border-radius: 10px; background: color-mix(in srgb, var(--accent-fg) 5%, transparent); border: 1px solid color-mix(in srgb, var(--accent-fg) 13%, transparent); }
.more-btn:hover { background: color-mix(in srgb, var(--accent-fg) 9%, transparent); border-color: color-mix(in srgb, var(--accent-fg) 20%, transparent); }

/* 单元格下钻详情条 */
.cell-detail { margin-top: 8px; padding: 6px 8px; background: var(--bg-default); border: 1px solid var(--border-muted); border-radius: 4px; font-size: 12px; line-height: 1.8; }
.cd-title { font-weight: 600; color: var(--fg-default); margin-bottom: 4px; }
.cd-sub { padding: 2px 0; border-top: 1px dashed var(--border-muted); }
.cd-sub:first-of-type { border-top: none; }
.cd-sub-name { font-weight: 600; color: var(--up-fg); margin-right: 6px; }
.cd-sub-cnt { color: var(--fg-subtle); font-size: 11px; margin-right: 8px; font-variant-numeric: tabular-nums; }
.cd-stock { margin-right: 8px; color: var(--fg-muted); }
.cd-stock.linked { color: var(--accent-fg); cursor: pointer; touch-action: manipulation; }
.cd-stock.linked:hover { text-decoration: underline; }
.cd-close { float: right; color: var(--fg-subtle); cursor: pointer; font-size: 14px; padding: 0 2px; }
.cd-close:hover { color: var(--danger-fg); }

/* B 强势/热点板块榜 (矩阵右侧栏) */
.rank { width: 340px; flex-shrink: 0; border-left: 1px dashed var(--border-muted); padding-left: 12px; }
.rank-title { font-size: 12px; font-weight: 600; color: var(--fg-default); margin-bottom: 6px; }
.rk-meta { font-weight: 400; color: var(--fg-subtle); font-size: 10.5px; margin-left: 6px; }
.rk-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 3px 0; font-size: 12px; }
.rk-no { width: 16px; text-align: center; color: var(--fg-subtle); font-weight: 700; }
.rk-label { font-size: 11px; }
.rk-name { font-weight: 600; color: var(--up-fg); cursor: pointer; }
.rk-name:hover { text-decoration: underline; }
.rk-stat { color: var(--fg-muted); font-variant-numeric: tabular-nums; }
.rk-rep { color: var(--fg-subtle); font-size: 11px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rk-pool { color: var(--fg-muted); font-size: 11px; }
.rk-stock { color: var(--accent-fg); cursor: pointer; margin-left: 4px; }
.rk-stock:hover { text-decoration: underline; }

/* 给我的操作 分组 — 三色语义纪律: 红(风险/该减) · 中性(持有/低吸/补票) · 弱化灰(退潮别追)。
   去掉🔴🟢🔵⚪⚠️彩色 emoji, 改全站邻居那套"纯色文字表头"; 头部左侧色条用 currentColor 自动同色。 */
.op-empty { color: var(--fg-subtle); font-size: 12px; padding: 14px 4px; text-align: center; }
.op-group { margin-bottom: 8px; }
.op-head { font-size: 11.5px; font-weight: 700; padding: 2px 0 3px; display: flex; align-items: baseline; gap: 6px; }
.op-head::before { content: ''; align-self: center; flex-shrink: 0; width: 3px; height: 11px; border-radius: 2px; background: currentColor; opacity: 0.65; }
.op-head .op-sub { font-size: 10px; font-weight: 400; color: var(--fg-subtle); }
.op-head.reduce { color: var(--danger-fg); }          /* 风险: 持仓+退潮, 该减/止盈 */
.op-head.buy { color: var(--fg-muted); }               /* 中性: 升温机会, 低吸/关注 */
.op-head.hold { color: var(--fg-muted); }              /* 中性: 持续, 持有 */
.op-head.refill { color: var(--fg-subtle); }            /* 中性偏淡: 候选 */
.op-head.avoid { color: var(--fg-subtle); }             /* 弱化灰: 退潮别追 */
.op-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 2px 0 2px 6px; font-size: 12px; }
.op-row.muted { opacity: 0.62; }
.op-theme { color: var(--fg-muted); cursor: pointer; font-size: 11px; }
.op-theme:hover { color: var(--up-fg); }
.op-stock { font-weight: 600; color: var(--accent-fg); cursor: pointer; touch-action: manipulation; }
.op-stock:hover { text-decoration: underline; }
.op-pct { font-variant-numeric: tabular-nums; font-size: 11px; }
.op-pct.up { color: var(--up-fg); }
.op-pct.down { color: var(--down-fg); }
.op-action { margin-left: auto; font-weight: 600; font-size: 11px; }
.op-action.reduce { color: var(--danger-fg); }         /* 风险动作=红 */
.op-action.buy { color: var(--fg-muted); }             /* 机会/持有动作=中性, 不用绿(A股绿=跌) */
.op-action.hold { color: var(--fg-muted); }
.op-rep { color: var(--fg-subtle); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.op-more { font-size: 11px; color: var(--accent-fg); cursor: pointer; padding: 3px 0 3px 6px; user-select: none; }
.op-more:hover { text-decoration: underline; }

/* 中窄屏(平板及以下): 两栏堆叠回上下, rank 右栏改全宽, 矩阵与榜单都不被挤压 */
@media (max-width: 1024px) {
  .body { flex-direction: column; }
  .left { width: 100%; }
  .rank { width: auto; border-left: none; padding-left: 0; border-top: 1px dashed var(--border-muted); margin-top: 12px; padding-top: 8px; }
}
/* 手机端(768): 收紧内距/字号, 矩阵保持横向滚动(宽表不硬挤), 主线 chips 与操作行可换行 */
@media (max-width: 768px) {
  .theme-heat { padding: 8px 10px; }
  .title { font-size: 13px; }
  .mainline { gap: 5px; margin: 8px 0 4px; }
  .body { gap: 12px; }
  .matrix { font-size: 10.5px; }
  .cell { min-width: 40px; height: 22px; }
  .th-theme { min-width: 44px; }
  .rank { margin-top: 10px; }
  .rk-rep { max-width: 60vw; }
}
</style>
