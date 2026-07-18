<script setup lang="ts">
// 自选分组/标签/备注 编辑器 (v1.7.670): 给某只自选股设分组、打标签、写备注。
import { ref, watch } from 'vue'
import { NModal, NInput, NDynamicTags, NButton, NAutoComplete } from 'naive-ui'
import { updateStock } from '../../api/stocks'
import { useGlobalMessage } from '../../composables/useGlobalMessage'

const props = defineProps<{
  show: boolean
  code: string
  name: string
  grp?: string
  tags?: string
  note?: string
  groupOptions?: string[]     // 已有分组, 供输入建议
}>()
const emit = defineEmits<{ 'update:show': [boolean]; changed: [] }>()
const message = useGlobalMessage()

const grp = ref('')
const tagList = ref<string[]>([])
const note = ref('')
const saving = ref(false)

watch(() => props.show, (v) => {
  if (v) {
    grp.value = props.grp || ''
    tagList.value = (props.tags || '').split(',').map((t) => t.trim()).filter(Boolean)
    note.value = props.note || ''
  }
})

const grpAcOptions = ref<{ label: string; value: string }[]>([])
function onGrpUpdate(v: string) {
  grp.value = v
  const kw = (v || '').toLowerCase()
  grpAcOptions.value = (props.groupOptions || [])
    .filter((g) => g && g.toLowerCase().includes(kw))
    .map((g) => ({ label: g, value: g }))
}

async function save() {
  saving.value = true
  try {
    await updateStock(props.code, {
      grp: grp.value.trim(),
      tags: tagList.value.join(','),
      note: note.value.trim(),
    })
    message.success('已保存')
    emit('changed')
    emit('update:show', false)
  } catch { message.error('保存失败') }
  finally { saving.value = false }
}
</script>

<template>
  <NModal :show="show" @update:show="emit('update:show', $event)" preset="card"
          :title="`分组 / 标签 / 备注 · ${name} ${code}`" style="width:460px;max-width:94vw">
    <div class="mm-form">
      <div class="ff">
        <label>分组</label>
        <NAutoComplete :value="grp" :options="grpAcOptions" placeholder="如 龙头 / 回踩候选 / 观察"
                       clearable @update:value="onGrpUpdate" @focus="onGrpUpdate(grp)" />
      </div>
      <div class="ff">
        <label>标签（回车加一个）</label>
        <NDynamicTags v-model:value="tagList" />
      </div>
      <div class="ff">
        <label>备注</label>
        <NInput v-model:value="note" type="textarea" :rows="3" placeholder="给这只票记点什么…" maxlength="255" show-count />
      </div>
    </div>
    <template #footer>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <NButton size="small" @click="emit('update:show', false)">取消</NButton>
        <NButton size="small" type="primary" :loading="saving" @click="save">保存</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.mm-form { display: flex; flex-direction: column; gap: 14px; }
.ff { display: flex; flex-direction: column; gap: 6px; }
.ff label { font-size: 12px; color: var(--fg-muted); font-weight: 500; }
</style>
