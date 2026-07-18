<script setup lang="ts">
// 预警中心 (v1.7.668): 集中管理所有自定义到价/涨跌幅/均线预警(引擎 custom_alert_scanner 已存在)。
// 预警在各股(股票池行/K线页)创建, 本页统一查看/开关/重启/删除。
import { computed, onMounted, ref } from 'vue'
import { NButton, NIcon, NTag, NSwitch, NPopconfirm, NEmpty, NSkeleton, NInput, NSelect } from 'naive-ui'
import { RefreshOutline, TrashOutline, NotificationsOutline } from '@vicons/ionicons5'
import { fetchAllAlerts, updateAlert, deleteAlert, type StockAlert, type AlertCondition } from '../api/stocks'
import FilterPanel from '../components/common/FilterPanel.vue'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { useUiStore } from '../stores/ui'

const message = useGlobalMessage()
const ui = useUiStore()
const alerts = ref<StockAlert[]>([])
const loading = ref(false)
const busyId = ref<number | null>(null)

const kw = ref('')
const statusFilter = ref<'active' | 'triggered' | null>(null)
const statusOptions = [
  { label: '全部状态', value: null as any },
  { label: '生效中', value: 'active' },
  { label: '已触发', value: 'triggered' },
]

async function load() {
  loading.value = true
  try { alerts.value = await fetchAllAlerts() }
  catch { message.error('加载预警失败') }
  finally { loading.value = false }
}
onMounted(load)

const filtered = computed(() => {
  const k = kw.value.trim().toLowerCase()
  return alerts.value.filter((a) => {
    if (k && !(a.code.toLowerCase().includes(k) || (a.note || '').toLowerCase().includes(k))) return false
    if (statusFilter.value && a.status !== statusFilter.value) return false
    return true
  })
})
const activeCount = computed(() => alerts.value.filter((a) => a.status === 'active' && a.enabled).length)

// 条件 → 人类可读
function condLabel(c: AlertCondition): string {
  const opTxt = c.op === 'gte' ? '≥' : '≤'
  if (c.dim === 'price') return `现价 ${opTxt} ¥${c.value}`
  if (c.dim === 'pct') return `涨跌幅 ${opTxt} ${c.value}%`
  if (c.dim === 'ma_near') return `贴近 MA${c.ma}${c.band ? ` (±${c.band}%)` : ''}`
  if (c.dim === 'ma_cross') return `${c.dir === 'up' ? '上穿' : '下穿'} MA${c.ma}`
  return String(c.dim)
}
function fmtTime(raw?: string | null): string { return !raw ? '' : raw.replace('T', ' ').slice(5, 16) }

async function toggleEnabled(a: StockAlert, on: boolean) {
  busyId.value = a.id
  try { await updateAlert(a.id, { enabled: on ? 1 : 0 }); a.enabled = on ? 1 : 0 }
  catch { message.error('操作失败'); }
  finally { busyId.value = null }
}
async function reactivate(a: StockAlert) {
  busyId.value = a.id
  try { await updateAlert(a.id, { status: 'active', enabled: 1 }); a.status = 'active'; a.enabled = 1; message.success('已重新启用') }
  catch { message.error('操作失败') }
  finally { busyId.value = null }
}
async function remove(a: StockAlert) {
  busyId.value = a.id
  try { await deleteAlert(a.id); alerts.value = alerts.value.filter((x) => x.id !== a.id); message.success('已删除') }
  catch { message.error('删除失败') }
  finally { busyId.value = null }
}
</script>

<template>
  <div class="ac-view">
    <div class="ac-head">
      <div class="title-wrap">
        <h2>预警中心</h2>
        <NButton size="small" secondary :loading="loading" @click="load">
          <template #icon><NIcon :component="RefreshOutline" /></template>刷新
        </NButton>
      </div>
      <div class="note">
        <NIcon :component="NotificationsOutline" />
        <span>自定义到价/涨跌幅/均线预警集中管理。预警在<b>股票池行或个股K线页</b>创建；触发一次即失效（可在此重新启用）。生效中 <b>{{ activeCount }}</b> 条。</span>
      </div>
    </div>

    <FilterPanel v-if="!loading && alerts.length">
      <div class="filter-bar">
        <div class="filter-fields">
          <div class="filter-item"><label>关键词</label><NInput v-model:value="kw" size="small" clearable placeholder="代码/备注" /></div>
          <div class="filter-item"><label>状态</label><NSelect v-model:value="statusFilter" :options="statusOptions" size="small" clearable placeholder="全部状态" /></div>
        </div>
      </div>
    </FilterPanel>

    <div v-if="loading" class="list"><NSkeleton v-for="i in 3" :key="i" height="76px" style="margin-bottom:10px;border-radius:8px" /></div>
    <NEmpty v-else-if="!alerts.length" description="还没有自定义预警" class="empty">
      <template #extra><div class="empty-hint">去股票池点某只股票的「设预警」，或在个股 K 线页设置到价/均线提醒。</div></template>
    </NEmpty>
    <NEmpty v-else-if="!filtered.length" description="没有匹配的预警" class="empty" />

    <div v-else class="list">
      <div v-for="a in filtered" :key="a.id" class="ac-item" :class="{ off: !a.enabled, trig: a.status === 'triggered' }">
        <div class="ac-main">
          <div class="ac-top">
            <span class="code" role="button" tabindex="0" @click="ui.openStock(a.code, a.code)">{{ a.code }}</span>
            <NTag v-if="a.preset" size="tiny" :bordered="false" type="info">{{ a.preset.toUpperCase() }}</NTag>
            <NTag v-if="a.status === 'triggered'" size="tiny" :bordered="false" type="warning">已触发{{ a.triggered_price ? ` @¥${a.triggered_price}` : '' }}{{ a.last_triggered_at ? ` · ${fmtTime(a.last_triggered_at)}` : '' }}</NTag>
            <NTag v-if="a.repeat_daily" size="tiny" :bordered="false">每日一次</NTag>
          </div>
          <div class="ac-conds">
            <span v-for="(c, i) in a.conditions" :key="i" class="cond">{{ condLabel(c) }}</span>
            <span v-if="a.conditions.length > 1" class="cond-and">全部满足</span>
          </div>
          <div v-if="a.note" class="ac-note">{{ a.note }}</div>
        </div>
        <div class="ac-actions">
          <NSwitch :value="!!a.enabled" size="small" :loading="busyId === a.id" :disabled="a.status === 'triggered'"
                   @update:value="(v: boolean) => toggleEnabled(a, v)" />
          <NButton v-if="a.status === 'triggered'" size="tiny" type="primary" secondary :loading="busyId === a.id" @click="reactivate(a)">重新启用</NButton>
          <NPopconfirm @positive-click="remove(a)">
            <template #trigger><NButton size="tiny" quaternary circle :loading="busyId === a.id"><template #icon><NIcon :component="TrashOutline" /></template></NButton></template>
            删除这条预警?
          </NPopconfirm>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ac-view { max-width: 900px; margin: 0 auto; }
.ac-head { margin-bottom: 14px; }
.title-wrap { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.title-wrap h2 { margin: 0; font-size: 18px; }
.note { display: flex; align-items: flex-start; gap: 6px; font-size: 12px; color: var(--fg-subtle); line-height: 1.6; background: var(--accent-bg-muted); padding: 8px 10px; border-radius: 8px; }
.note b { color: var(--fg-default); }

.filter-bar { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 8px; padding: 12px 16px; margin-bottom: 14px; }
.filter-fields { display: flex; gap: 12px; flex-wrap: wrap; }
.filter-item { display: flex; flex-direction: column; gap: 4px; min-width: 140px; }
.filter-item label { font-size: 12px; color: var(--fg-subtle); }

.list { display: flex; flex-direction: column; gap: 10px; }
.ac-item { display: flex; align-items: center; gap: 12px; border: 1px solid var(--border-default); border-radius: 8px; padding: 11px 14px; background: var(--bg-surface); box-shadow: 0 1px 2px rgba(20,30,50,.04); transition: border-color .15s; }
.ac-item:hover { border-color: var(--border-hard); }
.ac-item.off { opacity: .6; }
.ac-item.trig { border-left: 3px solid var(--warn-fg); }
.ac-main { flex: 1; min-width: 0; }
.ac-top { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin-bottom: 6px; }
.code { font-family: var(--font-mono); font-weight: 700; font-size: 14px; color: var(--accent-fg); cursor: pointer; }
.code:hover { text-decoration: underline; }
.ac-conds { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.cond { font-family: var(--font-mono); font-size: 12px; color: var(--fg-default); background: var(--bg-sunken); border: 1px solid var(--border-muted); border-radius: 4px; padding: 1px 8px; }
.cond-and { font-size: 11px; color: var(--fg-subtle); }
.ac-note { margin-top: 5px; font-size: 11px; color: var(--fg-subtle); }
.ac-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.empty { margin-top: 30px; }
.empty-hint { font-size: 12px; color: var(--fg-subtle); }

@media (max-width: 768px) {
  .ac-item { flex-direction: column; align-items: stretch; gap: 10px; }
  .ac-actions { justify-content: flex-end; }
  .filter-item { min-width: 0; flex: 1; }
}
</style>
