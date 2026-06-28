<script setup lang="ts">
// 个股自定义预警弹窗: 列出该票预警 + 新增/编辑(多条件 AND) + 启停/重启/删除。
// 维度: 价格 / 当日涨跌幅 / 接近均线(±带) / 上穿·跌破均线。一只票可挂多条(任一各自触发)。
import { ref, watch, computed } from 'vue'
import { NModal, NButton, NSelect, NInputNumber, NInput, NSwitch, NTag, NIcon, NEmpty, NSpin, NPopconfirm } from 'naive-ui'
import { AddOutline, TrashOutline, CreateOutline, RefreshOutline } from '@vicons/ionicons5'
import { useGlobalMessage } from '../../composables/useGlobalMessage'
import { useResponsive } from '../../composables/useResponsive'
import {
  fetchStockAlerts, createAlert, updateAlert, deleteAlert,
  type StockAlert, type AlertCondition, type AlertDim,
} from '../../api/stocks'

const props = defineProps<{ show: boolean; code: string; name: string }>()
const emit = defineEmits<{ 'update:show': [boolean]; changed: [] }>()

const message = useGlobalMessage()
const { isPhone } = useResponsive()

const loading = ref(false)
const saving = ref(false)
const list = ref<StockAlert[]>([])

// ── 编辑中的草稿(新增或编辑一条预警) ──
const editingId = ref<number | null>(null)   // null=新增, 数字=编辑该 id
const draftConditions = ref<AlertCondition[]>([])
const draftNote = ref('')

const DIM_OPTIONS = [
  { label: '价格', value: 'price' },
  { label: '当日涨跌幅', value: 'pct' },
  { label: '接近均线', value: 'ma_near' },
  { label: '上穿/跌破均线', value: 'ma_cross' },
]
const OP_OPTIONS = [{ label: '≥', value: 'gte' }, { label: '≤', value: 'lte' }]
const MA_OPTIONS = [5, 10, 20, 60].map(v => ({ label: `MA${v}`, value: v }))
const DIR_OPTIONS = [{ label: '上穿', value: 'up' }, { label: '跌破', value: 'down' }]

function blankCondition(dim: AlertDim = 'price'): AlertCondition {
  if (dim === 'price') return { dim, op: 'gte', value: undefined }
  if (dim === 'pct') return { dim, op: 'gte', value: undefined }
  if (dim === 'ma_near') return { dim, ma: 10, band: 2 }
  return { dim, ma: 10, dir: 'up' }
}

function onDimChange(c: AlertCondition, dim: AlertDim) {
  Object.assign(c, blankCondition(dim))
}

function describeCondition(c: AlertCondition): string {
  const op = c.op === 'gte' ? '≥' : '≤'
  if (c.dim === 'price') return `价格${op}${c.value ?? '?'}`
  if (c.dim === 'pct') return `涨跌幅${op}${c.value ?? '?'}%`
  if (c.dim === 'ma_near') return `接近MA${c.ma}(±${c.band}%)`
  if (c.dim === 'ma_cross') return `${c.dir === 'up' ? '上穿' : '跌破'}MA${c.ma}`
  return '?'
}
function describeAlert(a: StockAlert): string {
  return (a.conditions || []).map(describeCondition).join(' 且 ') || '-'
}

function resetDraft() {
  editingId.value = null
  draftConditions.value = [blankCondition('price')]
  draftNote.value = ''
}

async function reload() {
  if (!props.code) return
  loading.value = true
  try {
    list.value = await fetchStockAlerts(props.code)
  } catch {
    message.error('预警加载失败')
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (v) => {
  if (v) { resetDraft(); reload() }
})

function addConditionRow() {
  draftConditions.value.push(blankCondition('price'))
}
function removeConditionRow(i: number) {
  draftConditions.value.splice(i, 1)
  if (draftConditions.value.length === 0) draftConditions.value.push(blankCondition('price'))
}

function validateDraft(): AlertCondition[] | null {
  const out: AlertCondition[] = []
  for (const c of draftConditions.value) {
    if (c.dim === 'price' || c.dim === 'pct') {
      if (c.value == null || isNaN(c.value)) { message.warning('请填写完整的阈值'); return null }
      out.push({ dim: c.dim, op: c.op, value: c.value })
    } else if (c.dim === 'ma_near') {
      if (c.band == null || c.band <= 0) { message.warning('贴线带需大于 0'); return null }
      out.push({ dim: 'ma_near', ma: c.ma, band: c.band })
    } else {
      out.push({ dim: 'ma_cross', ma: c.ma, dir: c.dir })
    }
  }
  return out
}

async function submitDraft() {
  const conditions = validateDraft()
  if (!conditions) return
  saving.value = true
  try {
    if (editingId.value == null) {
      await createAlert(props.code, conditions, draftNote.value.trim())
      message.success('预警已添加')
    } else {
      await updateAlert(editingId.value, { conditions, note: draftNote.value.trim() })
      message.success('预警已更新')
    }
    resetDraft()
    await reload()
    emit('changed')
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

function editAlert(a: StockAlert) {
  editingId.value = a.id
  draftConditions.value = (a.conditions || []).map(c => ({ ...c }))
  if (draftConditions.value.length === 0) draftConditions.value = [blankCondition('price')]
  draftNote.value = a.note || ''
}

async function toggleEnabled(a: StockAlert, val: boolean) {
  try {
    await updateAlert(a.id, { enabled: val ? 1 : 0 })
    a.enabled = val ? 1 : 0
    emit('changed')
  } catch {
    message.error('操作失败')
  }
}

async function restartAlert(a: StockAlert) {
  try {
    await updateAlert(a.id, { status: 'active' })
    message.success('已重新启用')
    await reload()
    emit('changed')
  } catch {
    message.error('操作失败')
  }
}

async function removeAlert(a: StockAlert) {
  try {
    await deleteAlert(a.id)
    await reload()
    emit('changed')
  } catch {
    message.error('删除失败')
  }
}

const modalWidth = computed(() => (isPhone.value ? '94vw' : '600px'))
</script>

<template>
  <NModal
    :show="show"
    @update:show="emit('update:show', $event)"
    preset="card"
    :title="`${code} ${name} · 自定义预警`"
    :style="{ width: modalWidth, maxWidth: '600px' }"
    :block-scroll="false"
  >
    <div class="alert-modal">
      <!-- 已有预警列表 -->
      <div class="section-title">已设预警</div>
      <NSpin :show="loading">
        <div v-if="list.length === 0 && !loading" class="empty-box">
          <NEmpty description="还没有预警, 在下方添加" size="small" />
        </div>
        <div v-for="a in list" :key="a.id" class="alert-row" :class="{ triggered: a.status === 'triggered' }">
          <div class="alert-row-main">
            <div class="alert-cond">{{ describeAlert(a) }}</div>
            <div class="alert-meta">
              <NTag v-if="a.status === 'triggered'" size="tiny" type="warning" :bordered="false">已触发 · 已停用</NTag>
              <NTag v-else-if="a.enabled" size="tiny" type="success" :bordered="false">生效中</NTag>
              <NTag v-else size="tiny" :bordered="false">已暂停</NTag>
              <span v-if="a.note" class="alert-note">{{ a.note }}</span>
              <span v-if="a.last_triggered_at" class="alert-time">触发于 {{ a.last_triggered_at.slice(5, 16) }}</span>
            </div>
          </div>
          <div class="alert-row-actions">
            <NSwitch v-if="a.status !== 'triggered'" size="small" :value="!!a.enabled"
              @update:value="(v: boolean) => toggleEnabled(a, v)" />
            <NButton v-if="a.status === 'triggered'" size="tiny" type="primary" secondary @click="restartAlert(a)">
              <template #icon><NIcon><RefreshOutline /></NIcon></template>
              重启
            </NButton>
            <NButton size="tiny" quaternary title="编辑此预警" aria-label="编辑此预警" @click="editAlert(a)">
              <template #icon><NIcon><CreateOutline /></NIcon></template>
            </NButton>
            <NPopconfirm @positive-click="removeAlert(a)" positive-text="删除" negative-text="取消">
              <template #trigger>
                <NButton size="tiny" quaternary type="error" title="删除此预警" aria-label="删除此预警">
                  <template #icon><NIcon><TrashOutline /></NIcon></template>
                </NButton>
              </template>
              确认删除这条预警?
            </NPopconfirm>
          </div>
        </div>
      </NSpin>

      <!-- 新增/编辑 -->
      <div class="section-title editing">
        {{ editingId == null ? '新增预警' : '编辑预警' }}
        <span class="and-hint">多条件需全部满足(且)</span>
      </div>
      <div v-for="(c, i) in draftConditions" :key="i" class="cond-edit">
        <NSelect :value="c.dim" :options="DIM_OPTIONS" size="small" style="width: 116px; flex: 0 0 auto"
          @update:value="(v: AlertDim) => onDimChange(c, v)" />
        <!-- 价格 / 涨跌幅 -->
        <template v-if="c.dim === 'price' || c.dim === 'pct'">
          <NSelect v-model:value="c.op" :options="OP_OPTIONS" size="small" style="width: 64px; flex: 0 0 auto" />
          <NInputNumber v-model:value="c.value" size="small" :show-button="false"
            :placeholder="c.dim === 'price' ? '价格' : '涨跌幅%'" style="flex: 1 1 90px; min-width: 80px" />
          <span v-if="c.dim === 'pct'" class="unit">%</span>
        </template>
        <!-- 接近均线 -->
        <template v-else-if="c.dim === 'ma_near'">
          <NSelect v-model:value="c.ma" :options="MA_OPTIONS" size="small" style="width: 84px; flex: 0 0 auto" />
          <span class="unit">±</span>
          <NInputNumber v-model:value="c.band" size="small" :show-button="false" :min="0.1" :max="10" :step="0.5"
            style="flex: 1 1 70px; min-width: 64px" />
          <span class="unit">%</span>
        </template>
        <!-- 上穿/跌破均线 -->
        <template v-else>
          <NSelect v-model:value="c.dir" :options="DIR_OPTIONS" size="small" style="width: 84px; flex: 0 0 auto" />
          <NSelect v-model:value="c.ma" :options="MA_OPTIONS" size="small" style="width: 84px; flex: 0 0 auto" />
        </template>
        <NButton size="tiny" quaternary circle title="移除此条件" aria-label="移除此条件" @click="removeConditionRow(i)"
          :disabled="draftConditions.length === 1 && i === 0">
          <template #icon><NIcon><TrashOutline /></NIcon></template>
        </NButton>
      </div>
      <NButton size="tiny" dashed style="margin-top: 4px" @click="addConditionRow">
        <template #icon><NIcon><AddOutline /></NIcon></template>
        添加条件(且)
      </NButton>

      <NInput v-model:value="draftNote" size="small" placeholder="备注(可选, 如: 回踩接回)" :maxlength="100"
        style="margin-top: 10px" />

      <div class="footer">
        <NButton v-if="editingId != null" size="small" @click="resetDraft">取消编辑</NButton>
        <NButton type="primary" size="small" :loading="saving" @click="submitDraft">
          {{ editingId == null ? '添加预警' : '保存修改' }}
        </NButton>
      </div>

      <div class="tip">
        满足条件即推送(PushPlus+飞书, 发到你账户配置的渠道)并在股票池标记。
        <b>一次性触发</b>: 命中后该条自动停用, 需手动重启。检测仅在交易时段、与股票池扫描同节奏。
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.alert-modal { display: flex; flex-direction: column; }
.section-title {
  font-size: 13px; font-weight: 600; color: var(--text2);
  margin: 4px 0 6px; display: flex; align-items: center; gap: 8px;
}
.section-title.editing { margin-top: 16px; border-top: 1px dashed var(--border, #eee); padding-top: 12px; }
.and-hint { font-size: 11px; font-weight: 400; color: var(--text2); }
.empty-box { padding: 8px 0; }
.alert-row {
  display: flex; justify-content: space-between; align-items: center; gap: 8px;
  padding: 7px 10px; margin-bottom: 6px; border-radius: 6px;
  background: var(--card2, #f7f8fa); border: 1px solid var(--border, #eee);
}
.alert-row.triggered { background: rgba(240, 160, 32, 0.10); border-color: rgba(240, 160, 32, 0.4); }
.alert-row-main { min-width: 0; flex: 1 1 auto; }
.alert-cond { font-size: 13px; color: var(--text1); font-weight: 500; line-height: 1.4; }
.alert-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 3px; }
.alert-note { font-size: 11px; color: #7c3aed; }
.alert-time { font-size: 11px; color: var(--text2); font-variant-numeric: tabular-nums; }
.alert-row-actions { display: flex; align-items: center; gap: 4px; flex: 0 0 auto; }
.cond-edit {
  display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap;
}
.unit { font-size: 12px; color: var(--text2); flex: 0 0 auto; }
.footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.tip {
  margin-top: 12px; font-size: 11px; color: var(--text2); line-height: 1.5;
  background: var(--card2, #f7f8fa); padding: 8px 10px; border-radius: 6px;
}
</style>
