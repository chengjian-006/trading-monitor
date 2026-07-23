<script setup lang="ts">
// 个股「问财提问」弹窗 (v1.7.777): 选预设模版 / 自定义, 新标签打开同花顺问财并把问题填好。
// 为什么走 deep-link 而非后端调问财: 生产服务器 IP 被同花顺风控封, 后端连结构化选股都调不通,
// 更别说对话式 chat; 而在【用户自己浏览器】打开 iwencai.com 是用户本人已登录的会话, 不碰风控。
//
// v1.7.786: 预设问题从写死常量改成【每用户存库 + 弹窗内就地维护】(点右上「管理」进管理模式:
// 改文案 / 加删条目 / 上下调序 / 停用 / 恢复默认)。模版用 {name}{code} 占位; 两个占位都没写的,
// 提问时自动在最前面补上股名(与自定义输入框同一规则)。取数失败时回退到内置默认, 弹窗不至于空白。
import { ref, computed, watch } from 'vue'
import { NModal, NInput, NButton, NIcon, NPopconfirm, NSwitch, NSpin } from 'naive-ui'
import { OpenOutline, HelpCircleOutline, CreateOutline, TrashOutline, AddOutline,
         ArrowUpOutline, ArrowDownOutline, RefreshOutline, CheckmarkOutline } from '@vicons/ionicons5'
import type { Stock } from '../../types'
import { listAskPresets, createAskPreset, updateAskPreset, deleteAskPreset,
         reorderAskPresets, resetAskPresets, type WencaiAskPreset } from '../../api/wencai'
import { useGlobalMessage } from '../../composables/useGlobalMessage'
import { useResponsive } from '../../composables/useResponsive'

const props = defineProps<{ show: boolean; row: Stock | null }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()

const message = useGlobalMessage()
const { isPhone } = useResponsive()
// 手机端弹窗占满屏宽(管理模式里有输入框, 520px 固定宽在手机上会被挤成两指宽)
const modalStyle = computed(() => ({ maxWidth: isPhone.value ? '94vw' : '520px' }))

const name = computed(() => props.row?.name || '')
const code = computed(() => props.row?.code || '')

// 后端拿不到时的兜底(与 backend DEFAULT_ASK_PRESETS 同一套文案)
const FALLBACK: WencaiAskPreset[] = [
  { id: -1, label: '现在能不能买', template: '{name} 现在能不能买,当前股价位置、支撑位和压力位、买卖点', enabled: 1, sort_order: 10 },
  { id: -2, label: '消息面(利好利空/公告)', template: '{name} 最近有哪些利好利空消息和最新公告', enabled: 1, sort_order: 20 },
  { id: -3, label: '基本面(业绩/估值/行业)', template: '{name} 最新业绩、估值(市盈率、市净率)和所属行业地位', enabled: 1, sort_order: 30 },
  { id: -4, label: '题材强弱 + 技术面', template: '{name} 属于哪些概念题材、题材热度如何,以及当前技术形态和短线操作建议', enabled: 1, sort_order: 40 },
]

const presets = ref<WencaiAskPreset[]>([])
const loading = ref(false)
const saving = ref(false)
const manage = ref(false)
const customText = ref('')

// 管理模式的草稿(改完一起存, 不逐字打一次接口)
const draft = ref<WencaiAskPreset[]>([])

const askable = computed(() => presets.value.filter(p => p.enabled))

async function load() {
  loading.value = true
  try {
    const { presets: rows } = await listAskPresets()
    presets.value = (rows || []).length ? rows : FALLBACK
  } catch {
    presets.value = FALLBACK   // 接口挂了也要能提问, 只是改不了
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (v) => {
  if (v) {
    customText.value = ''
    manage.value = false
    load()
  }
})

// ── 提问 ──

/** 模版 → 真实问题: 替换 {name}{code}; 两个占位都没写就自动在最前面补股名 */
function renderTemplate(tpl: string): string {
  const t = (tpl || '').trim()
  if (!t) return ''
  if (t.includes('{name}') || t.includes('{code}')) {
    return t.replace(/\{name\}/g, name.value).replace(/\{code\}/g, code.value).trim()
  }
  return (t.includes(name.value) || t.includes(code.value)) ? t : `${name.value} ${t}`
}

// 打开同花顺问财【经典问答页】(新标签): ?w= 会被自动执行并直接出结果, 一点即得。
// 为何不用 /chat 对话页: 实测其不读 ?w= 参数, 问题填不进输入框(同花顺前端实现所限);
// 经典问答页则可靠自动执行。要「投顾式对话 + 自动填输入框」需走浏览器扩展, 非纯 URL 可达。
function ask(question: string) {
  const q = question.trim()
  if (!q) return
  window.open(`https://www.iwencai.com/unifiedwap/result?w=${encodeURIComponent(q)}&querytype=stock`, '_blank', 'noopener,noreferrer')
}

function askCustom() {
  const t = customText.value.trim()
  if (!t) return
  ask(renderTemplate(t))
}

// ── 管理模式 ──

function enterManage() {
  draft.value = presets.value.map(p => ({ ...p }))
  manage.value = true
}

function moveRow(i: number, delta: number) {
  const j = i + delta
  if (j < 0 || j >= draft.value.length) return
  const arr = draft.value
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}

function addRow() {
  if (draft.value.length >= 20) {
    message.warning('模版最多 20 条')
    return
  }
  draft.value.push({ id: 0, label: '', template: '', enabled: 1, sort_order: 0 })
}

async function removeRow(i: number) {
  const row = draft.value[i]
  draft.value.splice(i, 1)
  if (row.id > 0) {
    try {
      await deleteAskPreset(row.id)
    } catch {
      message.error('删除失败, 请重试')
      await load()
      draft.value = presets.value.map(p => ({ ...p }))
    }
  }
}

/** 保存草稿: 新行 POST / 改过的行 PUT / 最后整体重排 */
async function saveDraft() {
  const rows = draft.value
  for (const r of rows) {
    if (!r.template.trim()) {
      message.warning('有模版内容是空的, 先填上或删掉这条')
      return
    }
  }
  saving.value = true
  try {
    for (const r of rows) {
      const label = r.label.trim()
      const template = r.template.trim()
      if (r.id > 0) {
        const old = presets.value.find(p => p.id === r.id)
        if (!old) continue
        if (old.label !== label || old.template !== template || old.enabled !== r.enabled) {
          await updateAskPreset(r.id, { label, template, enabled: r.enabled })
        }
      } else {
        const { id } = await createAskPreset(label, template)
        r.id = id
        if (!r.enabled) await updateAskPreset(id, { enabled: 0 })
      }
    }
    const ids = rows.map(r => r.id).filter(id => id > 0)
    if (ids.length) await reorderAskPresets(ids)
    await load()
    manage.value = false
    message.success('预设问题已保存')
  } catch {
    message.error('保存失败, 请重试')
  } finally {
    saving.value = false
  }
}

async function doReset() {
  saving.value = true
  try {
    const { presets: rows } = await resetAskPresets()
    presets.value = rows || []
    draft.value = presets.value.map(p => ({ ...p }))
    message.success('已恢复系统默认模版')
  } catch {
    message.error('恢复默认失败, 请重试')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal :show="show" @update:show="emit('update:show', $event)" preset="card"
          :title="`问财提问 · ${name}${code ? '(' + code + ')' : ''}`"
          :style="modalStyle" :bordered="false">
    <template #header-extra>
      <NButton v-if="!manage" size="tiny" quaternary @click="enterManage">
        <template #icon><NIcon :component="CreateOutline" /></template>管理
      </NButton>
    </template>

    <!-- ── 提问模式 ── -->
    <template v-if="!manage">
      <p class="wm-hint">
        <NIcon :component="HelpCircleOutline" :size="14" style="vertical-align: -2px" />
        选一个模版即在<b>新标签打开同花顺问财并直接出结果</b>(已自动带上「{{ name }}」);或自己写。需你已登录问财。
      </p>

      <NSpin :show="loading">
        <div class="wm-presets">
          <button v-for="t in askable" :key="t.id" type="button" class="wm-preset" @click="ask(renderTemplate(t.template))">
            <span class="wm-label">{{ t.label }}</span>
            <span class="wm-full">{{ renderTemplate(t.template) }}</span>
            <NIcon :component="OpenOutline" :size="14" class="wm-open" />
          </button>
          <p v-if="!loading && !askable.length" class="wm-empty">还没有启用的预设问题,点右上「管理」加一条。</p>
        </div>
      </NSpin>

      <div class="wm-custom">
        <NInput v-model:value="customText" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }"
                placeholder="或输入自定义问题(会自动带上股名)…" @keydown.enter.exact.prevent="askCustom" />
        <NButton type="primary" :disabled="!customText.trim()" @click="askCustom">
          <template #icon><NIcon :component="OpenOutline" /></template>去问财提问
        </NButton>
      </div>
    </template>

    <!-- ── 管理模式 ── -->
    <template v-else>
      <p class="wm-hint">
        模版里写 <code>{name}</code> 代表股名、<code>{code}</code> 代表代码;都不写的话提问时自动在最前面补股名。
        改动点<b>保存</b>才生效,PC 和手机同步。
      </p>

      <div class="wm-edit-list">
        <div v-for="(r, i) in draft" :key="r.id || 'new' + i" class="wm-edit-row">
          <div class="wm-edit-head">
            <NInput v-model:value="r.label" size="small" placeholder="按钮名(如: 现在能不能买)" :maxlength="40" class="wm-edit-label" />
            <div class="wm-edit-ops">
              <NSwitch v-model:value="r.enabled" :checked-value="1" :unchecked-value="0" size="small" title="启用/停用" />
              <NButton size="tiny" quaternary :disabled="i === 0" title="上移" @click="moveRow(i, -1)">
                <template #icon><NIcon :component="ArrowUpOutline" /></template>
              </NButton>
              <NButton size="tiny" quaternary :disabled="i === draft.length - 1" title="下移" @click="moveRow(i, 1)">
                <template #icon><NIcon :component="ArrowDownOutline" /></template>
              </NButton>
              <NPopconfirm @positive-click="removeRow(i)" positive-text="删除" negative-text="取消">
                <template #trigger>
                  <NButton size="tiny" quaternary title="删除">
                    <template #icon><NIcon :component="TrashOutline" /></template>
                  </NButton>
                </template>
                删除这条预设问题?
              </NPopconfirm>
            </div>
          </div>
          <NInput v-model:value="r.template" type="textarea" size="small" :autosize="{ minRows: 2, maxRows: 4 }"
                  :maxlength="255" placeholder="问题模版,如: {name} 现在能不能买,支撑位和压力位" />
        </div>
        <p v-if="!draft.length" class="wm-empty">一条都没有了,点下面「加一条」。</p>
      </div>

      <div class="wm-edit-foot">
        <NButton size="small" @click="addRow">
          <template #icon><NIcon :component="AddOutline" /></template>加一条
        </NButton>
        <NPopconfirm @positive-click="doReset" positive-text="恢复默认" negative-text="取消">
          <template #trigger>
            <NButton size="small" quaternary>
              <template #icon><NIcon :component="RefreshOutline" /></template>恢复默认
            </NButton>
          </template>
          会清掉你现在的全部模版, 换回系统默认那 4 条, 不可撤销。确认?
        </NPopconfirm>
        <span class="wm-foot-gap" />
        <NButton size="small" @click="manage = false">取消</NButton>
        <NButton size="small" type="primary" :loading="saving" @click="saveDraft">
          <template #icon><NIcon :component="CheckmarkOutline" /></template>保存
        </NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.wm-hint { margin: 0 0 12px; font-size: 12px; color: var(--fg-subtle); line-height: 1.5; }
.wm-hint b { color: var(--fg-muted); }
.wm-hint code {
  background: var(--bg-sunken); border: 1px solid var(--border-muted); border-radius: 4px;
  padding: 0 3px; font-size: 11px; color: var(--fg-muted);
}
.wm-presets { display: flex; flex-direction: column; gap: 8px; }
.wm-preset {
  position: relative; text-align: left; cursor: pointer; appearance: none; font: inherit;
  border: 1px solid var(--border-muted); border-radius: 8px; background: var(--bg-sunken);
  padding: 8px 34px 8px 12px; transition: border-color 0.12s, background 0.12s;
}
.wm-preset:hover { border-color: color-mix(in srgb, var(--accent-fg) 40%, transparent); background: var(--accent-bg-muted); }
.wm-label { display: block; font-size: 13px; font-weight: 600; color: var(--fg-default); }
.wm-full { display: block; margin-top: 2px; font-size: 11.5px; color: var(--fg-subtle); line-height: 1.4; }
.wm-open { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); color: var(--fg-subtle); }
.wm-preset:hover .wm-open { color: var(--accent-fg); }
.wm-custom { display: flex; align-items: flex-end; gap: 8px; margin-top: 14px; }
.wm-custom :deep(.n-input) { flex: 1; }
.wm-empty { margin: 6px 0; font-size: 12px; color: var(--fg-subtle); }

/* 管理模式 */
.wm-edit-list { display: flex; flex-direction: column; gap: 10px; max-height: 52vh; overflow-y: auto; }
.wm-edit-row {
  border: 1px solid var(--border-muted); border-radius: 8px; padding: 8px 10px;
  background: var(--bg-sunken); display: flex; flex-direction: column; gap: 6px;
}
.wm-edit-head { display: flex; align-items: center; gap: 8px; }
.wm-edit-label { flex: 1; }
.wm-edit-ops { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
.wm-edit-foot { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.wm-foot-gap { flex: 1; }

@media (max-width: 768px) {
  /* 手机: 管理行的操作按钮换行不挤(弹窗宽度走 modalStyle) */
  .wm-edit-list { max-height: 46vh; }
  .wm-edit-head { flex-wrap: wrap; }
  .wm-edit-label { flex: 1 0 100%; }
  .wm-custom { flex-direction: column; align-items: stretch; }
}
</style>
