<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import {
  NInput, NButton, NSpace, NSpin, NModal,
  NList, NListItem, NTag, NIcon, NProgress, NSkeleton,
  NUpload, NUploadDragger, NSelect, NTabs, NTabPane, NPopconfirm,
  NRadioGroup, NRadioButton, NInputNumber, NDropdown,
} from 'naive-ui'
import type { UploadFileInfo } from 'naive-ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { AddOutline, SearchOutline, CloudDownloadOutline, DownloadOutline, SwapVerticalOutline, ImageOutline, DocumentTextOutline, RefreshOutline } from '@vicons/ionicons5'
import { useStockStore } from '../stores/stock'
import { useSignalStore } from '../stores/signal'
import { useResponsive } from '../composables/useResponsive'
import { searchStock, ocrRecognize, batchImportStocks, batchDeleteStocks } from '../api/stocks'
import { fetchThsGroups, importThsGroup, compareThsUpload } from '../api/config'
import type { ThsCompareResult } from '../types'
import StockTable from '../components/stock/StockTable.vue'
import StockList from '../components/stock/StockList.vue'
import PoolStatsBar from '../components/stock/PoolStatsBar.vue'
import StrategyOverviewDrawer from '../components/stock/StrategyOverviewDrawer.vue'
import SignalSummaryBar from '../components/stock/SignalSummaryBar.vue'
import TagLegendButton from '../components/stock/TagLegendButton.vue'
import { computed } from 'vue'
import type { ThsGroup } from '../types'

const stockStore = useStockStore()
const signalStore = useSignalStore()
// 三档断点统一走 useResponsive; 手机(<768)用 isPhone 切卡片视图, 平板/桌面继续用宽表
const { isPhone } = useResponsive()
const message = useGlobalMessage()
const stockTableRef = ref<InstanceType<typeof StockTable> | null>(null)
const showStrategyDrawer = ref(false)
const showSparkline = ref(false)

// v1.7.x: SignalSummaryBar 从 StockTable 内部移到 PoolView 顶部 (避免与 StockTable 名称色块重复展示),
// 由 PoolView 集中渲染一次, 同时供桌面/移动复用
import { useSignalGrouping } from '../composables/useSignalGrouping'
const { signalsByCode } = useSignalGrouping(() => signalStore.signals)

// 股票池客户端即时筛选 (v1.7.419)
import { usePoolFilter } from '../composables/usePoolFilter'
const pf = usePoolFilter(() => stockStore.stocks, signalsByCode)
// 自选分组下拉选项 (v1.7.670): 从池内已有分组去重
const groupOptions = computed(() =>
  [...new Set(stockStore.stocks.map((s) => s.grp).filter((g): g is string => !!g))].sort()
    .map((g) => ({ label: g, value: g })))

// 工具栏重排 (v1.7.676): 导入合并成下拉
const importOptions = [
  { label: '自选导入（同花顺）', key: 'ths' },
  { label: '截图导入（OCR 识别）', key: 'ocr' },
]
function onImportSelect(key: string) {
  if (key === 'ths') openThsImport()
  else if (key === 'ocr') openOcrImport()
}

const searchText = ref('')
const searchResults = ref<{ code: string; name: string }[]>([])
const selectedStock = ref<{ code: string; name: string } | null>(null)
const tradeType = ref('short')
const status = ref('watch')
const searchTimer = ref<ReturnType<typeof setTimeout>>()
const showDropdown = ref(false)

// THS import
const showThsModal = ref(false)
const thsTab = ref<'import' | 'compare'>('import')
const thsGroups = ref<ThsGroup[]>([])
const thsLoading = ref(false)
const thsImporting = ref<string | null>(null)
const importProgress = ref<{ current: number; total: number; code: string; name: string; status: string } | null>(null)

// THS 自选对比(浏览器上传本地同花顺自选文件 → 云端解析对比, 不依赖服务器本地路径)
const cmpLoading = ref(false)
const cmpResult = ref<ThsCompareResult | null>(null)
const cmpFile = ref<File | null>(null)       // 缓存所选文件, 增删后复用它重新对比
const cmpLastFileName = ref<string>(localStorage.getItem('ths_cmp_last_file') || '')  // 上次成功对比的文件名(浏览器只能拿到文件名, 拿不到完整路径)
const cmpAddChecked = ref<string[]>([])      // 勾选要新增到系统的(同花顺有·系统缺)
const cmpDelChecked = ref<string[]>([])      // 勾选要从系统删除的(系统有·同花顺缺)
const cmpAddBusy = ref(false)
const cmpDelBusy = ref(false)

onMounted(() => {
  stockStore.loadStocks(true)
  stockStore.startPolling()
  signalStore.loadTodaySignals()
})

onUnmounted(() => {
  stockStore.stopPolling()
})

function onSearchInput(val: string) {
  searchText.value = val
  selectedStock.value = null
  clearTimeout(searchTimer.value)
  if (val.length < 1) {
    searchResults.value = []
    showDropdown.value = false
    return
  }
  searchTimer.value = setTimeout(async () => {
    const results = await searchStock(val)
    searchResults.value = results
    showDropdown.value = results.length > 0
  }, 300)
}

function selectResult(item: { code: string; name: string }) {
  selectedStock.value = item
  searchText.value = `${item.code} ${item.name}`
  showDropdown.value = false
}

async function handleAdd() {
  let stock = selectedStock.value
  if (!stock) {
    const val = searchText.value.trim()
    if (/^\d{6}$/.test(val)) {
      stock = { code: val, name: '' }
    } else {
      message.warning('请先搜索并选择一只股票')
      return
    }
  }
  try {
    await stockStore.addStock(stock.code, stock.name, tradeType.value, status.value)
    searchText.value = ''
    selectedStock.value = null
    message.success(`${stock.code} 添加成功`)
  } catch {
    message.error(`添加 ${stock.code} 失败`)
  }
}

async function openThsImport() {
  showThsModal.value = true
  thsTab.value = 'import'
  cmpResult.value = null
  cmpFile.value = null
  thsLoading.value = true
  try {
    const result = await fetchThsGroups()
    if (result.ok) {
      thsGroups.value = result.groups
    } else {
      // 打开弹窗时未找到自选文件不再弹 toast(刚加载就报错很突兀), 由下方空态文案提示即可
      thsGroups.value = []
    }
  } catch {
    message.error('获取分组失败')
  } finally {
    thsLoading.value = false
  }
}

// OCR image import
const showOcrModal = ref(false)
const ocrLoading = ref(false)
const ocrResults = ref<{ code: string; name: string }[]>([])
const ocrChecked = ref<string[]>([])
const ocrImporting = ref(false)
const ocrDone = ref<{ success: number; total: number } | null>(null)

function openOcrImport() {
  showOcrModal.value = true
  ocrResults.value = []
  ocrChecked.value = []
  ocrDone.value = null
}

async function handleOcrUpload({ file }: { file: UploadFileInfo }) {
  if (!file.file) return
  ocrLoading.value = true
  ocrResults.value = []
  ocrChecked.value = []
  ocrDone.value = null
  try {
    const res = await ocrRecognize(file.file)
    if (res.error) {
      message.error(res.error)
      return
    }
    if (res.stocks && res.stocks.length > 0) {
      ocrResults.value = res.stocks
      ocrChecked.value = res.stocks.map(s => s.code)
    } else {
      message.warning('未识别到股票信息')
    }
  } catch {
    message.error('图片识别失败')
  } finally {
    ocrLoading.value = false
  }
}

async function handleOcrConfirm() {
  const selected = ocrResults.value.filter(s => ocrChecked.value.includes(s.code))
  if (!selected.length) {
    message.warning('请至少选择一只股票')
    return
  }
  ocrImporting.value = true
  try {
    const res = await batchImportStocks(selected)
    ocrDone.value = { success: res.success, total: res.total }
    await stockStore.loadStocks(true)
  } catch {
    message.error('批量导入失败')
  } finally {
    ocrImporting.value = false
  }
}


// 选择/重新对比: 上传缓存的同花顺自选文件到云端解析对比
async function runThsCompare() {
  if (!cmpFile.value) return
  cmpLoading.value = true
  try {
    const res = await compareThsUpload(cmpFile.value)
    cmpResult.value = res
    if (!res.ok) {
      message.warning(res.msg || '解析失败, 请确认上传的是同花顺自选文件')
    } else {
      cmpAddChecked.value = (res.ths_only || []).map(s => s.code)
      cmpDelChecked.value = []   // 删除默认不勾, 防误删
      if (cmpFile.value?.name) {  // 记住本次成功用的文件名, 下次进来作提示
        cmpLastFileName.value = cmpFile.value.name
        localStorage.setItem('ths_cmp_last_file', cmpFile.value.name)
      }
    }
  } catch {
    message.error('对比失败')
  } finally {
    cmpLoading.value = false
  }
}

function onCmpFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  if (f) {
    cmpFile.value = f
    cmpResult.value = null
    runThsCompare()
  }
  input.value = ''   // 允许再次选同一文件触发 change
}

function onThsTabChange(tab: string) {
  thsTab.value = tab as 'import' | 'compare'
}

async function handleCmpAdd() {
  const all = cmpResult.value?.ths_only || []
  const picked = all.filter(s => cmpAddChecked.value.includes(s.code))
  if (!picked.length) {
    message.warning('请至少勾选一只股票')
    return
  }
  cmpAddBusy.value = true
  try {
    const res = await batchImportStocks(picked.map(s => ({ code: s.code, name: s.name })))
    message.success(`已新增 ${res.success} 只到系统`)
    await stockStore.loadStocks(true)
    await runThsCompare()
  } catch {
    message.error('新增失败')
  } finally {
    cmpAddBusy.value = false
  }
}

async function handleCmpDelete() {
  const codes = cmpDelChecked.value.slice()
  if (!codes.length) {
    message.warning('请至少勾选一只股票')
    return
  }
  cmpDelBusy.value = true
  try {
    const res = await batchDeleteStocks(codes)
    message.success(`已从系统出池 ${res.deleted} 只`)
    await stockStore.loadStocks(true)
    await runThsCompare()
  } catch {
    message.error('删除失败')
  } finally {
    cmpDelBusy.value = false
  }
}

async function handleThsImport(groupId: string) {
  thsImporting.value = groupId
  importProgress.value = { current: 0, total: 0, code: '', name: '', status: '准备中...' }
  try {
    const result = await importThsGroup(groupId, (event) => {
      if (event.type === 'progress') {
        const action = event.action === 'import' ? '导入' : '跳过'
        const label = event.name || event.code || ''
        importProgress.value = {
          current: event.current!,
          total: event.total!,
          code: event.code || '',
          name: label,
          status: `${action} ${event.code} ${label}`,
        }
      } else if (event.type === 'status') {
        if (importProgress.value) importProgress.value.status = event.msg || ''
      }
    })
    if (result.ok) {
      message.success(result.msg)
      showThsModal.value = false
      await stockStore.loadStocks(true)
    } else {
      message.error(result.msg)
    }
  } catch {
    message.error('导入失败')
  } finally {
    thsImporting.value = null
    importProgress.value = null
  }
}
</script>

<template>
  <div :class="['pool-view', { 'pool-view--fixed': !isPhone }]">
    <div class="pool-controls">
    <div class="filter-bar">
      <div class="filter-fields">
        <div class="filter-item" style="flex: 2; min-width: 200px;">
          <label>添加股票</label>
          <div class="search-wrapper">
            <NInput
              :value="searchText"
              @update:value="onSearchInput"
              placeholder="输入代码或名称搜索..."
              clearable
              size="small"
              @focus="showDropdown = searchResults.length > 0"
            />
            <div v-if="showDropdown" class="search-dropdown">
              <div
                v-for="item in searchResults"
                :key="item.code"
                class="search-item"
                role="button"
                tabindex="0"
                @click="selectResult(item)"
                @keydown.enter="selectResult(item)"
              >
                <span class="item-code">{{ item.code }}</span>
                {{ item.name }}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="filter-actions">
        <!-- 添加组 -->
        <NSelect
          v-model:value="tradeType"
          :options="[{ label: '短线', value: 'short' }, { label: '中线', value: 'mid' }, { label: '指数', value: 'index' }]"
          size="small"
          style="width: 78px"
        />
        <NButton type="primary" size="small" @click="handleAdd">
          <template #icon><NIcon><AddOutline /></NIcon></template>添加
        </NButton>
        <NDropdown trigger="click" :options="importOptions" @select="onImportSelect">
          <NButton size="small" secondary>
            <template #icon><NIcon><CloudDownloadOutline /></NIcon></template>导入
          </NButton>
        </NDropdown>
        <span class="fa-divider" />
        <!-- 数据组 -->
        <NButton size="small" secondary circle title="刷新行情/数据" @click="stockStore.loadStocks(true)" :loading="stockStore.loading">
          <template #icon><NIcon><RefreshOutline /></NIcon></template>
        </NButton>
        <span class="fa-divider" />
        <!-- 视图 · 工具组 -->
        <NButton size="small" :type="showSparkline ? 'success' : 'default'" secondary title="走势迷你图" @click="showSparkline = !showSparkline">
          走势{{ showSparkline ? '开' : '关' }}
        </NButton>
        <NButton size="small" secondary @click="showStrategyDrawer = true">
          <template #icon><NIcon><DocumentTextOutline /></NIcon></template>策略总览
        </NButton>
        <NButton v-if="!isPhone" size="small" secondary :disabled="stockStore.stocks.length === 0" title="导出 Excel" @click="stockTableRef?.exportXlsx()">
          <template #icon><NIcon><DownloadOutline /></NIcon></template>导出
        </NButton>
        <NButton v-if="stockTableRef?.sortState" size="small" type="warning" secondary @click="stockTableRef?.resetSort()">
          <template #icon><NIcon><SwapVerticalOutline /></NIcon></template>重置排序
        </NButton>
      </div>
    </div>

    <!-- 股票池筛选 (v1.7.419): 快捷胶囊 + 高级面板, 全部前端即时过滤 -->
    <div class="pool-filter">
      <div class="pf-row">
        <NInput v-model:value="pf.fKeyword.value" size="small" clearable class="pf-search"
          placeholder="代码 / 名称 / 拼音首字母 查找…（如 gzmt → 贵州茅台）">
          <template #prefix><NIcon :component="SearchOutline" /></template>
        </NInput>
        <NRadioGroup v-model:value="pf.fStatus.value" size="small">
          <NRadioButton value="all">全部</NRadioButton>
          <NRadioButton value="hold">持仓</NRadioButton>
          <NRadioButton value="watch">关注</NRadioButton>
        </NRadioGroup>
        <NRadioGroup v-model:value="pf.fUpDown.value" size="small">
          <NRadioButton value="all">涨跌不限</NRadioButton>
          <NRadioButton value="up">上涨</NRadioButton>
          <NRadioButton value="down">下跌</NRadioButton>
        </NRadioGroup>
        <NSelect v-if="groupOptions.length" v-model:value="pf.fGroup.value" :options="groupOptions"
          size="small" clearable placeholder="全部分组" class="pf-group" style="width:130px" />
        <div class="pf-chips">
          <NButton size="tiny" round :type="pf.fHasBuy.value ? 'primary' : 'default'" :tertiary="!pf.fHasBuy.value" @click="pf.fHasBuy.value = !pf.fHasBuy.value">今日有买点</NButton>
          <NButton size="tiny" round :type="pf.fHasSell.value ? 'warning' : 'default'" :tertiary="!pf.fHasSell.value" @click="pf.fHasSell.value = !pf.fHasSell.value">今日有卖点</NButton>
          <NButton size="tiny" round :type="pf.fLimitUp.value ? 'error' : 'default'" :tertiary="!pf.fLimitUp.value" @click="pf.fLimitUp.value = !pf.fLimitUp.value">涨停</NButton>
          <NButton size="tiny" round :type="pf.fLianBan.value ? 'error' : 'default'" :tertiary="!pf.fLianBan.value" @click="pf.fLianBan.value = !pf.fLianBan.value">连板</NButton>
          <NButton size="tiny" round :type="pf.fAboveMa20.value ? 'info' : 'default'" :tertiary="!pf.fAboveMa20.value" @click="pf.fAboveMa20.value = !pf.fAboveMa20.value; if (pf.fAboveMa20.value) pf.fBelowMa20.value = false">站上20线</NButton>
          <NButton size="tiny" round :type="pf.fBelowMa20.value ? 'info' : 'default'" :tertiary="!pf.fBelowMa20.value" @click="pf.fBelowMa20.value = !pf.fBelowMa20.value; if (pf.fBelowMa20.value) pf.fAboveMa20.value = false">未站上20线</NButton>
          <NButton size="tiny" round :type="pf.fNearMa10.value ? 'info' : 'default'" :tertiary="!pf.fNearMa10.value" @click="pf.fNearMa10.value = !pf.fNearMa10.value">近10线±2%</NButton>
          <NButton size="tiny" round :type="pf.fNearMa60.value ? 'info' : 'default'" :tertiary="!pf.fNearMa60.value" @click="pf.fNearMa60.value = !pf.fNearMa60.value">近60线±2%</NButton>
        </div>
        <NButton size="tiny" quaternary @click="pf.advancedOpen.value = !pf.advancedOpen.value">
          高级筛选 {{ pf.advancedOpen.value ? '收起' : '展开' }}
        </NButton>
        <NButton v-if="pf.hasActiveFilter.value" size="tiny" type="warning" tertiary @click="pf.reset()">清空筛选</NButton>
      </div>

      <div v-if="pf.advancedOpen.value" class="pf-advanced">
        <div class="pf-adv-item">
          <label>交易类型</label>
          <NSelect v-model:value="pf.fTradeTypes.value" multiple size="small" style="width: 170px" placeholder="不限"
            :options="[{ label: '短线', value: 'short' }, { label: '中线', value: 'mid' }, { label: '指数', value: 'index' }]" />
        </div>
        <div class="pf-adv-item">
          <label>涨幅%</label>
          <NInputNumber v-model:value="pf.fPctMin.value" size="small" style="width: 84px" placeholder="下限" />
          <span class="pf-tilde">~</span>
          <NInputNumber v-model:value="pf.fPctMax.value" size="small" style="width: 84px" placeholder="上限" />
        </div>
        <div class="pf-adv-item">
          <label>换手率≥</label>
          <NInputNumber v-model:value="pf.fTurnoverMin.value" size="small" style="width: 90px" :min="0" placeholder="%" />
        </div>
        <div class="pf-adv-item">
          <label>量比≥</label>
          <NInputNumber v-model:value="pf.fVolRatioMin.value" size="small" style="width: 84px" :min="0" placeholder="" />
        </div>
        <div class="pf-adv-item">
          <label>板块内名次≤</label>
          <NInputNumber v-model:value="pf.fBoardRankMax.value" size="small" style="width: 84px" :min="1" placeholder="N" />
        </div>
        <div class="pf-adv-item">
          <label>人气榜≤</label>
          <NInputNumber v-model:value="pf.fPopRankMax.value" size="small" style="width: 96px" :min="1" placeholder="N" />
        </div>
        <div class="pf-adv-item">
          <label>行业/题材</label>
          <NInput v-model:value="pf.fIndustry.value" size="small" style="width: 130px" placeholder="包含..." clearable />
        </div>
      </div>
    </div>
    </div>

    <NSkeleton v-if="stockStore.loading && stockStore.stocks.length === 0" :repeat="6" text style="margin-bottom: 16px" />

    <Transition v-else name="content-fade" appear>
      <div :class="{ 'pool-content': !isPhone }">
        <SignalSummaryBar :signals-by-code="signalsByCode" />
        <div class="pool-summary-row">
          <PoolStatsBar :stocks="stockStore.stocks" />
          <div class="table-summary">
            <span v-if="pf.hasActiveFilter.value">筛选后 <b>{{ pf.filteredStocks.value.length }}</b> / 共 {{ stockStore.stocks.length }} 只</span>
            <span v-else>共 {{ stockStore.stocks.length }} 只，持仓 {{ stockStore.stocks.filter(s => s.status === 'hold').length }} 只，关注 {{ stockStore.stocks.filter(s => s.focused).length }} 只</span>
            <TagLegendButton />
          </div>
        </div>
        <StockTable v-if="!isPhone" ref="stockTableRef" :stocks="pf.filteredStocks.value" :show-sparkline="showSparkline" />
        <StockList v-else :stocks="pf.filteredStocks.value" />
      </div>
    </Transition>

    <StrategyOverviewDrawer
      v-model:show="showStrategyDrawer"
      :stocks="stockStore.stocks"
      :signals-by-code="signalsByCode"
    />

    <NModal v-model:show="showThsModal" preset="card" title="同花顺自选" style="max-width: 560px" :closable="true" :mask-closable="!thsImporting" :close-on-esc="!thsImporting" :block-scroll="false" :on-close="() => { if (!thsImporting) showThsModal = false }">
      <NTabs :value="thsTab" type="segment" size="small" @update:value="onThsTabChange">
        <!-- 导入分组 -->
        <NTabPane name="import" tab="导入分组">
          <div v-if="importProgress" class="import-progress">
            <NProgress type="line" :percentage="importProgress.total ? Math.round(importProgress.current / importProgress.total * 100) : 0" :show-indicator="true" />
            <div class="progress-detail">
              <span class="progress-count">{{ importProgress.current }} / {{ importProgress.total }}</span>
              <span class="progress-status">{{ importProgress.status }}</span>
            </div>
          </div>
          <template v-else>
            <NSkeleton v-if="thsLoading" :repeat="3" text />
            <template v-else>
              <NList v-if="thsGroups.length" bordered>
                <NListItem v-for="g in thsGroups" :key="g.id">
                  <div style="display: flex; align-items: center; justify-content: space-between">
                    <div>
                      <span style="font-weight: 600">{{ g.name }}</span>
                      <NTag size="tiny" :bordered="false" style="margin-left: 8px">{{ g.count }} 只</NTag>
                    </div>
                    <NButton
                      size="small"
                      type="primary"
                      :loading="thsImporting === g.id"
                      @click="handleThsImport(g.id)"
                    >
                      <template #icon><NIcon><DownloadOutline /></NIcon></template>
                      导入
                    </NButton>
                  </div>
                </NListItem>
              </NList>
              <div v-else class="ths-empty">
                未找到自选分组，请先在系统设置中配置同花顺安装路径
              </div>
            </template>
          </template>
        </NTabPane>

        <!-- 对比自选 -->
        <NTabPane name="compare" tab="对比自选">
          <div class="cmp-picker">
            <label class="cmp-pick-btn">
              <input type="file" accept=".json,.xml" hidden @change="onCmpFilePicked" />
              {{ cmpFile ? '重新选择文件' : '选择同花顺自选文件' }}
            </label>
            <span class="cmp-pick-hint">选本地 <code>SelfStockCache.json</code>(自选主表)或 <code>blockstockV3.xml</code>(全部分组)</span>
          </div>
          <div v-if="cmpLastFileName && !cmpFile" class="cmp-last-file">上次用过:<code>{{ cmpLastFileName }}</code> · 浏览器不能自动定位,需再点上方按钮选同一文件</div>

          <NSkeleton v-if="cmpLoading" :repeat="4" text />
          <div v-else-if="cmpResult && cmpResult.ok" class="cmp-wrap">
            <div class="cmp-summary">
              <span v-if="cmpResult.source" class="cmp-source">{{ cmpResult.source }}</span>
              同花顺 <b>{{ cmpResult.ths_count }}</b> 只 · 系统 <b>{{ cmpResult.system_count }}</b> 只 · 共有 <b>{{ cmpResult.both }}</b> 只
              <NButton size="tiny" quaternary style="margin-left: auto" @click="runThsCompare">刷新</NButton>
            </div>

            <!-- 同花顺有 · 系统缺 -->
            <div class="cmp-section">
              <div class="cmp-section-head">
                <span>同花顺有 · 系统缺 <NTag size="tiny" :bordered="false" type="success">{{ cmpResult.ths_only?.length || 0 }}</NTag></span>
                <div class="cmp-head-actions" v-if="cmpResult.ths_only?.length">
                  <NButton size="tiny" quaternary @click="cmpAddChecked = (cmpResult.ths_only || []).map(s => s.code)">全选</NButton>
                  <NButton size="tiny" quaternary @click="cmpAddChecked = []">清空</NButton>
                </div>
              </div>
              <div v-if="cmpResult.ths_only?.length" class="cmp-list">
                <label v-for="item in cmpResult.ths_only" :key="item.code" class="cmp-item" :class="{ checked: cmpAddChecked.includes(item.code) }">
                  <input type="checkbox" :value="item.code" v-model="cmpAddChecked" class="cmp-checkbox" />
                  <span class="cmp-code">{{ item.code }}</span>
                  <span class="cmp-name">{{ item.name || '—' }}</span>
                </label>
              </div>
              <div v-else class="cmp-empty">系统已包含同花顺自选的全部个股</div>
              <div v-if="cmpResult.ths_only?.length" class="cmp-actions">
                <span class="cmp-count">已选 <b>{{ cmpAddChecked.length }}</b> / {{ cmpResult.ths_only.length }}</span>
                <NButton type="primary" size="small" :loading="cmpAddBusy" :disabled="!cmpAddChecked.length" @click="handleCmpAdd">
                  <template #icon><NIcon><AddOutline /></NIcon></template>
                  新增选中到系统
                </NButton>
              </div>
            </div>

            <!-- 系统有 · 同花顺缺 -->
            <div class="cmp-section">
              <div class="cmp-section-head">
                <span>系统有 · 同花顺缺 <NTag size="tiny" :bordered="false" type="warning">{{ cmpResult.system_only?.length || 0 }}</NTag></span>
                <div class="cmp-head-actions" v-if="cmpResult.system_only?.length">
                  <NButton size="tiny" quaternary @click="cmpDelChecked = (cmpResult.system_only || []).map(s => s.code)">全选</NButton>
                  <NButton size="tiny" quaternary @click="cmpDelChecked = []">清空</NButton>
                </div>
              </div>
              <div v-if="cmpResult.system_only?.length" class="cmp-list">
                <label v-for="item in cmpResult.system_only" :key="item.code" class="cmp-item" :class="{ checked: cmpDelChecked.includes(item.code) }">
                  <input type="checkbox" :value="item.code" v-model="cmpDelChecked" class="cmp-checkbox" />
                  <span class="cmp-code">{{ item.code }}</span>
                  <span class="cmp-name">{{ item.name || '—' }}</span>
                </label>
              </div>
              <div v-else class="cmp-empty">系统没有同花顺以外的多余个股</div>
              <div v-if="cmpResult.system_only?.length" class="cmp-actions">
                <span class="cmp-count">已选 <b>{{ cmpDelChecked.length }}</b> / {{ cmpResult.system_only.length }}</span>
                <NPopconfirm @positive-click="handleCmpDelete">
                  <template #trigger>
                    <NButton type="error" size="small" :loading="cmpDelBusy" :disabled="!cmpDelChecked.length">
                      <template #icon><NIcon><DownloadOutline /></NIcon></template>
                      删除选中(出池)
                    </NButton>
                  </template>
                  确认把选中的 {{ cmpDelChecked.length }} 只从系统出池?(逻辑删除, 保留历史信号)
                </NPopconfirm>
              </div>
            </div>
          </div>
          <div v-else-if="cmpResult && !cmpResult.ok" class="ths-empty">
            {{ cmpResult.msg || '解析失败, 请确认上传的是同花顺自选文件' }}
          </div>
          <div v-else class="ths-empty">
            点上方「选择同花顺自选文件」开始对比。<br />
            文件一般在 <code>同花顺远航版\bin\users\&lt;账号&gt;\SelfStockCache.json</code>
          </div>
        </NTabPane>
      </NTabs>
    </NModal>

    <!-- OCR 图片导入 -->
    <NModal v-model:show="showOcrModal" preset="card" title="图片识别导入" style="max-width: 560px" :closable="!ocrLoading && !ocrImporting" :mask-closable="!ocrLoading && !ocrImporting" :block-scroll="false">
      <!-- 导入完成 -->
      <div v-if="ocrDone" class="ocr-done">
        <div class="ocr-done-text">导入完成：成功 {{ ocrDone.success }} 只，共 {{ ocrDone.total }} 只</div>
        <NButton type="primary" size="small" @click="showOcrModal = false">关闭</NButton>
      </div>
      <!-- 识别中 -->
      <div v-else-if="ocrLoading" class="ocr-loading">
        <NSpin size="medium" />
        <span style="margin-left: 12px; color: var(--text2)">正在识别图片中的股票信息...</span>
      </div>
      <!-- 识别结果确认 -->
      <div v-else-if="ocrResults.length > 0" class="ocr-results">
        <div class="ocr-results-header">
          <div class="ocr-results-info">
            <span class="ocr-results-badge">{{ ocrResults.length }}</span>
            <span>只股票识别成功，勾选需要导入的：</span>
          </div>
          <div class="ocr-results-actions-top">
            <NButton size="tiny" quaternary @click="ocrChecked = ocrResults.map(s => s.code)">全选</NButton>
            <NButton size="tiny" quaternary @click="ocrChecked = []">清空</NButton>
          </div>
        </div>
        <div class="ocr-stock-list">
          <label v-for="item in ocrResults" :key="item.code" class="ocr-stock-item" :class="{ checked: ocrChecked.includes(item.code) }">
            <input type="checkbox" :value="item.code" v-model="ocrChecked" class="ocr-checkbox" />
            <span class="ocr-stock-code">{{ item.code }}</span>
            <span class="ocr-stock-name">{{ item.name }}</span>
          </label>
        </div>
        <div class="ocr-actions">
          <span class="ocr-selected-count">已选 <b>{{ ocrChecked.length }}</b> / {{ ocrResults.length }} 只</span>
          <NButton type="primary" :loading="ocrImporting" @click="handleOcrConfirm" :disabled="ocrChecked.length === 0">
            确认导入
          </NButton>
        </div>
      </div>
      <!-- 上传区域 -->
      <div v-else>
        <NUpload
          accept="image/*"
          :max="1"
          :default-upload="false"
          @change="handleOcrUpload"
          :show-file-list="false"
        >
          <NUploadDragger>
            <div class="ocr-upload-content">
              <NIcon size="48" :depth="3"><ImageOutline /></NIcon>
              <div class="ocr-upload-text">点击或拖拽上传自选股截图</div>
              <div class="ocr-upload-hint">支持同花顺、东方财富等软件的自选股列表截图</div>
            </div>
          </NUploadDragger>
        </NUpload>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
/* 桌面端: 整页内容按列填满 pc-main 高度, 让表格内部滚动而非整页滚动 → 表头吸顶 */
.pool-view--fixed {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.pool-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* 合并添加区+筛选区为一个卡片 (v1.7.680): 外卡机构描边, 内两行发丝线隔开, 收紧密度 */
.pool-controls {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
  position: sticky;
  top: 0;
  z-index: 50;
}
.filter-bar {
  background: transparent;
  border-radius: 0;
  box-shadow: none;
  padding: 10px 14px;
  margin-bottom: 0;
  border-bottom: 1px solid var(--border-muted);
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  align-items: end;
}
.pool-filter {
  background: transparent;
  border-radius: 0;
  box-shadow: none;
  padding: 10px 14px;
  margin-bottom: 0;
}
.pf-search {
  flex: 0 1 300px;
  min-width: 180px;
}
.pf-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
}
.pf-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pf-advanced {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}
.pf-adv-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pf-adv-item label {
  font-size: 12px;
  color: var(--text2);
  white-space: nowrap;
}
.pf-tilde {
  color: var(--text2);
}
.filter-fields {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 200px;
}
.filter-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 100px;
}
.filter-item label {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.6);
  white-space: nowrap;
}
.filter-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
}
/* 工具栏分组分隔线 (v1.7.676) */
.fa-divider {
  width: 1px;
  height: 18px;
  background: var(--border-default);
  margin: 0 2px;
  flex-shrink: 0;
}
.search-wrapper {
  position: relative;
}
.search-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  max-height: 200px;
  overflow-y: auto;
  z-index: 200;
  margin-top: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.search-item {
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  touch-action: manipulation;
}
.search-item:hover {
  background: var(--bg);
}
.item-code {
  color: var(--primary);
  margin-right: 8px;
  font-family: monospace;
}
.table-summary {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  font-size: 12px;
  color: var(--text2);
  margin-bottom: 8px;
}
/* 统计栏 + 计数行合并成一行: 统计栏在左撑开, 计数/标签靠右; 窄屏自动换行堆叠 */
.pool-summary-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.pool-summary-row :deep(.pool-stats-bar) {
  flex: 1 1 340px;
  min-width: 0;
}
.pool-summary-row .table-summary {
  margin-bottom: 0;
  flex: 0 0 auto;
}
.ths-empty {
  text-align: center;
  padding: 20px;
  color: var(--text2);
}
.import-progress {
  padding: 12px 0;
}
.progress-detail {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  font-size: 13px;
}
.progress-count {
  color: var(--primary);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.progress-status {
  color: var(--text2);
  font-size: 12px;
}
.ocr-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
}
.ocr-upload-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px;
}
.ocr-upload-text {
  font-size: 14px;
  color: var(--text1);
}
.ocr-upload-hint {
  font-size: 12px;
  color: var(--text2);
}
.ocr-results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.ocr-results-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text2);
}
.ocr-results-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: var(--primary);
  color: var(--on-emphasis);
  font-size: 12px;
  font-weight: 600;
}
.ocr-results-actions-top {
  display: flex;
  gap: 4px;
}
.ocr-stock-list {
  border: 1px solid var(--border);
  border-radius: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.ocr-stock-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.15s;
  border-bottom: 1px solid var(--border);
  touch-action: manipulation;
}
.ocr-stock-item:last-child {
  border-bottom: none;
}
.ocr-stock-item:hover {
  background: var(--bg);
}
.ocr-stock-item.checked {
  background: var(--accent-bg-muted);
}
.ocr-checkbox {
  width: 16px;
  height: 16px;
  accent-color: var(--primary);
  cursor: pointer;
}
.ocr-stock-code {
  font-family: monospace;
  font-size: 13px;
  font-weight: 600;
  color: var(--primary);
  min-width: 60px;
  font-variant-numeric: tabular-nums;
}
.ocr-stock-name {
  font-size: 13px;
  color: var(--text1);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ocr-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.ocr-selected-count {
  font-size: 13px;
  color: var(--text2);
}
.ocr-selected-count b {
  color: var(--primary);
}
.ocr-done {
  text-align: center;
  padding: 32px 0;
}
.ocr-done-text {
  font-size: 15px;
  margin-bottom: 16px;
  color: var(--text1);
  font-weight: 500;
}
/* 自选对比 */
.cmp-picker {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.cmp-pick-btn {
  display: inline-flex;
  align-items: center;
  padding: 5px 14px;
  border-radius: 6px;
  background: var(--primary);
  color: var(--on-emphasis);
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  touch-action: manipulation;
}
.cmp-pick-btn:hover {
  opacity: 0.9;
}
.cmp-pick-hint {
  font-size: 12px;
  color: var(--text2);
}
.cmp-last-file {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text2);
}
.cmp-last-file code {
  font-family: monospace;
  font-size: 11px;
  background: var(--bg);
  padding: 1px 4px;
  border-radius: 3px;
}
.cmp-pick-hint code,
.ths-empty code {
  font-family: monospace;
  font-size: 11px;
  background: var(--bg);
  padding: 1px 4px;
  border-radius: 3px;
}
.cmp-source {
  font-size: 11px;
  color: var(--text2);
  background: var(--bg);
  padding: 1px 6px;
  border-radius: 3px;
  margin-right: 6px;
}
.cmp-wrap {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.cmp-summary {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--text2);
}
.cmp-summary b {
  color: var(--primary);
}
.cmp-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: var(--text1);
  margin-bottom: 6px;
}
.cmp-head-actions {
  display: flex;
  gap: 4px;
}
.cmp-list {
  border: 1px solid var(--border);
  border-radius: 8px;
  max-height: 220px;
  overflow-y: auto;
}
.cmp-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  cursor: pointer;
  transition: background 0.15s;
  border-bottom: 1px solid var(--border);
  touch-action: manipulation;
}
.cmp-item:last-child {
  border-bottom: none;
}
.cmp-item:hover {
  background: var(--bg);
}
.cmp-item.checked {
  background: var(--accent-bg-muted);
}
.cmp-checkbox {
  width: 16px;
  height: 16px;
  accent-color: var(--primary);
  cursor: pointer;
}
.cmp-code {
  font-family: monospace;
  font-size: 13px;
  font-weight: 600;
  color: var(--primary);
  min-width: 60px;
  font-variant-numeric: tabular-nums;
}
.cmp-name {
  font-size: 13px;
  color: var(--text1);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cmp-empty {
  text-align: center;
  padding: 16px;
  color: var(--text2);
  font-size: 13px;
  background: var(--bg);
  border-radius: 8px;
}
.cmp-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}
.cmp-count {
  font-size: 13px;
  color: var(--text2);
}
.cmp-count b {
  color: var(--primary);
}

/* ====================================================================
 * 移动端适配 (手机 < 768px)
 * 只做窄屏覆盖: 宽表已由 isPhone 切 StockList 卡片版; 这里处理双层筛选区
 * 的单列/全宽堆叠 + 胶囊横向滚动 + 触摸目标≥40px, 保证无横向溢出。
 * 不改桌面布局与任何筛选逻辑。
 * ================================================================== */
@media (max-width: 768px) {
  .pool-view {
    max-width: 100%;
    overflow-x: hidden;
  }

  /* ── 添加/操作条: 不吸顶(省小屏纵向空间), 控件全宽换行 ── */
  .filter-bar {
    position: static;
    padding: 12px 14px;
    gap: 10px 12px;
    align-items: stretch;
  }
  .filter-fields {
    flex: 1 1 100%;
    min-width: 0;
    width: 100%;
  }
  .filter-item {
    flex: 1 1 100%;
    min-width: 0;
  }
  .filter-item[style] {
    flex: 1 1 100% !important;
    min-width: 0 !important;
  }
  .filter-actions {
    width: 100%;
    flex-wrap: wrap;
    justify-content: flex-start;
    align-items: stretch;
    gap: 8px;
  }
  /* 添加条里的下拉不再固定 80px, 跟随内容; 按钮触摸目标≥40px */
  .filter-actions :deep(.n-base-selection) {
    min-height: 40px;
  }
  .filter-bar :deep(.n-button),
  .filter-bar :deep(.n-input .n-input__input-el),
  .filter-bar :deep(.n-base-selection) {
    min-height: 40px;
  }

  /* ── 股票池筛选: 单列堆叠, 每个控件占满整行 ── */
  .pool-filter {
    padding: 10px 14px;
  }
  .pf-row {
    gap: 10px;
  }
  .pf-search {
    flex: 1 1 100%;
    min-width: 0;
    width: 100%;
  }
  /* 状态/涨跌 分段单选占满整行, 按钮等分 */
  .pf-row :deep(.n-radio-group) {
    display: flex;
    width: 100%;
  }
  .pf-row :deep(.n-radio-group .n-radio-button) {
    flex: 1 1 0;
    text-align: center;
  }
  /* 胶囊组横向滚动, 不换行不撑破 */
  .pf-chips {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    width: 100%;
    padding-bottom: 4px;
    scrollbar-width: thin;
  }
  .pf-chips :deep(.n-button) {
    flex: 0 0 auto;
  }
  /* 高级筛选/清空 等按钮全宽起排 */
  .pf-row > :deep(.n-button) {
    flex: 1 1 auto;
  }

  /* 触摸目标≥40px: 筛选区所有可点控件 */
  .pool-filter :deep(.n-button),
  .pool-filter :deep(.n-input .n-input__input-el),
  .pool-filter :deep(.n-radio-button),
  .pool-filter :deep(.n-base-selection),
  .pool-filter :deep(.n-input-number) {
    min-height: 40px;
  }
  .pool-filter :deep(.n-radio-button__state-border),
  .pool-filter :deep(.n-radio-group) {
    min-height: 40px;
  }

  /* ── 高级筛选面板: 每项占整行, 标签左/控件右 ── */
  .pf-advanced {
    gap: 10px 12px;
  }
  .pf-adv-item {
    flex: 1 1 100%;
    justify-content: space-between;
  }
  .pf-adv-item label {
    flex: 0 0 auto;
  }
  /* 面板里内联固定宽度控件在窄屏自适应, 不溢出 */
  .pf-adv-item :deep(.n-select),
  .pf-adv-item :deep(.n-input) {
    flex: 1 1 auto;
    min-width: 0;
  }

  /* 统计/计数行: 计数靠左顺排, 避免右对齐挤成竖条 */
  .table-summary,
  .pool-summary-row .table-summary {
    justify-content: flex-start;
  }

  /* 弹窗内长内容各自横向滚动, 不撑破页面 */
  .cmp-list,
  .ocr-stock-list {
    max-width: 100%;
    overflow-x: auto;
  }
}
</style>
