<script setup lang="ts">
// 问财观点参考 (v1.7.627) — 同花顺问财 chat「智能调度」投顾式推荐的存档。
// 由本地油猴脚本(登录态浏览器发 stream-query SSE)手动问一句、上报整段话术, 服务器撞字典抽出被提及的股票。
// 明确定位: 这是 LLM 投顾观点, 非回测背书的信号。点股弹K线, 可一键加自选。
import { ref, onMounted, computed } from 'vue'
import { NButton, NIcon, NSkeleton, NEmpty, NTag, NPopconfirm, NCollapse, NCollapseItem, NInput, NDatePicker, NSelect } from 'naive-ui'
import { RefreshOutline, TrashOutline, BulbOutline, AddCircleOutline } from '@vicons/ionicons5'
import { listWencaiOpinions, deleteWencaiOpinion, addWencaiToPool, type WencaiOpinion, type WencaiConclusion } from '../api/wencai'
import FilterPanel from '../components/common/FilterPanel.vue'
import { useUiStore } from '../stores/ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'

const ui = useUiStore()
const message = useGlobalMessage()

const opinions = ref<WencaiOpinion[]>([])
const loading = ref(false)
const loaded = ref(false)
const addingId = ref<number | null>(null)

// 查询区 (客户端过滤已加载的观点数组) —— 关键词 / 记录时间段 / 模式 / 上报人
const filterKeyword = ref('')
const filterDateRange = ref<[number, number] | null>(null)
const filterMode = ref<string | null>(null)
const filterUploader = ref<string | null>(null)

function fmtDay(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 模式/上报人下拉选项从已加载数据动态归纳, 避免空选项
const modeOptions = computed(() => {
  const set = new Set(opinions.value.map((o) => o.agent_mode || 'normal'))
  return [...set].map((m) => ({ label: modeLabel(m), value: m }))
})
const uploaderOptions = computed(() => {
  const set = new Set(opinions.value.map((o) => o.uploader).filter(Boolean))
  return [...set].map((u) => ({ label: u, value: u }))
})

const filteredOpinions = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  const range = filterDateRange.value
  const from = range ? fmtDay(range[0]) : ''
  const to = range ? fmtDay(range[1]) : ''
  return opinions.value.filter((op) => {
    if (filterMode.value && (op.agent_mode || 'normal') !== filterMode.value) return false
    if (filterUploader.value && op.uploader !== filterUploader.value) return false
    if (range) {
      const day = (op.created_at || '').slice(0, 10)
      if (!day || day < from || day > to) return false
    }
    if (kw) {
      const hay = [
        op.question,
        op.conclusion?.stock, op.conclusion?.buy, op.conclusion?.logic, op.conclusion?.risk,
        ...op.stocks.map((s) => `${s.name} ${s.code}`),
      ].filter(Boolean).join(' ').toLowerCase()
      if (!hay.includes(kw)) return false
    }
    return true
  })
})

function resetFilters() {
  filterKeyword.value = ''
  filterDateRange.value = null
  filterMode.value = null
  filterUploader.value = null
}

async function load() {
  loading.value = true
  try {
    const { opinions: rows } = await listWencaiOpinions()
    opinions.value = rows
    fetchPrices()   // 异步拉主推股现价, 到了自动补上距现价%
  } catch {
    message.error('加载问财观点失败')
  } finally {
    loading.value = false
    loaded.value = true
  }
}

function fmtTime(t: string | null) {
  return t ? String(t).slice(0, 16).replace('T', ' ') : ''
}

function modeLabel(m: string) {
  if (m === 'deep_research') return '深度研究'
  if (m === 'normal') return '普通'
  return m || '普通'
}

// 话术轻量渲染: 转义 HTML → **加粗** 转 <strong> → 换行转 <br>
function renderAnswer(text: string): string {
  // 先去掉问财内嵌图表/模型占位块(```visual{...uuid...}```), 纯文本里是噪音
  const cleaned = (text || '')
    .replace(/```\s*visual[\s\S]*?```/gi, '')
    .replace(/```[\s\S]*?```/g, (m) => (/"uuid"\s*:/.test(m) ? '' : m))
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  const esc = cleaned
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
}

function hasConclusion(c: WencaiConclusion | undefined): boolean {
  return !!c && !!(c.stock || c.buy || c.takeProfit || c.stopLoss || c.logic || c.risk)
}

// ── 研判决策卡: 价位提取/清洗 (与扩展 common.js 同口径) + 距现价% ──
function extractPrice(text: string): { lo: number, hi: number } | null {
  const t = (text || '').replace(/[,，]/g, '')
  let m = t.match(/(\d{2,5}(?:\.\d+)?)\s*[—~\-–]\s*(\d{2,5}(?:\.\d+)?)\s*元/)
  if (m) return { lo: +m[1], hi: +m[2] }
  m = t.match(/(\d{2,5}(?:\.\d+)?)\s*元/)
  if (m) return { lo: +m[1], hi: +m[1] }
  m = t.match(/(\d{2,5}(?:\.\d+)?)\s*[—~–]\s*(\d{2,5}(?:\.\d+)?)/)
  if (m) return { lo: +m[1], hi: +m[2] }
  const re = /(\d{2,5}(?:\.\d+)?)(?!\s*(?:%|％|天|日|周|个?月|年|倍|万|亿|手))/g
  let mm: RegExpExecArray | null
  while ((mm = re.exec(t))) { const v = +mm[1]; if (v >= 2 && v <= 100000) return { lo: v, hi: v } }
  return null
}
function cleanConcVal(s: string | undefined, label: string): string {
  let v = String(s || '').replace(/[*`]/g, '').trim()
  v = v.replace(/^\|+/, '').replace(/\|+$/, '').trim()
  if (label) v = v.replace(new RegExp('^' + label + '\\s*[|｜:：]?\\s*'), '')
  return v.replace(/\s*\|\s*/g, ' ').replace(/\s+/g, ' ').trim()
}
const fmtN = (v: number) => Number.isInteger(v) ? String(v) : String(+v.toFixed(2))
const signPct = (p: number) => (p >= 0 ? '+' : '−') + Math.abs(p).toFixed(1) + '%'

// 主推股现价(用于算距现价%), 按代码从 /quote 拉, 存 map
const priceMap = ref<Record<string, number>>({})
function priceOf(op: WencaiOpinion): number | null {
  const code = recStocks(op)[0]?.code
  return code && priceMap.value[code] != null ? priceMap.value[code] : null
}
async function fetchPrices() {
  const codes = [...new Set(opinions.value.flatMap((o) => recStocks(o).map((s) => s.code)).filter(Boolean))]
  await Promise.all(codes.map(async (code) => {
    try {
      const r = await fetch('/api/wencai/quote?code=' + encodeURIComponent(code))
      if (!r.ok) return
      const q = await r.json()
      if (q && q.price) priceMap.value = { ...priceMap.value, [code]: +q.price }
    } catch { /* 现价拉不到就只显示价位不显示% */ }
  }))
}

interface DTile { cls: string; cn: string; en: string; num?: string; delta?: string; dir?: string; barW?: number; cap: string; only?: boolean }
function tilesOf(op: WencaiOpinion): DTile[] {
  const c = op.conclusion || {}
  const cur = priceOf(op)
  const defs: [string, string, string, string | undefined][] = [
    ['buy', '买入', 'BUY', c.buy], ['tp', '止盈', 'TARGET', c.takeProfit], ['sl', '止损', 'STOP', c.stopLoss],
  ]
  return defs.map(([cls, cn, en, raw]) => {
    const v = cleanConcVal(raw, cn)
    const pr = v ? extractPrice(v) : null
    if (!pr) return { cls, cn, en, cap: v, only: true }
    const num = pr.lo === pr.hi ? fmtN(pr.lo) : fmtN(pr.lo) + '–' + fmtN(pr.hi)
    const t: DTile = { cls, cn, en, num, cap: v }
    if (cur) {
      const loP = (pr.lo - cur) / cur * 100, hiP = (pr.hi - cur) / cur * 100, mid = (loP + hiP) / 2
      t.dir = mid >= 0 ? 'up' : 'down'
      t.delta = (mid >= 0 ? '↑ ' : '↓ ') + (pr.lo === pr.hi ? signPct(loP) : signPct(loP) + '~' + signPct(hiP))
      t.barW = Math.max(6, Math.min(100, Math.abs(mid) / 10 * 100))
    }
    return t
  })
}

async function removeOpinion(id: number) {
  try {
    await deleteWencaiOpinion(id)
    opinions.value = opinions.value.filter((o) => o.id !== id)
    message.success('已删除')
  } catch {
    message.error('删除失败')
  }
}

// 只取"最终推荐标的"(primary), 不展示话术里顺带提到的其它个股 —— 避免噪音干扰判断(v1.7.667)
function recStocks(op: WencaiOpinion) {
  return (op.stocks || []).filter((s) => s.primary)
}

async function addStocksToPool(op: WencaiOpinion) {
  const picks = recStocks(op).map((s) => ({ code: s.code, name: s.name }))
  if (!picks.length) return
  addingId.value = op.id
  try {
    const r = await addWencaiToPool(picks)
    message.success(`已加入自选 ${r.added} 只`)
  } catch {
    message.error('加自选失败')
  } finally {
    addingId.value = null
  }
}

const hasData = computed(() => opinions.value.length > 0)

onMounted(load)
</script>

<template>
  <div class="opinion-view">
    <div class="head">
      <div class="title-wrap">
        <h2>问财观点参考</h2>
        <NButton size="small" secondary :loading="loading" @click="load">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          刷新
        </NButton>
      </div>
      <div class="note">
        <NIcon :component="BulbOutline" />
        <span>同花顺问财 chat「智能调度」的投顾式推荐存档。这是 <b>LLM 投顾观点</b>,同一问题不同时候答案会变,<b>非回测背书的买卖信号</b>,仅供参考。由本地油猴脚本手动问一句后上报。</span>
      </div>
    </div>

    <FilterPanel v-if="loaded && hasData">
    <div class="filter-bar">
      <div class="filter-fields">
        <div class="filter-item" style="min-width: 200px">
          <label for="op-keyword">关键词</label>
          <NInput
            v-model:value="filterKeyword"
            size="small"
            clearable
            placeholder="问题/个股/关键词"
            :input-props="{ id: 'op-keyword', name: 'keyword', type: 'search' }"
          />
        </div>
        <div class="filter-item" style="min-width: 220px">
          <label>时间段</label>
          <NDatePicker
            v-model:value="filterDateRange"
            type="daterange"
            size="small"
            clearable
            format="yyyy-MM-dd"
            placement="bottom"
            to="body"
          />
        </div>
        <div v-if="modeOptions.length > 1" class="filter-item">
          <label>模式</label>
          <NSelect
            v-model:value="filterMode"
            :options="modeOptions"
            size="small"
            clearable
            placeholder="全部"
          />
        </div>
        <div v-if="uploaderOptions.length > 1" class="filter-item">
          <label>上报人</label>
          <NSelect
            v-model:value="filterUploader"
            :options="uploaderOptions"
            size="small"
            clearable
            placeholder="全部"
          />
        </div>
      </div>
      <div class="filter-actions">
        <NButton size="small" secondary @click="resetFilters">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          重置
        </NButton>
      </div>
    </div>
    </FilterPanel>

    <div v-if="!loaded" class="cards">
      <NSkeleton v-for="i in 3" :key="i" height="120px" style="margin-bottom:12px;border-radius:10px" />
    </div>

    <NEmpty v-else-if="!hasData" description="还没有问财观点" class="empty">
      <template #extra>
        <div class="empty-hint">在浏览器登录 iwencai 后, 用「问财观点上报」油猴脚本手动问一句, 结果会出现在这里。</div>
      </template>
    </NEmpty>

    <div v-else class="cards">
      <NEmpty v-if="!filteredOpinions.length" description="没有匹配的观点" class="empty" />
      <div v-for="op in filteredOpinions" :key="op.id" class="op-card">
        <div class="op-top">
          <div class="q">{{ op.question }}</div>
          <div class="meta">
            <NTag v-if="op.uploader" size="small" type="info" round>{{ op.uploader }}</NTag>
            <NTag size="small" :type="op.agent_mode === 'deep_research' ? 'warning' : 'default'" round>
              {{ modeLabel(op.agent_mode) }}
            </NTag>
            <span class="time">{{ fmtTime(op.created_at) }}</span>
            <NPopconfirm @positive-click="removeOpinion(op.id)">
              <template #trigger>
                <NButton size="tiny" quaternary circle>
                  <template #icon><NIcon :component="TrashOutline" /></template>
                </NButton>
              </template>
              删除这条观点?
            </NPopconfirm>
          </div>
        </div>

        <!-- 研判决策卡: 主推 hero + 买入/止盈/止损价位磁贴(距现价%) + 逻辑/风险 (与扩展/详情页一套观感) -->
        <div v-if="hasConclusion(op.conclusion)" class="dcard">
          <div class="dc-top">
            <div class="dc-l">
              <div class="dc-eye">主推标的 · Top Pick</div>
              <div v-if="recStocks(op)[0]" class="dc-name">
                <span class="nm" role="button" tabindex="0"
                      @click="ui.openStock(recStocks(op)[0].code, recStocks(op)[0].name)"
                      @keydown.enter="ui.openStock(recStocks(op)[0].code, recStocks(op)[0].name)">{{ recStocks(op)[0].name }}</span>
                <span class="cd">{{ recStocks(op)[0].code }}</span>
                <span v-if="priceOf(op) != null" class="cur">现价 {{ fmtN(priceOf(op)!) }}</span>
              </div>
              <div v-else-if="cleanConcVal(op.conclusion?.stock, '标的')" class="dc-name">
                <span class="nm-plain">{{ cleanConcVal(op.conclusion?.stock, '标的') }}</span>
              </div>
            </div>
            <NButton v-if="recStocks(op).length" size="tiny" secondary type="primary" class="dc-add"
                     :loading="addingId === op.id" @click="addStocksToPool(op)">
              <template #icon><NIcon :component="AddCircleOutline" /></template>加自选
            </NButton>
          </div>
          <div class="dc-tiles">
            <div v-for="t in tilesOf(op)" :key="t.cls" class="dtile" :class="t.cls">
              <div class="dt-k">{{ t.cn }} <i>{{ t.en }}</i></div>
              <template v-if="t.only"><div class="dt-only">{{ t.cap || '—' }}</div></template>
              <template v-else>
                <div class="dt-num">{{ t.num }}<span v-if="t.delta" class="dt-d" :class="t.dir">{{ t.delta }}</span></div>
                <div v-if="t.barW" class="dt-bar"><i :style="{ width: t.barW + '%' }"></i></div>
                <div class="dt-cap">{{ t.cap }}</div>
              </template>
            </div>
          </div>
          <div v-if="cleanConcVal(op.conclusion?.logic,'逻辑') || cleanConcVal(op.conclusion?.risk,'风险')" class="dthesis">
            <div v-if="cleanConcVal(op.conclusion?.logic,'逻辑')" class="dth"><b>逻辑</b>{{ cleanConcVal(op.conclusion?.logic,'逻辑') }}</div>
            <div v-if="cleanConcVal(op.conclusion?.risk,'风险')" class="dth risk"><b>风险</b>{{ cleanConcVal(op.conclusion?.risk,'风险') }}</div>
          </div>
        </div>

        <!-- 无结论但有推荐个股时的兜底: 仍给出个股 + 加自选 -->
        <div v-else-if="recStocks(op).length" class="rec-stocks">
          <span class="rl">推荐标的</span>
          <NTag v-for="s in recStocks(op)" :key="s.code" size="small" type="primary" class="stk" @click="ui.openStock(s.code, s.name)">
            {{ s.name }}<span class="code">{{ s.code }}</span>
          </NTag>
          <NButton size="tiny" secondary type="primary" class="add-btn" :loading="addingId === op.id" @click="addStocksToPool(op)">
            <template #icon><NIcon :component="AddCircleOutline" /></template>加自选
          </NButton>
        </div>

        <NCollapse class="ans-collapse">
          <NCollapseItem title="展开完整分析" name="ans">
            <div class="answer" v-html="renderAnswer(op.answer_text)"></div>
          </NCollapseItem>
          <NCollapseItem v-if="op.reasoning" title="思考过程" name="rz">
            <div class="reasoning">{{ op.reasoning }}</div>
          </NCollapseItem>
        </NCollapse>
      </div>
    </div>
  </div>
</template>

<style scoped>
.opinion-view { padding: 16px; max-width: 900px; margin: 0 auto; }
.head { margin-bottom: 16px; }
.title-wrap { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.title-wrap h2 { margin: 0; font-size: 18px; }
.note {
  display: flex; align-items: flex-start; gap: 6px;
  font-size: 12px; color: var(--fg-subtle); line-height: 1.6;
  background: color-mix(in srgb, var(--warn-fg) 8%, transparent); padding: 8px 10px; border-radius: 8px;
}
.note b { color: var(--warn-fg); }
.filter-bar {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px 24px;
  align-items: end;
}
.filter-fields { display: flex; gap: 12px; flex-wrap: wrap; }
.filter-item {
  display: flex; flex-direction: column; gap: 4px;
  flex: 1; min-width: 120px;
}
.filter-item label { font-size: 12px; color: var(--fg-subtle); white-space: nowrap; }
.filter-actions { display: flex; gap: 8px; align-items: flex-end; justify-content: flex-end; }
.cards { display: flex; flex-direction: column; gap: 14px; }
/* 真卡片: 描边 + 双层柔和投影 + hover 抬升 (v1.7.667) */
.op-card {
  border: 1px solid var(--border-default); border-radius: 12px;
  padding: 14px 16px; background: var(--bg-surface);
  box-shadow: 0 1px 2px rgba(20,30,50,.05), 0 4px 14px rgba(20,30,50,.04);
  transition: box-shadow .18s, border-color .18s;
}
.op-card:hover { box-shadow: 0 2px 6px rgba(20,30,50,.08), 0 10px 30px rgba(20,30,50,.07); border-color: var(--border-hard); }
/* 问题=上下文, 弱化为次要; 结论速览才是焦点 */
.op-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.q { font-weight: 600; font-size: 13px; line-height: 1.5; flex: 1; color: var(--fg-muted); }
.meta { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.time { font-size: 11px; color: var(--fg-subtle); }

/* 研判决策卡: 主推 hero + 价位磁贴(距现价%) + 逻辑/风险 (与扩展/详情页一套观感) */
.dcard { margin: 12px 0 0; padding: 14px; border: 1px solid var(--border-default); border-radius: 12px; background: var(--bg-surface); }
.dc-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.dc-eye { font-size: 10px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; color: var(--fg-subtle); }
.dc-name { margin-top: 5px; display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; }
.dc-name .nm { font-size: 22px; font-weight: 800; letter-spacing: -.01em; color: var(--fg-default); cursor: pointer; }
.dc-name .nm:hover { color: var(--accent-fg); }
.dc-name .nm-plain { font-size: 20px; font-weight: 800; color: var(--fg-default); }
.dc-name .cd { font-size: 13px; font-weight: 600; color: var(--fg-subtle); font-family: var(--font-mono); }
.dc-name .cur { font-size: 12px; color: var(--fg-muted); font-weight: 600; }
.dc-add { flex-shrink: 0; }
.dc-tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 12px; }
.dtile { background: color-mix(in srgb, var(--fg-default) 4%, transparent); border: 1px solid var(--border-muted); border-radius: 11px; padding: 11px 12px; }
.dt-k { font-size: 10px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; color: var(--fg-subtle); }
.dt-k i { font-style: normal; opacity: .7; margin-left: 4px; }
.dt-num { font-size: 21px; font-weight: 800; color: var(--fg-default); margin-top: 6px; line-height: 1.05; display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; font-variant-numeric: tabular-nums; }
.dt-d { font-size: 12px; font-weight: 800; } .dt-d.up { color: var(--up-fg); } .dt-d.down { color: var(--down-fg); }
.dt-bar { height: 4px; border-radius: 99px; margin-top: 9px; background: var(--border-muted); overflow: hidden; } .dt-bar i { display: block; height: 100%; }
.dtile.buy .dt-bar i { background: var(--down-fg); } .dtile.tp .dt-bar i { background: var(--accent-fg); } .dtile.sl .dt-bar i { background: var(--up-fg); }
.dt-cap { font-size: 11.5px; color: var(--fg-muted); line-height: 1.5; margin-top: 9px; }
.dt-only { font-size: 13px; color: var(--fg-default); font-weight: 600; margin-top: 6px; line-height: 1.5; }
.dthesis { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.dth { font-size: 12.5px; line-height: 1.6; color: var(--fg-default); padding: 9px 11px; border-radius: 9px; background: var(--accent-bg-muted); }
.dth.risk { background: color-mix(in srgb, var(--up-fg) 10%, transparent); }
.dth b { color: var(--down-fg); font-weight: 800; margin-right: 7px; } .dth.risk b { color: var(--up-fg); }

/* 推荐标的: 只显最终推荐 */
.rec-stocks { margin-top: 10px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.rec-stocks .rl { font-size: 12px; color: var(--fg-subtle); font-weight: 600; }
.stk { cursor: pointer; }
.stk .code { margin-left: 4px; opacity: 0.6; font-size: 11px; }
.add-btn { margin-left: 4px; }

.ans-collapse { margin-top: 8px; }
.answer { font-size: 13px; line-height: 1.7; white-space: normal; word-break: break-word; }
.reasoning { font-size: 12px; line-height: 1.7; color: var(--fg-subtle); white-space: pre-wrap; word-break: break-word; }

@media (max-width: 768px) {
  .opinion-view { padding: 12px; }
  .op-top { flex-direction: column; gap: 6px; }
  .meta { align-self: flex-end; }
  .filter-bar { grid-template-columns: 1fr; }
  .filter-item { min-width: 140px; }
  .filter-actions { justify-content: flex-start; }
  .dc-tiles { grid-template-columns: 1fr; }   /* 手机单列 */
  .dc-top { flex-wrap: wrap; }
}
</style>
