<script setup lang="ts">
// 通用个股详情弹窗(全局单实例, 挂在 App.vue)。任意组件 useUiStore().openStock(code,name) 即弹。
// 头部: 名称/代码/板块 + 现价/涨跌幅 + 自选/持仓徽标; 内容复用 StockCharts(速览/信号/分时/日K/大单);
// 底部操作: 加自选 / 设策略 / 整页打开 / 同花顺·东财外链。与整页 /intraday 同源(同一 StockCharts)。
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NModal, NButton, NInput, NTag } from 'naive-ui'
import StockCharts from './StockCharts.vue'
import StockReviewCard from '../stock/StockReviewCard.vue'
import { thsStockUrl, emStockUrl, openExternal } from '../../utils/stockLinks'
import type { StockSummary } from '../../api/kline'
import { useUiStore } from '../../stores/ui'
import { useStockStore } from '../../stores/stock'
import { useGlobalMessage } from '../../composables/useGlobalMessage'

const ui = useUiStore()
const stockStore = useStockStore()
const router = useRouter()
const message = useGlobalMessage()

const summary = ref<StockSummary | null>(null)
const show = computed({ get: () => ui.stockShow, set: (v: boolean) => { if (!v) ui.closeStock() } })

const code = computed(() => ui.stockCode)
const name = computed(() => summary.value?.name || ui.stockName || ui.stockCode)

function boardOf(c: string): string {
  if (c.startsWith('688')) return '科创板'
  if (c.startsWith('8') || c.startsWith('4')) return '北交所'
  if (c.startsWith('300') || c.startsWith('301')) return '创业板'
  if (c.startsWith('60')) return '沪市主板'
  if (c.startsWith('00')) return '深市主板'
  return ''
}
const board = computed(() => boardOf(code.value))

// 自选/持仓状态(来自股票池 store)
const inPool = computed(() => stockStore.stocks.find(s => s.code === code.value) || null)

function pctText(v: number | null | undefined): string {
  if (v == null) return ''
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

const adding = ref(false)
async function addToPool() {
  if (!code.value || inPool.value) return
  adding.value = true
  try {
    await stockStore.addStock(code.value, name.value, 'short', 'watch')
    message.success('已加入自选')
  } catch {
    message.error('加入自选失败')
  } finally {
    adding.value = false
  }
}

// 设策略(仅自选内的票): 内联展开 textarea
const stratOpen = ref(false)
const stratText = ref('')
const stratSaving = ref(false)
function toggleStrategy() {
  stratText.value = inPool.value?.strategy || ''
  stratOpen.value = !stratOpen.value
}
async function saveStrategy() {
  stratSaving.value = true
  try {
    await stockStore.updateStock(code.value, { strategy: stratText.value.trim() })
    message.success('策略已保存')
    stratOpen.value = false
  } catch {
    message.error('保存失败')
  } finally {
    stratSaving.value = false
  }
}

function openFullPage() {
  ui.closeStock()
  router.push({ path: '/intraday', query: { code: code.value, name: name.value } })
}

// AI 个股研判卡(按需触发, 独立弹窗叠在详情弹窗之上)
const reviewShow = ref(false)

// 切换股票时重置内联策略编辑
watch(code, () => { stratOpen.value = false; summary.value = null; reviewShow.value = false })
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    style="max-width: 1080px; width: 94vw"
    :block-scroll="false"
    @update:show="(v: boolean) => { if (!v) ui.closeStock() }"
  >
    <template #header>
      <div class="dm-head">
        <span class="dm-name">{{ name }}</span>
        <span class="dm-code">{{ code }}</span>
        <NTag v-if="board" size="small" :bordered="false">{{ board }}</NTag>
        <NTag v-if="inPool" size="small" :bordered="false" :type="inPool.status === 'hold' ? 'success' : 'info'">
          {{ inPool.status === 'hold' ? '持仓' : '自选' }}
        </NTag>
        <span v-if="summary?.close != null" class="dm-price" :class="(summary.pct_change ?? 0) >= 0 ? 'up' : 'down'">
          {{ summary.close.toFixed(2) }}
          <span class="dm-pct">{{ pctText(summary.pct_change) }}</span>
        </span>
      </div>
    </template>
    <template #header-extra>
      <NButton text type="primary" size="small" @click="openFullPage">整页打开 ↗</NButton>
    </template>

    <StockCharts v-if="show" :code="code" :name="name" compact @summary="(s) => summary = s" />

    <!-- 设策略内联编辑 -->
    <div v-if="stratOpen" class="dm-strat">
      <NInput v-model:value="stratText" type="textarea" :rows="3" :maxlength="500" show-count
              placeholder="填入这只票的入场/加仓/止损计划，触发信号时会附在推送和信号卡上。" />
      <div class="dm-strat-act">
        <NButton size="small" @click="stratOpen = false">取消</NButton>
        <NButton size="small" type="primary" :loading="stratSaving" @click="saveStrategy">保存</NButton>
      </div>
    </div>

    <StockReviewCard v-model:show="reviewShow" :code="code" :name="name" />

    <template #footer>
      <div class="dm-foot">
        <NButton v-if="!inPool" size="small" type="primary" :loading="adding" @click="addToPool">+ 加自选</NButton>
        <NButton v-if="inPool" size="small" :type="stratOpen ? 'primary' : 'default'" @click="toggleStrategy">设策略</NButton>
        <NButton size="small" type="info" secondary @click="reviewShow = true">AI 研判</NButton>
        <span class="dm-ext">
          <NButton size="small" tertiary title="在同花顺网页版看分时/K线" @click="openExternal(thsStockUrl(code))">同花顺 ↗</NButton>
          <NButton size="small" tertiary title="在东方财富网页版看分时/K线" @click="openExternal(emStockUrl(code))">东财 ↗</NButton>
        </span>
        <NButton size="small" @click="openFullPage">整页打开 ↗</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.dm-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; }
.dm-name { font-size: 16px; font-weight: 600; color: var(--text1); }
.dm-code { font-size: 13px; color: var(--text2); font-family: monospace; font-variant-numeric: tabular-nums; }
.dm-price { margin-left: 6px; font-size: 18px; font-weight: 700; font-family: monospace; font-variant-numeric: tabular-nums; }
.dm-pct { font-size: 13px; margin-left: 4px; }
.dm-strat { margin-top: 12px; }
.dm-strat-act { margin-top: 8px; display: flex; justify-content: flex-end; gap: 8px; }
.dm-foot { display: flex; gap: 8px; justify-content: flex-end; align-items: center; flex-wrap: wrap; }
.dm-ext { display: inline-flex; gap: 6px; }
@media (max-width: 768px) {
  .dm-foot { gap: 6px; }
  .dm-ext { order: 3; flex-basis: 100%; justify-content: flex-end; }
}
.up { color: #cf222e; }
.down { color: #1a7f37; }
</style>
