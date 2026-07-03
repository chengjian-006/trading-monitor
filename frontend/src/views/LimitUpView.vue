<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { NButton, NSelect, NIcon, NSkeleton, NTag } from 'naive-ui'
import { DownloadOutline, RefreshOutline } from '@vicons/ionicons5'
import client from '../api/client'
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

const sortedBoards = computed(() => {
  const k = sortKey.value, asc = sortAsc.value
  return [...boards.value].sort((a, b) => {
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
          <div v-for="b in ladder" :key="b.code" class="lu-card">
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

        <h2><span class="c">全部涨停</span><span class="n">{{ boards.length }} 只 · 点表头可排序</span></h2>
        <div class="lu-tablewrap">
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
                <td class="name">{{ b.name }}</td>
                <td><NTag :type="bardgeType(b.height)" size="small" round :bordered="false">{{ b.streak_label }}</NTag></td>
                <td class="pct">{{ b.pct != null ? '+' + b.pct.toFixed(2) + '%' : '—' }}</td>
                <td><span v-if="b.open_times > 0" class="zb">{{ b.open_times }}</span><span v-else class="dash">—</span></td>
                <td class="rs-cell">{{ b.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
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
.lu-live { font-size: 12px; font-weight: 700; color: #d92b26; background: #fbe8e6; padding: 1px 8px; border-radius: 20px; }
.lu-controls { margin-left: auto; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.lu-src { font-size: 12.5px; color: #9c948a; margin-top: 6px; }
.lu-src b { color: #6b655c; }

.lu-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 18px 0 28px; }
.lu-kpi { background: var(--n-card-color, #fff); border: 1px solid #ece7df; border-radius: 10px; padding: 12px 13px; }
.lu-kpi .k { font-size: 12px; color: #726b60; }
.lu-kpi .v { font-size: 24px; font-weight: 800; margin-top: 3px; font-variant-numeric: tabular-nums; line-height: 1; }
.lu-kpi .v small { font-size: 12px; font-weight: 600; color: #9c948a; margin-left: 2px; }
.lu-kpi.up .v { color: #d92b26; } .lu-kpi.down .v { color: #159a6e; }

h2 { font-size: 15px; font-weight: 800; margin: 30px 0 12px; display: flex; align-items: baseline; gap: 9px; }
h2 .c { color: #b0812c; } h2 .n { font-size: 12.5px; color: #9c948a; font-weight: 600; }

.lu-ladder { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.lu-card { background: var(--n-card-color, #fff); border: 1px solid #ece7df; border-left: 3px solid #d92b26; border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 3px; }
.lu-card .r1 { display: flex; align-items: baseline; gap: 8px; }
.lu-card .nm { font-weight: 700; font-size: 15px; }
.lu-card .cd { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: #9c948a; }
.lu-card .bd { margin-left: auto; font-size: 12px; font-weight: 800; color: #d92b26; background: #fbe8e6; padding: 1px 7px; border-radius: 20px; }
.lu-card .rs { font-size: 12.5px; color: #726b60; }

.lu-bars { display: flex; flex-direction: column; gap: 7px; max-width: 640px; }
.lu-bar { display: grid; grid-template-columns: 100px 1fr 34px; align-items: center; gap: 11px; font-size: 13px; }
.lu-bar .lb { text-align: right; color: #726b60; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lu-bar .tr { background: #f0ece5; border-radius: 5px; height: 16px; overflow: hidden; }
.lu-bar .fl { height: 100%; background: linear-gradient(90deg, #b0812c, #d92b26); border-radius: 5px; }
.lu-bar .ct { text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; }

.lu-tablewrap { overflow-x: auto; border: 1px solid #ece7df; border-radius: 12px; }
.lu-tbl { border-collapse: collapse; width: 100%; font-size: 13.5px; min-width: 620px; }
.lu-tbl thead th { position: sticky; top: 0; background: #f4f1ec; color: #726b60; font-weight: 700; text-align: left; padding: 9px 12px; font-size: 12px; border-bottom: 1px solid #ece7df; cursor: pointer; white-space: nowrap; user-select: none; }
.lu-tbl thead th.sorted::after { content: ' ↓'; color: #b0812c; }
.lu-tbl thead th.sorted.asc::after { content: ' ↑'; }
.lu-tbl tbody td { padding: 8px 12px; border-bottom: 1px solid #f0ece5; vertical-align: top; }
.lu-tbl tbody tr:hover { background: #faf8f3; }
.lu-tbl td.code { font-family: ui-monospace, Menlo, Consolas, monospace; color: #9c948a; font-variant-numeric: tabular-nums; }
.lu-tbl td.name { font-weight: 700; white-space: nowrap; }
.lu-tbl td.pct { font-variant-numeric: tabular-nums; font-weight: 700; color: #d92b26; text-align: right; }
.zb { color: #159a6e; font-variant-numeric: tabular-nums; font-size: 12px; }
.dash { color: #c4bdb2; }
.rs-cell { color: #726b60; line-height: 1.45; min-width: 220px; }
.lu-empty { padding: 40px 16px; text-align: center; color: #9c948a; font-size: 14px; }
.lu-foot { margin-top: 22px; font-size: 12px; color: #9c948a; line-height: 1.6; }

@media (max-width: 560px) {
  .lu-kpis { grid-template-columns: repeat(2, 1fr); }
  .lu-title h1 { font-size: 20px; }
  .lu-controls { width: 100%; }
}
</style>
