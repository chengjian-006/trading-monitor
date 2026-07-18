<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { NButton, NSelect, NIcon, NSkeleton, NTag, NInput } from 'naive-ui'
import { DownloadOutline, RefreshOutline, SearchOutline } from '@vicons/ionicons5'
import client from '../api/client'
import FilterPanel from '../components/common/FilterPanel.vue'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { fetchLimitUp, fetchLimitUpDates, limitUpExportUrl, type LimitUpStock, type LimitUpMeta } from '../api/limit-up'

const message = useGlobalMessage()
const loading = ref(false)
const boards = ref<LimitUpStock[]>([])
const meta = ref<LimitUpMeta>({})
const tradeDate = ref<string | null>(null)
const isLive = ref(false)
const dates = ref<string[]>([])
const activeDate = ref<string | undefined>(undefined)
const sortKey = ref<keyof LimitUpStock>('height')
const sortAsc = ref(false)
const viewMode = ref<'stock' | 'concept'>('stock')

// 「全部涨停」表内查询区(仅按个股视图, 客户端过滤已加载数据)
const kw = ref('')
const conceptFilter = ref<string | null>(null)
const heightFilter = ref<'all' | 'first' | 'multi'>('all')
const heightOptions = [
  { label: '全部板数', value: 'all' },
  { label: '仅首板', value: 'first' },
  { label: '2板及以上', value: 'multi' },
]
function resetFilter() { kw.value = ''; conceptFilter.value = null; heightFilter.value = 'all' }
const filterActive = computed(() =>
  !!kw.value.trim() || !!conceptFilter.value || heightFilter.value !== 'all')

// 概念归一(与后端一致)——热点分布计数用
const MERGE: Record<string, string> = {
  宇树: '机器人', 减速器: '机器人', 减速机: '机器人', 灵巧手: '机器人', 谐波: '机器人',
  丝杠: '机器人', 具身智能: '机器人', 人形机器人: '机器人',
  航天: '商业航天', 卫星: '商业航天', 火箭: '商业航天',
  存储: '半导体', 芯片: '半导体', 封装: '半导体', SSD: '半导体',
  覆铜板: 'PCB', 创新药: '医药', 摘帽: 'ST摘帽', 脱星: 'ST摘帽',
  算力: '算力/数据中心', 数据中心: '算力/数据中心', 液冷: '算力/数据中心',
}
const KEYWORDS = ['机器人', '宇树', '减速器', '减速机', '灵巧手', '谐波', '丝杠', '具身智能',
  '黄金', '半导体', '存储', '芯片', '封装', '商业航天', '航天', '卫星', 'PCB', '覆铜板',
  '创新药', '医药', 'ST', '摘帽', '算力', '数据中心', '液冷', '特斯拉', '低空经济', '光伏', '变压器', '资产重组']

const dateLabel = computed(() => {
  const d = tradeDate.value
  if (!d) return '—'
  return d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d
})
const dateOptions = computed(() => dates.value.map(d => ({
  label: d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d, value: d,
})))

const ladder = computed(() =>
  boards.value.filter(b => (b.height || 1) >= 2)
    .sort((a, b) => (b.height - a.height) || a.code.localeCompare(b.code)))

const conceptRanking = computed(() => {
  const cnt: Record<string, number> = {}
  for (const b of boards.value) {
    const seen = new Set<string>()
    for (const kw of KEYWORDS) if ((b.reason || '').includes(kw)) seen.add(MERGE[kw] || kw)
    for (const g of seen) cnt[g] = (cnt[g] || 0) + 1
  }
  return Object.entries(cnt).filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 12)
})
const rankMax = computed(() => conceptRanking.value.length ? conceptRanking.value[0][1] : 1)

// 按概念维度归组: 拆 reason 的 '+' 多标签, 命中关键词的并入归一组(与热点分布同口径),
// 未命中的用原始标签自成一组; ≥2只成组展示, 没进任何组的票兜底进「其他」
// 把一条 reason 拆成归一后的概念标签集(与热点分布/概念分组同口径)
function conceptTokens(reason: string | null | undefined): string[] {
  const tokens = String(reason || '').split(/[+＋]/).map(t => t.trim()).filter(Boolean)
  const names = new Set<string>()
  for (const t of tokens) {
    let g: string | null = null
    for (const key of KEYWORDS) if (t.includes(key)) { g = MERGE[key] || key; break }
    names.add(g || t)
  }
  return [...names]
}

interface ConceptGroup { name: string; stocks: LimitUpStock[] }
const conceptGroups = computed<ConceptGroup[]>(() => {
  const map = new Map<string, LimitUpStock[]>()
  for (const b of boards.value) {
    for (const n of conceptTokens(b.reason)) {
      if (!map.has(n)) map.set(n, [])
      map.get(n)!.push(b)
    }
  }
  const sortStocks = (arr: LimitUpStock[]) =>
    [...arr].sort((a, b) => ((b.height || 1) - (a.height || 1)) || ((b.pct ?? 0) - (a.pct ?? 0)))
  const groups: ConceptGroup[] = []
  for (const [name, stocks] of map) if (stocks.length >= 2) groups.push({ name, stocks: sortStocks(stocks) })
  groups.sort((a, b) => (b.stocks.length - a.stocks.length)
    || ((b.stocks[0]?.height || 1) - (a.stocks[0]?.height || 1)))
  const covered = new Set(groups.flatMap(g => g.stocks.map(s => s.code)))
  const rest = boards.value.filter(b => !covered.has(b.code))
  if (rest.length) groups.push({ name: '其他', stocks: sortStocks(rest) })
  return groups
})

// 概念下拉选项 —— 从当前涨停数据动态汇总(按命中只数降序)
const conceptOptions = computed(() => {
  const cnt = new Map<string, number>()
  for (const b of boards.value)
    for (const n of conceptTokens(b.reason)) cnt.set(n, (cnt.get(n) || 0) + 1)
  return [...cnt.entries()]
    .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]))
    .map(([n, c]) => ({ label: `${n}（${c}）`, value: n }))
})

// 表内过滤: 关键词(代码/名称) + 概念 + 板数, 只作用于「全部涨停」个股表
const filteredBoards = computed(() => {
  const q = kw.value.trim().toLowerCase()
  const c = conceptFilter.value
  const h = heightFilter.value
  return boards.value.filter(b => {
    if (q && !(b.code.toLowerCase().includes(q) || (b.name || '').toLowerCase().includes(q))) return false
    if (c && !conceptTokens(b.reason).includes(c)) return false
    if (h === 'first' && (b.height || 1) !== 1) return false
    if (h === 'multi' && (b.height || 1) < 2) return false
    return true
  })
})

const sortedBoards = computed(() => {
  const k = sortKey.value, asc = sortAsc.value
  return [...filteredBoards.value].sort((a, b) => {
    let x: any = a[k], y: any = b[k]
    if (k === 'reason' || k === 'name' || k === 'code' || k === 'streak_label') {
      x = String(x); y = String(y); return asc ? x.localeCompare(y) : y.localeCompare(x)
    }
    return asc ? (x - y) : (y - x)
  })
})

function setSort(k: keyof LimitUpStock) {
  if (sortKey.value === k) sortAsc.value = !sortAsc.value
  else { sortKey.value = k; sortAsc.value = false }
}
function bardgeType(h: number): 'default' | 'warning' | 'error' {
  return h >= 3 ? 'error' : h >= 2 ? 'warning' : 'default'
}
function sealText(s: number | null | undefined) {
  return (typeof s === 'number') ? `${(s * 100).toFixed(0)}%` : '—'
}
// 跳同花顺网页版个股页(分时+K线; 手机端自动转 m.10jqka)
function openThs(code: string) {
  window.open(`https://stockpage.10jqka.com.cn/${code}/`, '_blank', 'noopener')
}

async function load(date?: string) {
  loading.value = true
  try {
    const d = await fetchLimitUp(date)
    boards.value = d.boards || []
    meta.value = d.meta || {}
    tradeDate.value = d.trade_date
    isLive.value = d.live
    activeDate.value = d.trade_date || undefined
  } catch {
    message.error('涨停复盘加载失败')
  } finally {
    loading.value = false
  }
}

async function onExport() {
  try {
    const resp = await client.get(limitUpExportUrl(activeDate.value), { responseType: 'blob' })
    const url = URL.createObjectURL(resp.data as Blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `涨停复盘_${tradeDate.value || ''}.csv`
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  } catch {
    message.error('导出失败')
  }
}

onMounted(async () => {
  await load()
  try { dates.value = await fetchLimitUpDates() } catch { /* 首次无存档正常 */ }
})
</script>

<template>
  <div class="lu-wrap">
    <header class="lu-top">
      <div class="lu-title">
        <div class="lu-eyebrow">每日涨停复盘</div>
        <h1>{{ dateLabel }}<span v-if="isLive" class="lu-live">盘中实时</span></h1>
      </div>
      <div class="lu-controls">
        <NSelect v-if="dateOptions.length" v-model:value="activeDate" :options="dateOptions"
          size="small" style="width:150px" @update:value="(v:string)=>load(v)" placeholder="选择日期" />
        <NButton size="small" @click="load(activeDate)" :loading="loading">
          <template #icon><NIcon :component="RefreshOutline" /></template>刷新
        </NButton>
        <NButton size="small" type="primary" ghost @click="onExport" :disabled="!boards.length">
          <template #icon><NIcon :component="DownloadOutline" /></template>导出CSV
        </NButton>
      </div>
    </header>

    <div class="lu-src">数据源 <b>同花顺涨停池</b>（远航版同口径）· 收盘定版</div>

    <NSkeleton v-if="loading && !boards.length" text :repeat="6" style="margin-top:20px" />
    <template v-else>
      <div class="lu-kpis">
        <div class="lu-kpi up"><div class="k">涨停</div><div class="v">{{ meta.limit_up_count ?? '—' }}<small>家</small></div></div>
        <div class="lu-kpi"><div class="k">曾涨停</div><div class="v">{{ meta.limit_up_history ?? '—' }}<small>家</small></div></div>
        <div class="lu-kpi"><div class="k">炸板</div><div class="v">{{ meta.broken_board_count ?? '—' }}<small>家</small></div></div>
        <div class="lu-kpi"><div class="k">封板率</div><div class="v">{{ sealText(meta.seal_rate) }}</div></div>
        <div class="lu-kpi down"><div class="k">跌停</div><div class="v">{{ meta.limit_down_count ?? '—' }}<small>家</small></div></div>
      </div>

      <template v-if="boards.length">
        <h2><span class="c">连板梯队</span><span class="n">{{ ladder.length }} 只（2板及以上）</span></h2>
        <div class="lu-ladder">
          <div v-for="b in ladder" :key="b.code" class="lu-card clickable" role="link" tabindex="0"
            :title="`${b.name} ${b.code} · 打开同花顺分时/K线`"
            @click="openThs(b.code)" @keydown.enter="openThs(b.code)">
            <div class="r1"><span class="nm">{{ b.name }}</span><span class="cd">{{ b.code }}</span>
              <span class="bd">{{ b.streak_label }}</span></div>
            <div class="rs">{{ (b.reason || '').split('+').slice(0, 3).join(' · ') }}</div>
          </div>
        </div>

        <h2><span class="c">热点分布</span><span class="n">按概念关键词命中只数</span></h2>
        <div class="lu-bars">
          <div v-for="[g, c] in conceptRanking" :key="g" class="lu-bar">
            <div class="lb">{{ g }}</div>
            <div class="tr"><div class="fl" :style="{ width: Math.round(c / rankMax * 100) + '%' }"></div></div>
            <div class="ct">{{ c }}</div>
          </div>
        </div>

        <h2>
          <span class="c">全部涨停</span>
          <span class="n">{{ boards.length }} 只 · {{ viewMode === 'stock' ? '点表头可排序' : '按涨停概念归组 · 一票多概念会重复出现' }}</span>
          <div class="lu-seg" role="tablist">
            <button role="tab" :aria-selected="viewMode === 'stock'" :class="{ on: viewMode === 'stock' }" @click="viewMode = 'stock'">按个股</button>
            <button role="tab" :aria-selected="viewMode === 'concept'" :class="{ on: viewMode === 'concept' }" @click="viewMode = 'concept'">按概念</button>
          </div>
        </h2>

        <div v-if="viewMode === 'concept'" class="lu-cgrid">
          <div v-for="g in conceptGroups" :key="g.name" class="lu-cgroup" :class="{ other: g.name === '其他' }">
            <div class="hd">
              <span class="gn">{{ g.name }}</span>
              <span class="gc">{{ g.stocks.length }}<small>只</small></span>
            </div>
            <div class="gs">
              <div v-for="b in g.stocks" :key="b.code" class="gi" role="link" tabindex="0"
                :title="`${b.name} ${b.code} · 打开同花顺分时/K线`"
                @click="openThs(b.code)" @keydown.enter="openThs(b.code)">
                <span class="nm">{{ b.name }}</span>
                <span class="bd" :class="(b.height || 1) >= 3 ? 'h3' : (b.height || 1) >= 2 ? 'h2c' : 'h1'">{{ b.streak_label }}</span>
                <span class="pc">{{ b.pct != null ? '+' + b.pct.toFixed(1) + '%' : '—' }}</span>
              </div>
            </div>
          </div>
        </div>

        <template v-else>
          <FilterPanel>
          <div class="lu-filter">
            <NInput v-model:value="kw" size="small" clearable placeholder="搜代码 / 名称"
              class="f-kw" :input-props="{ type: 'search' }">
              <template #prefix><NIcon :component="SearchOutline" /></template>
            </NInput>
            <NSelect v-model:value="conceptFilter" :options="conceptOptions" size="small"
              clearable filterable placeholder="全部概念" class="f-cpt" />
            <NSelect v-model:value="heightFilter" :options="heightOptions" size="small" class="f-ht" />
            <NButton size="small" quaternary :disabled="!filterActive" @click="resetFilter">
              <template #icon><NIcon :component="RefreshOutline" /></template>重置
            </NButton>
            <span v-if="filterActive" class="f-n">{{ filteredBoards.length }} / {{ boards.length }} 只</span>
          </div>
          </FilterPanel>

          <div v-if="!filteredBoards.length" class="lu-empty">没有匹配的涨停股，试试放宽筛选条件。</div>
          <div v-else class="lu-tablewrap">
          <table class="lu-tbl">
            <thead><tr>
              <th @click="setSort('code')">代码</th>
              <th @click="setSort('name')">名称</th>
              <th @click="setSort('height')" :class="{ sorted: sortKey === 'height', asc: sortAsc }">板数</th>
              <th @click="setSort('pct')" :class="{ sorted: sortKey === 'pct', asc: sortAsc }">涨幅</th>
              <th @click="setSort('open_times')" :class="{ sorted: sortKey === 'open_times', asc: sortAsc }">炸板</th>
              <th>涨停概念</th>
            </tr></thead>
            <tbody>
              <tr v-for="b in sortedBoards" :key="b.code">
                <td class="code">{{ b.code }}</td>
                <td class="name"><span class="nm-link" role="link" tabindex="0"
                  :title="`打开同花顺分时/K线`"
                  @click="openThs(b.code)" @keydown.enter="openThs(b.code)">{{ b.name }}</span></td>
                <td><NTag :type="bardgeType(b.height)" size="small" round :bordered="false">{{ b.streak_label }}</NTag></td>
                <td class="pct">{{ b.pct != null ? '+' + b.pct.toFixed(2) + '%' : '—' }}</td>
                <td><span v-if="b.open_times > 0" class="zb">{{ b.open_times }}</span><span v-else class="dash">—</span></td>
                <td class="rs-cell">{{ b.reason }}</td>
              </tr>
            </tbody>
          </table>
          </div>
        </template>
      </template>
      <div v-else class="lu-empty">该日暂无涨停复盘数据。收盘后 15:40 自动存档；首次上线可稍等历史回填完成。</div>

      <p class="lu-foot">
        「板数」为同花顺口径（首板 / N天M板 中的 M）。「炸板」为当日开板次数，数值大＝反复打开、封板不坚决。
        「涨停概念」为同花顺原始多标签题材。此页由涨停池结构化数据直接生成，非图片识别。
      </p>
    </template>
  </div>
</template>

<style scoped>
.lu-wrap { max-width: 1060px; margin: 0 auto; padding: 20px 18px 60px; }
.lu-top { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; }
.lu-eyebrow { font-size: 12px; letter-spacing: .16em; color: #b0812c; font-weight: 700; }
.lu-title h1 { font-size: 24px; margin: 2px 0 0; font-weight: 800; display: flex; align-items: baseline; gap: 10px; }
.lu-live { font-size: 12px; font-weight: 700; color: var(--up-fg); background: var(--up-bg-muted); padding: 1px 8px; border-radius: 20px; }
.lu-controls { margin-left: auto; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.lu-src { font-size: 12.5px; color: var(--fg-subtle); margin-top: 6px; }
.lu-src b { color: var(--fg-muted); }

.lu-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 18px 0 28px; }
.lu-kpi { background: var(--n-card-color, var(--bg-surface)); border: 1px solid var(--border-default); border-radius: 10px; padding: 12px 13px; }
.lu-kpi .k { font-size: 12px; color: var(--fg-muted); }
.lu-kpi .v { font-size: 24px; font-weight: 800; margin-top: 3px; font-variant-numeric: tabular-nums; line-height: 1; }
.lu-kpi .v small { font-size: 12px; font-weight: 600; color: var(--fg-subtle); margin-left: 2px; }
.lu-kpi.up .v { color: var(--up-fg); } .lu-kpi.down .v { color: var(--down-fg); }

h2 { font-size: 15px; font-weight: 800; margin: 30px 0 12px; display: flex; align-items: baseline; gap: 9px; }
h2 .c { color: #b0812c; } h2 .n { font-size: 12.5px; color: var(--fg-subtle); font-weight: 600; }

.lu-ladder { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.lu-card { background: var(--n-card-color, var(--bg-surface)); border: 1px solid var(--border-default); border-left: 3px solid var(--up-fg); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 3px; }
.lu-card .r1 { display: flex; align-items: baseline; gap: 8px; }
.lu-card .nm { font-weight: 700; font-size: 15px; }
.lu-card .cd { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: var(--fg-subtle); }
.lu-card .bd { margin-left: auto; font-size: 12px; font-weight: 800; color: var(--up-fg); background: var(--up-bg-muted); padding: 1px 7px; border-radius: 20px; }
.lu-card .rs { font-size: 12.5px; color: var(--fg-muted); }
.lu-card.clickable { cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.lu-card.clickable:hover { border-color: #d9c9a3; box-shadow: 0 2px 8px rgba(120, 90, 30, .10); }

.lu-bars { display: flex; flex-direction: column; gap: 7px; max-width: 640px; }
.lu-bar { display: grid; grid-template-columns: 100px 1fr 34px; align-items: center; gap: 11px; font-size: 13px; }
.lu-bar .lb { text-align: right; color: var(--fg-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lu-bar .tr { background: var(--bg-default); border-radius: 5px; height: 16px; overflow: hidden; }
.lu-bar .fl { height: 100%; background: linear-gradient(90deg, #b0812c, var(--up-fg)); border-radius: 5px; }
.lu-bar .ct { text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; }

/* 按个股/按概念 分段切换 —— 沿用页面金/红暖色语言 */
.lu-seg { margin-left: auto; display: inline-flex; background: var(--bg-default); border: 1px solid var(--border-muted); border-radius: 20px; padding: 2px; gap: 2px; }
.lu-seg button { appearance: none; border: 0; background: transparent; font: inherit; font-size: 12.5px; font-weight: 700; color: var(--fg-subtle); padding: 3px 14px; border-radius: 16px; cursor: pointer; transition: color .18s, background .18s, box-shadow .18s; }
.lu-seg button:hover { color: var(--fg-muted); }
.lu-seg button.on { background: var(--bg-surface); color: #b0812c; box-shadow: 0 1px 3px rgba(90, 70, 30, .14); }

/* 概念分组卡片 */
.lu-cgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; align-items: start; }
.lu-cgroup { background: var(--n-card-color, var(--bg-surface)); border: 1px solid var(--border-default); border-radius: 12px; overflow: hidden; }
.lu-cgroup .hd { display: flex; align-items: baseline; gap: 8px; padding: 9px 13px 8px; background: linear-gradient(180deg, #faf6ee, #f6f1e7); border-bottom: 1px solid var(--border-muted); }
.lu-cgroup .gn { font-size: 14px; font-weight: 800; color: #7a5a1e; }
.lu-cgroup .gc { margin-left: auto; font-size: 15px; font-weight: 800; color: var(--up-fg); font-variant-numeric: tabular-nums; }
.lu-cgroup .gc small { font-size: 11px; font-weight: 600; color: var(--fg-subtle); margin-left: 1px; }
.lu-cgroup .gs { padding: 4px 6px 6px; }
.lu-cgroup .gi { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 8px; padding: 5px 7px; border-radius: 7px; font-size: 13px; cursor: pointer; }
.lu-cgroup .gi:hover { background: var(--bg-default); }
.lu-cgroup .gi:hover .nm { color: #b0812c; }
.lu-cgroup .gi .nm { font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color .15s; }
.lu-cgroup .gi .bd { font-size: 11px; font-weight: 800; padding: 0 7px; border-radius: 20px; white-space: nowrap; }
.lu-cgroup .gi .bd.h1 { color: var(--fg-subtle); background: var(--bg-default); }
.lu-cgroup .gi .bd.h2c { color: #b0812c; background: #f7ecd4; }
.lu-cgroup .gi .bd.h3 { color: var(--up-fg); background: var(--up-bg-muted); }
.lu-cgroup .gi .pc { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--up-fg); font-size: 12.5px; }
.lu-cgroup.other { opacity: .85; }
.lu-cgroup.other .hd { background: var(--bg-default); }
.lu-cgroup.other .gn { color: var(--fg-subtle); }
.lu-cgroup.other .gc { color: var(--fg-subtle); }

/* 全部涨停 表内查询区 —— 与顶部控制区同一视觉语言 */
.lu-filter { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 12px; }
.lu-filter .f-kw { max-width: 200px; }
.lu-filter .f-cpt { width: 180px; }
.lu-filter .f-ht { width: 130px; }
.lu-filter .f-n { font-size: 12.5px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; margin-left: 2px; }

.lu-tablewrap { overflow-x: auto; border: 1px solid var(--border-default); border-radius: 12px; }
.lu-tbl { border-collapse: collapse; width: 100%; font-size: 13.5px; min-width: 620px; }
.lu-tbl thead th { position: sticky; top: 0; background: var(--bg-default); color: var(--fg-muted); font-weight: 700; text-align: left; padding: 9px 12px; font-size: 12px; border-bottom: 1px solid var(--border-muted); cursor: pointer; white-space: nowrap; user-select: none; }
.lu-tbl thead th.sorted::after { content: ' ↓'; color: #b0812c; }
.lu-tbl thead th.sorted.asc::after { content: ' ↑'; }
.lu-tbl tbody td { padding: 8px 12px; border-bottom: 1px solid var(--border-muted); vertical-align: top; }
.lu-tbl tbody tr:hover { background: var(--bg-default); }
.lu-tbl td.code { font-family: ui-monospace, Menlo, Consolas, monospace; color: var(--fg-subtle); font-variant-numeric: tabular-nums; }
.lu-tbl td.name { font-weight: 700; white-space: nowrap; }
.nm-link { cursor: pointer; border-bottom: 1px dotted #c9b98f; transition: color .15s; }
.nm-link:hover { color: #b0812c; }
.lu-tbl td.pct { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--up-fg); text-align: right; }
.zb { color: var(--down-fg); font-variant-numeric: tabular-nums; font-size: 12px; }
.dash { color: var(--fg-subtle); }
.rs-cell { color: var(--fg-muted); line-height: 1.45; min-width: 220px; }
.lu-empty { padding: 40px 16px; text-align: center; color: var(--fg-subtle); font-size: 14px; }
.lu-foot { margin-top: 22px; font-size: 12px; color: var(--fg-subtle); line-height: 1.6; }

@media (max-width: 768px) {
  .lu-kpis { grid-template-columns: repeat(2, 1fr); }
  .lu-title h1 { font-size: 20px; }
  .lu-controls { width: 100%; }
  h2 { flex-wrap: wrap; }
  .lu-seg { margin-left: 0; width: 100%; }
  .lu-seg button { flex: 1; }
  .lu-cgrid { grid-template-columns: 1fr; }
  .lu-filter .f-kw, .lu-filter .f-cpt, .lu-filter .f-ht { width: 100%; max-width: none; flex: 1 1 140px; }
}
</style>
