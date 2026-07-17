<script setup lang="ts">
// 问财候选榜 (v1.7.540; v1.7.546 支持自己输入条件) — 同花顺问财(iwencai)自然语言选股。
// 顶部输入框: 即时搜索(不保存) + 存为常驻榜(进定时刷新, 仅自己可见)。
// 下方: 预置榜(全局) + 我的自定义榜(可改/删), 勾选一键加自选, 点股弹K线。
import { ref, onMounted } from 'vue'
import { NButton, NIcon, NSkeleton, NEmpty, NTag, NInput, NModal, NCard, NPopconfirm } from 'naive-ui'
import { RefreshOutline, SearchOutline, WarningOutline, CreateOutline, TrashOutline, BookmarkOutline, PlayOutline } from '@vicons/ionicons5'
import {
  fetchWencai, addWencaiToPool, searchWencai, scanWencai, createWencaiQuery, updateWencaiQuery, deleteWencaiQuery,
  type WencaiStrategy, type WencaiItem,
} from '../api/wencai'
import WencaiStockList from '../components/common/WencaiStockList.vue'
import { useUiStore } from '../stores/ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'

const ui = useUiStore()
const message = useGlobalMessage()

const strategies = ref<WencaiStrategy[]>([])
const loading = ref(false)
const adding = ref(false)
const loaded = ref(false)
const scanning = ref(false)
const autoRefreshing = ref(false)   // 页面事件触发的静默刷新(数据非今日时)

// 即时搜索
const searchInput = ref('')
const searching = ref(false)
const searchResult = ref<{ query: string; items: WencaiItem[] } | null>(null)

// 语句编辑弹窗(新增/改 共用)
const showEditor = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const editorId = ref<number | null>(null)
const editorName = ref('')
const editorQuery = ref('')
const saving = ref(false)

function fmtTime(t: string | null) {
  return t ? String(t).slice(5, 16).replace('T', ' ') : ''
}
function openChart(it: WencaiItem) {
  ui.openStock(it.code, it.name)
}

async function load() {
  loading.value = true
  try {
    strategies.value = (await fetchWencai()).strategies
  } catch {
    message.error('问财候选榜加载失败')
  } finally {
    loading.value = false
    loaded.value = true
  }
}

async function runScan() {
  scanning.value = true
  try {
    const r = await scanWencai()
    if (r.failed.length) message.warning(`已刷新 ${r.succeeded}/${r.total} 条，失败：${r.failed.join('、')}`)
    else message.success(`已跑问财刷新全部 ${r.total} 条榜`)
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '刷新失败（问财接口可能暂时不可用）')
  } finally {
    scanning.value = false
  }
}

function localToday(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// 页面事件触发: 打开页面时若候选榜不是今天的(或为空), 静默自动刷一次;
// 成功即替换、失败保留旧数据不打扰(问财逆向接口偶发风控是常态)。取代后台定时扫描。
function isStale(): boolean {
  if (!strategies.value.length) return true
  const t = localToday()
  return strategies.value.some(s => (s.trade_date || '') !== t)
}

async function autoRefreshIfStale() {
  if (!isStale() || autoRefreshing.value) return
  autoRefreshing.value = true
  try {
    await scanWencai()
    await load()
  } catch {
    // 静默: 保留已显示的旧数据, 顶部会标注数据日期
  } finally {
    autoRefreshing.value = false
  }
}

async function doSearch() {
  const q = searchInput.value.trim()
  if (!q) { message.warning('请输入选股条件'); return }
  searching.value = true
  try {
    const r = await searchWencai(q)
    searchResult.value = { query: r.query, items: r.items }
    if (!r.items.length) message.info('没查到符合条件的票')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '搜索失败')
  } finally {
    searching.value = false
  }
}

async function addPicks(picks: { code: string; name: string }[]) {
  if (!picks.length) { message.warning('请先勾选要加入自选的股票'); return }
  adding.value = true
  try {
    const res = await addWencaiToPool(picks)
    message.success(`已加入自选池 ${res.added} 只`)
  } catch {
    message.error('加入自选失败')
  } finally {
    adding.value = false
  }
}

// ── 语句编辑 ──
function openCreate(prefill = '') {
  editorMode.value = 'create'
  editorId.value = null
  editorName.value = ''
  editorQuery.value = prefill
  showEditor.value = true
}
function openEdit(s: WencaiStrategy) {
  editorMode.value = 'edit'
  editorId.value = s.query_id
  editorName.value = s.strategy_name
  editorQuery.value = s.query_text
  showEditor.value = true
}
async function saveEditor() {
  const q = editorQuery.value.trim()
  if (!q) { message.warning('请输入选股条件'); return }
  saving.value = true
  try {
    if (editorMode.value === 'create') {
      const r = await createWencaiQuery(editorName.value.trim(), q)
      message.success(r.run?.ok ? `已存为常驻榜，选出 ${r.run.stock_count} 只` : '已存为常驻榜（本次拉取失败，下次定时重试）')
    } else if (editorId.value != null) {
      await updateWencaiQuery(editorId.value, { name: editorName.value.trim(), query: q })
      message.success('已更新')
    }
    showEditor.value = false
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
async function removeQuery(s: WencaiStrategy) {
  if (s.query_id == null) return
  try {
    await deleteWencaiQuery(s.query_id)
    message.success('已删除')
    await load()
  } catch {
    message.error('删除失败')
  }
}

function saveSearchAsQuery() {
  openCreate(searchResult.value?.query || searchInput.value.trim())
}

onMounted(async () => {
  await load()          // 先秒显现有数据
  await autoRefreshIfStale()   // 非今日则静默刷新替换
})
</script>

<template>
  <div class="wencai-view">
    <div class="page-head">
      <div class="title">
        <NIcon :component="SearchOutline" :size="20" />
        <h2>问财候选榜</h2>
      </div>
      <div class="head-ops">
        <NButton size="small" :loading="loading" @click="load">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          刷新页面
        </NButton>
        <NButton size="small" type="primary" :loading="scanning" @click="runScan">
          <template #icon><NIcon :component="PlayOutline" /></template>
          立即跑问财
        </NButton>
      </div>
    </div>
    <p class="page-desc">
      同花顺问财(iwencai)自然语言选股。打开页面时若候选不是今天的会<b>自动刷新一次</b>；也可随时点「立即跑问财」手动重刷。
      下方输入条件可即时搜索;看中的条件「存为常驻榜」(仅你自己可见)。
    </p>
    <div v-if="autoRefreshing" class="auto-refresh-tip">
      <NIcon :component="RefreshOutline" class="spin" /> 正在更新今日候选…（问财偶发限流，稍等；失败会保留现有数据）
    </div>

    <!-- 输入条件 -->
    <div class="search-box">
      <NInput v-model:value="searchInput" type="text" clearable
              placeholder="输入问财条件，如：换手率大于8% 且 5日内涨幅大于20% 且 非ST"
              @keydown.enter="doSearch" />
      <NButton type="primary" :loading="searching" @click="doSearch">
        <template #icon><NIcon :component="SearchOutline" /></template>
        即时搜索
      </NButton>
      <NButton :disabled="!searchInput.trim()" @click="saveSearchAsQuery">
        <template #icon><NIcon :component="BookmarkOutline" /></template>
        存为常驻榜
      </NButton>
    </div>

    <!-- 即时搜索结果 -->
    <section v-if="searchResult" class="strategy-card search-card">
      <div class="s-head">
        <div class="s-title">
          <NTag size="small" :bordered="false" type="success">即时搜索</NTag>
          <span class="s-name">{{ searchResult.query }}</span>
          <NTag size="small" :bordered="false">{{ searchResult.items.length }} 只</NTag>
        </div>
        <NButton size="tiny" quaternary @click="searchResult = null">关闭结果</NButton>
      </div>
      <div v-if="!searchResult.items.length" class="s-empty">没查到符合条件的票。</div>
      <WencaiStockList v-else :items="searchResult.items" :adding="adding"
                       @add="addPicks" @open="openChart" />
    </section>

    <NSkeleton v-if="loading && !loaded" text :repeat="6" style="margin-top: 16px" />

    <NEmpty v-else-if="loaded && !strategies.length" class="empty" description="暂无候选榜">
      <template #extra>
        <p class="empty-hint">还没有任何榜。上面输入条件搜索, 或「存为常驻榜」建一个每天自动刷新的榜。</p>
      </template>
    </NEmpty>

    <div v-else class="strategies">
      <section v-for="s in strategies" :key="s.strategy_id" class="strategy-card">
        <div class="s-head">
          <div class="s-title">
            <span class="s-name">{{ s.strategy_name }}</span>
            <NTag size="small" :bordered="false" :type="s.is_custom ? 'warning' : 'info'">
              {{ s.is_custom ? '我的' : '预置' }} · {{ s.stock_count }}只
            </NTag>
            <span v-if="s.computed_at" class="s-meta">更新 {{ fmtTime(s.computed_at) }}</span>
          </div>
          <div v-if="s.is_custom" class="s-ops">
            <NButton size="tiny" quaternary @click="openEdit(s)">
              <template #icon><NIcon :component="CreateOutline" /></template>改
            </NButton>
            <NPopconfirm @positive-click="removeQuery(s)">
              <template #trigger>
                <NButton size="tiny" quaternary type="error">
                  <template #icon><NIcon :component="TrashOutline" /></template>删
                </NButton>
              </template>
              确定删除「{{ s.strategy_name }}」这条常驻榜?
            </NPopconfirm>
          </div>
        </div>
        <div class="s-query">
          <NIcon :component="SearchOutline" :size="12" /><span>{{ s.query_text }}</span>
        </div>
        <div v-if="s.last_error" class="s-error">
          <NIcon :component="WarningOutline" :size="13" /> 本次刷新失败({{ s.last_error }}), 下方为上次成功结果。
        </div>
        <div v-if="!s.items.length" class="s-empty">该策略当前无符合条件的票。</div>
        <WencaiStockList v-else :items="s.items" :adding="adding" @add="addPicks" @open="openChart" />
      </section>
    </div>

    <p class="foot-hint">
      候选由问财按其相关度排序, 仅供选股参考, 非买卖点信号; 真正买点以监控看板/实时推送为准。
    </p>

    <!-- 新增/编辑 常驻语句 -->
    <NModal v-model:show="showEditor">
      <NCard style="max-width: 540px" :title="editorMode === 'create' ? '存为常驻榜' : '编辑常驻榜'" closable @close="showEditor = false">
        <div class="editor">
          <label>榜名称（可留空，自动取条件前几字）</label>
          <NInput v-model:value="editorName" placeholder="如：超跌反弹候选" />
          <label>问财条件（自然语言，大白话即可）</label>
          <NInput v-model:value="editorQuery" type="textarea" :rows="3"
                  placeholder="如：连续3天缩量 且 站上20日线 且 流通市值小于80亿 且 非ST" />
          <p class="editor-hint">保存后会立即跑一次出榜, 之后每交易日盘中约15分钟自动刷新。</p>
        </div>
        <template #footer>
          <div class="editor-foot">
            <NButton @click="showEditor = false">取消</NButton>
            <NButton type="primary" :loading="saving" @click="saveEditor">保存</NButton>
          </div>
        </template>
      </NCard>
    </NModal>
  </div>
</template>

<style scoped>
.wencai-view { max-width: 1200px; margin: 0 auto; padding: 16px; }
.page-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.title { display: flex; align-items: center; gap: 8px; }
.title h2 { margin: 0; font-size: 18px; }
.head-ops { display: flex; gap: 8px; flex-shrink: 0; }
.page-desc { color: var(--fg-muted); font-size: 13px; line-height: 1.6; margin: 8px 0 0; }
.auto-refresh-tip { display: flex; align-items: center; gap: 6px; margin: 10px 0 0; font-size: 13px; color: var(--warn-fg); }
.auto-refresh-tip .spin { animation: wc-spin 1s linear infinite; }
@keyframes wc-spin { to { transform: rotate(360deg); } }

.search-box { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.search-box :deep(.n-input) { flex: 1; min-width: 220px; }

.empty { margin-top: 40px; }
.empty-hint { max-width: 460px; color: var(--fg-subtle); font-size: 12.5px; line-height: 1.7; }

.strategies { margin-top: 16px; display: flex; flex-direction: column; gap: 16px; }
.strategy-card { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 8px; padding: 12px 14px; }
.search-card { margin-top: 16px; border-color: var(--success-fg); }
.s-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.s-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.s-name { font-size: 15px; font-weight: 600; color: var(--fg-default); word-break: break-all; }
.s-meta { font-size: 11px; color: var(--fg-subtle); }
.s-ops { display: flex; gap: 4px; flex-shrink: 0; }

.s-query { display: flex; align-items: center; gap: 5px; margin: 8px 0; font-size: 12px; color: var(--accent-fg);
  background: var(--accent-bg-muted); padding: 4px 9px; border-radius: 6px; }
.s-query span { word-break: break-all; }
.s-error { display: flex; align-items: center; gap: 5px; margin-bottom: 8px; font-size: 12px; color: var(--warn-fg); }
.s-empty { color: var(--fg-subtle); font-size: 13px; padding: 8px 0; }

.foot-hint { margin-top: 16px; font-size: 11.5px; color: var(--fg-subtle); line-height: 1.6;
  background: var(--bg-sunken); padding: 8px 12px; border-radius: 6px; }

.editor { display: flex; flex-direction: column; gap: 6px; }
.editor label { font-size: 12px; color: var(--fg-muted); margin-top: 6px; }
.editor-hint { font-size: 11.5px; color: var(--fg-subtle); margin: 4px 0 0; }
.editor-foot { display: flex; justify-content: flex-end; gap: 8px; }

@media (max-width: 768px) {
  .wencai-view { padding: 10px; }
}
</style>
