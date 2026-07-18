<script setup lang="ts">
// 交易日记 (v1.7.669): 手动记录每笔买卖的理由/心态/复盘, 事后回看决策模式。与交易分析(客观数据)互补。
import { computed, onMounted, ref } from 'vue'
import {
  NButton, NIcon, NTag, NModal, NInput, NInputNumber, NSelect, NDatePicker,
  NPopconfirm, NEmpty, NSkeleton,
} from 'naive-ui'
import { AddOutline, CreateOutline, TrashOutline, JournalOutline } from '@vicons/ionicons5'
import { fetchJournal, createJournal, updateJournal, deleteJournal, type JournalEntry } from '../api/trade-journal'
import FilterPanel from '../components/common/FilterPanel.vue'
import { useGlobalMessage } from '../composables/useGlobalMessage'

const message = useGlobalMessage()
const list = ref<JournalEntry[]>([])
const loading = ref(false)

const kw = ref('')
const sideFilter = ref<string | null>(null)
const SIDE_OPTS = [
  { label: '全部', value: null as any },
  { label: '买入', value: 'buy' }, { label: '卖出', value: 'sell' },
  { label: '持有', value: 'hold' }, { label: '笔记', value: 'note' },
]
const EMOTION_OPTS = ['冷静执行', '纪律', '贪婪追高', '恐惧割肉', '犹豫踏空', '冲动', '侥幸', '其它']
  .map((e) => ({ label: e, value: e }))

const sideMeta: Record<string, { txt: string; type: any }> = {
  buy: { txt: '买入', type: 'error' }, sell: { txt: '卖出', type: 'success' },
  hold: { txt: '持有', type: 'warning' }, note: { txt: '笔记', type: 'default' },
}

async function load() {
  loading.value = true
  try { list.value = await fetchJournal() }
  catch { message.error('加载交易日记失败') }
  finally { loading.value = false }
}
onMounted(load)

const filtered = computed(() => {
  const k = kw.value.trim().toLowerCase()
  return list.value.filter((e) => {
    if (sideFilter.value && e.side !== sideFilter.value) return false
    if (k && !(`${e.code}${e.name}${e.reason || ''}${e.review || ''}`.toLowerCase().includes(k))) return false
    return true
  })
})

// ── 新增/编辑弹窗 ──
const showEdit = ref(false)
const editId = ref<number | null>(null)
const form = ref<Partial<JournalEntry>>({})
const dateTs = ref<number | null>(null)

function openNew() {
  editId.value = null
  form.value = { side: 'buy', emotion: '冷静执行' }
  dateTs.value = Date.now()
  showEdit.value = true
}
function openEdit(e: JournalEntry) {
  editId.value = e.id
  form.value = { ...e }
  dateTs.value = e.trade_date ? new Date(e.trade_date + 'T00:00:00').getTime() : null
  showEdit.value = true
}
function tsToDay(ts: number | null): string | null {
  if (ts == null) return null
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const saving = ref(false)
async function save() {
  saving.value = true
  const body = { ...form.value, trade_date: tsToDay(dateTs.value) }
  try {
    if (editId.value == null) await createJournal(body as any)
    else await updateJournal(editId.value, body as any)
    showEdit.value = false
    message.success('已保存')
    await load()
  } catch { message.error('保存失败') }
  finally { saving.value = false }
}
async function remove(e: JournalEntry) {
  try { await deleteJournal(e.id); list.value = list.value.filter((x) => x.id !== e.id); message.success('已删除') }
  catch { message.error('删除失败') }
}
</script>

<template>
  <div class="tj-view">
    <div class="tj-head">
      <div class="title-wrap">
        <h2>交易日记</h2>
        <NButton size="small" type="primary" @click="openNew">
          <template #icon><NIcon :component="AddOutline" /></template>写一条
        </NButton>
      </div>
      <div class="note"><NIcon :component="JournalOutline" /><span>记下每笔买卖的<b>理由 · 心态 · 复盘</b>，事后回看自己的决策模式。与「交易分析」的客观数据互补。</span></div>
    </div>

    <FilterPanel v-if="!loading && list.length">
      <div class="filter-bar">
        <div class="filter-fields">
          <div class="filter-item"><label>关键词</label><NInput v-model:value="kw" size="small" clearable placeholder="代码/名称/理由/复盘" /></div>
          <div class="filter-item"><label>方向</label><NSelect v-model:value="sideFilter" :options="SIDE_OPTS" size="small" clearable placeholder="全部" /></div>
        </div>
      </div>
    </FilterPanel>

    <div v-if="loading" class="list"><NSkeleton v-for="i in 3" :key="i" height="96px" style="margin-bottom:12px;border-radius:10px" /></div>
    <NEmpty v-else-if="!list.length" description="还没有交易日记" class="empty">
      <template #extra><NButton size="small" @click="openNew">写第一条</NButton></template>
    </NEmpty>
    <NEmpty v-else-if="!filtered.length" description="没有匹配的记录" class="empty" />

    <div v-else class="list">
      <div v-for="e in filtered" :key="e.id" class="tj-card" :class="'s-' + e.side">
        <div class="tj-card-head">
          <NTag size="small" :type="sideMeta[e.side]?.type || 'default'" :bordered="false">{{ sideMeta[e.side]?.txt || e.side || '记录' }}</NTag>
          <span v-if="e.name || e.code" class="stk"><b>{{ e.name }}</b><span class="cd">{{ e.code }}</span></span>
          <span v-if="e.price != null" class="px">¥{{ e.price }}<span v-if="e.qty != null" class="qty"> × {{ e.qty }}</span></span>
          <span class="sp" />
          <NTag v-if="e.emotion" size="tiny" :bordered="false" class="emo">{{ e.emotion }}</NTag>
          <span class="date">{{ e.trade_date || (e.created_at || '').slice(0, 10) }}</span>
          <NButton size="tiny" quaternary circle @click="openEdit(e)"><template #icon><NIcon :component="CreateOutline" /></template></NButton>
          <NPopconfirm @positive-click="remove(e)">
            <template #trigger><NButton size="tiny" quaternary circle><template #icon><NIcon :component="TrashOutline" /></template></NButton></template>
            删除这条日记?
          </NPopconfirm>
        </div>
        <div v-if="e.reason" class="tj-reason">{{ e.reason }}</div>
        <div v-if="e.review" class="tj-review"><span class="rl">复盘</span>{{ e.review }}</div>
      </div>
    </div>

    <NModal v-model:show="showEdit" preset="card" :title="editId == null ? '写一条交易日记' : '编辑交易日记'" style="width:560px;max-width:94vw">
      <div class="form">
        <div class="frow">
          <div class="ff"><label>方向</label><NSelect v-model:value="form.side" :options="SIDE_OPTS.slice(1)" size="small" /></div>
          <div class="ff"><label>日期</label><NDatePicker v-model:value="dateTs" type="date" size="small" clearable style="width:100%" /></div>
        </div>
        <div class="frow">
          <div class="ff"><label>代码</label><NInput v-model:value="form.code" size="small" clearable placeholder="如 300033" /></div>
          <div class="ff"><label>名称</label><NInput v-model:value="form.name" size="small" clearable placeholder="如 同花顺" /></div>
        </div>
        <div class="frow">
          <div class="ff"><label>价格</label><NInputNumber v-model:value="form.price" size="small" :min="0" :step="0.01" style="width:100%" placeholder="选填" /></div>
          <div class="ff"><label>股数</label><NInputNumber v-model:value="form.qty" size="small" :min="0" :step="100" style="width:100%" placeholder="选填" /></div>
        </div>
        <div class="ff"><label>理由（为什么买/卖）</label><NInput v-model:value="form.reason" type="textarea" size="small" :rows="2" placeholder="触发了什么买点/卖点、当时怎么判断的…" /></div>
        <div class="ff"><label>心态</label><NSelect v-model:value="form.emotion" :options="EMOTION_OPTS" size="small" filterable tag placeholder="选或自定义" /></div>
        <div class="ff"><label>复盘（结果/教训，可事后补）</label><NInput v-model:value="form.review" type="textarea" size="small" :rows="2" placeholder="做对/做错了什么，下次怎么改…" /></div>
      </div>
      <template #footer>
        <div style="display:flex;justify-content:flex-end;gap:8px">
          <NButton size="small" @click="showEdit = false">取消</NButton>
          <NButton size="small" type="primary" :loading="saving" @click="save">保存</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.tj-view { max-width: 900px; margin: 0 auto; }
.tj-head { margin-bottom: 14px; }
.title-wrap { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.title-wrap h2 { margin: 0; font-size: 18px; }
.note { display: flex; align-items: flex-start; gap: 6px; font-size: 12px; color: var(--fg-subtle); line-height: 1.6; background: var(--accent-bg-muted); padding: 8px 10px; border-radius: 8px; }
.note b { color: var(--fg-default); }

.filter-bar { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 8px; padding: 12px 16px; margin-bottom: 14px; }
.filter-fields { display: flex; gap: 12px; flex-wrap: wrap; }
.filter-item { display: flex; flex-direction: column; gap: 4px; min-width: 140px; }
.filter-item label { font-size: 12px; color: var(--fg-subtle); }

.list { display: flex; flex-direction: column; gap: 12px; }
.tj-card { border: 1px solid var(--border-default); border-radius: 10px; padding: 12px 14px; background: var(--bg-surface); box-shadow: 0 1px 2px rgba(20,30,50,.04); border-left: 3px solid var(--border-muted); }
.tj-card.s-buy { border-left-color: var(--up-fg); }
.tj-card.s-sell { border-left-color: var(--down-fg); }
.tj-card.s-hold { border-left-color: var(--warn-fg); }
.tj-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stk b { font-size: 14px; } .stk .cd { font-family: var(--font-mono); font-size: 11px; color: var(--fg-subtle); margin-left: 5px; }
.px { font-family: var(--font-mono); font-size: 13px; color: var(--fg-muted); } .px .qty { color: var(--fg-subtle); }
.sp { flex: 1; }
.emo { color: var(--tide-deep); background: var(--tide-bg-muted); }
.date { font-family: var(--font-mono); font-size: 11px; color: var(--fg-subtle); }
.tj-reason { margin-top: 8px; font-size: 13px; line-height: 1.6; color: var(--fg-default); }
.tj-review { margin-top: 6px; font-size: 12px; line-height: 1.6; color: var(--fg-muted); background: var(--bg-sunken); border-radius: 6px; padding: 6px 10px; }
.tj-review .rl { font-size: 11px; color: var(--fg-subtle); font-weight: 600; margin-right: 6px; }
.empty { margin-top: 30px; }

.form { display: flex; flex-direction: column; gap: 12px; }
.frow { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.ff { display: flex; flex-direction: column; gap: 5px; }
.ff label { font-size: 12px; color: var(--fg-muted); font-weight: 500; }

@media (max-width: 768px) {
  .filter-item { min-width: 0; flex: 1; }
  .frow { grid-template-columns: 1fr; }
}
</style>
