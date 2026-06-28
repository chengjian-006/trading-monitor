<script setup lang="ts">
// 个股策略编辑弹窗(共享): 股票池表格 与 策略总览抽屉 共用, 消除重复逻辑。
import { ref, watch } from 'vue'
import { NModal, NInput, NButton } from 'naive-ui'
import { useStockStore } from '../../stores/stock'
import { useGlobalMessage } from '../../composables/useGlobalMessage'

const props = defineProps<{ show: boolean; code: string; name: string; text: string }>()
const emit = defineEmits<{ 'update:show': [boolean]; saved: [code: string, text: string] }>()

const stockStore = useStockStore()
const message = useGlobalMessage()

const editText = ref('')
const saving = ref(false)

watch(() => props.show, (v) => {
  if (v) editText.value = props.text || ''
})

async function save() {
  saving.value = true
  try {
    const newText = editText.value.trim()
    await stockStore.updateStock(props.code, { strategy: newText })
    emit('saved', props.code, newText)
    emit('update:show', false)
    message.success(newText ? '策略已保存' : '策略已清空')
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    @update:show="emit('update:show', $event)"
    preset="card"
    :title="`${code} ${name} · 操作策略`"
    style="max-width: 480px"
    :block-scroll="false"
  >
    <div style="margin-bottom:10px;color:var(--text2);font-size:12px">
      填入这只票的入场/加仓/止损计划。触发信号时会附在推送和信号卡上。
    </div>
    <NInput
      v-model:value="editText"
      type="textarea"
      :rows="4"
      placeholder="例：&#10;等回踩10日线附近 9.5 加仓30%&#10;破 9 止损&#10;站上 10.5 减仓50%"
      :maxlength="500"
      show-count
    />
    <div style="margin-top:14px;display:flex;justify-content:flex-end;gap:8px">
      <NButton @click="emit('update:show', false)">取消</NButton>
      <NButton type="primary" :loading="saving" @click="save">保存</NButton>
    </div>
  </NModal>
</template>
