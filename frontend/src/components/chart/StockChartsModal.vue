<script setup lang="ts">
// 股票池点代码弹出的"分时 + 日K + 大单"弹框。内容复用 StockCharts, 与整页 /intraday 同源。
import { useRouter } from 'vue-router'
import { NModal, NButton } from 'naive-ui'
import StockCharts from './StockCharts.vue'

const props = defineProps<{ show: boolean; code: string; name?: string }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()
const router = useRouter()

function openFullPage() {
  emit('update:show', false)
  router.push({ path: '/intraday', query: { code: props.code, name: props.name || '' } })
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="`${name || code} ${code} · 行情`"
    style="max-width: 560px; width: 92vw"
    :block-scroll="false"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <template #header-extra>
      <NButton text type="primary" size="small" @click="openFullPage">整页打开 ↗</NButton>
    </template>
    <!-- v-if: 每次打开重新挂载 → 自动按当前 code 取数; compact=紧凑(图更矮+去大单列表)一屏不滚动 -->
    <StockCharts v-if="show" :code="code" :name="name" compact />
  </NModal>
</template>
