<script setup lang="ts">
// 问财候选榜 (v1.7.540) — 同花顺问财(iwencai)自然语言选股结果。
// 后端 scan_wencai 定时跑配置里的选股语句, 各成一榜落库; 本页读快照展示, 支持勾选一键加入自选池。
// 候选全局共享(选股语句在系统设置), 加自选是写当前用户的股票池。
import { ref, computed, onMounted, reactive } from 'vue'
import { NButton, NIcon, NSkeleton, NEmpty, NCheckbox, NTag, NTooltip } from 'naive-ui'
import { RefreshOutline, AddCircleOutline, SearchOutline, WarningOutline } from '@vicons/ionicons5'
import { fetchWencai, addWencaiToPool, type WencaiStrategy, type WencaiItem } from '../api/wencai'
import { useUiStore } from '../stores/ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'

const ui = useUiStore()
const message = useGlobalMessage()

const strategies = ref<WencaiStrategy[]>([])
const loading = ref(false)
const adding = ref(false)
const loaded = ref(false)
// 每策略已勾选的 code 集合
const selected = reactive<Record<string, Set<string>>>({})

const hasAny = computed(() => strategies.value.some(s => s.items.length > 0))

function pctText(v: number | null) {
  if (v == null) return '—'
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}
function amountText(v?: number) {
  if (v == null) return ''
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`
  return `${v}`
}
function fmtTime(t: string | null) {
  return t ? String(t).slice(5, 16).replace('T', ' ') : ''
}

function selSet(sid: string): Set<string> {
  if (!selected[sid]) selected[sid] = new Set()
  return selected[sid]
}
function isSel(sid: string, code: string) {
  return selSet(sid).has(code)
}
function toggle(sid: string, code: string) {
  const s = selSet(sid)
  s.has(code) ? s.delete(code) : s.add(code)
}
function allSelected(s: WencaiStrategy) {
  return s.items.length > 0 && s.items.every(it => isSel(s.strategy_id, it.code))
}
function toggleAll(s: WencaiStrategy) {
  const set = selSet(s.strategy_id)
  if (allSelected(s)) set.clear()
  else s.items.forEach(it => set.add(it.code))
}
function selCount(sid: string) {
  return selSet(sid).size
}

function openChart(it: WencaiItem) {
  ui.openStock(it.code, it.name)
}

async function load() {
  loading.value = true
  try {
    const { strategies: data } = await fetchWencai()
    strategies.value = data
  } catch {
    message.error('问财候选榜加载失败')
  } finally {
    loading.value = false
    loaded.value = true
  }
}

async function addSelected(s: WencaiStrategy) {
  const set = selSet(s.strategy_id)
  const picks = s.items.filter(it => set.has(it.code)).map(it => ({ code: it.code, name: it.name }))
  if (!picks.length) {
    message.warning('请先勾选要加入自选的股票')
    return
  }
  adding.value = true
  try {
    const res = await addWencaiToPool(picks)
    message.success(`已加入自选池 ${res.added} 只`)
    set.clear()
  } catch {
    message.error('加入自选失败')
  } finally {
    adding.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="wencai-view">
    <div class="page-head">
      <div class="title">
        <NIcon :component="SearchOutline" :size="20" />
        <h2>问财候选榜</h2>
      </div>
      <NButton size="small" :loading="loading" @click="load">
        <template #icon><NIcon :component="RefreshOutline" /></template>
        刷新
      </NButton>
    </div>
    <p class="page-desc">
      同花顺问财(iwencai)自然语言选股结果, 按选股语句分组。后端交易日盘中定时刷新,
      勾选感兴趣的票一键加入自选池。选股语句在「系统设置」中维护。
    </p>

    <NSkeleton v-if="loading && !loaded" text :repeat="6" style="margin-top: 16px" />

    <NEmpty v-else-if="loaded && !strategies.length" class="empty"
            description="暂无候选数据">
      <template #extra>
        <p class="empty-hint">
          问财候选榜尚未启用或未产出。需在部署机安装 Node + pywencai, 并在系统设置开启
          wencai_screening。开启后交易日盘中每约 15 分钟自动出榜。
        </p>
      </template>
    </NEmpty>

    <div v-else class="strategies">
      <section v-for="s in strategies" :key="s.strategy_id" class="strategy-card">
        <div class="s-head">
          <div class="s-title">
            <span class="s-name">{{ s.strategy_name }}</span>
            <NTag size="small" :bordered="false" type="info">{{ s.stock_count }} 只</NTag>
            <span v-if="s.computed_at" class="s-meta">更新 {{ fmtTime(s.computed_at) }}</span>
          </div>
          <div class="s-actions">
            <NCheckbox v-if="s.items.length" :checked="allSelected(s)" @update:checked="toggleAll(s)">
              全选
            </NCheckbox>
            <NButton size="small" type="primary" :disabled="!selCount(s.strategy_id)"
                     :loading="adding" @click="addSelected(s)">
              <template #icon><NIcon :component="AddCircleOutline" /></template>
              加入自选{{ selCount(s.strategy_id) ? `(${selCount(s.strategy_id)})` : '' }}
            </NButton>
          </div>
        </div>

        <div class="s-query">
          <NIcon :component="SearchOutline" :size="12" />
          <span>{{ s.query_text }}</span>
        </div>

        <div v-if="s.last_error" class="s-error">
          <NIcon :component="WarningOutline" :size="13" />
          本次刷新失败({{ s.last_error }}), 下方为上一次成功结果。
        </div>

        <div v-if="!s.items.length" class="s-empty">该策略当前无符合条件的票。</div>

        <div v-else class="grid">
          <div v-for="it in s.items" :key="it.code"
               :class="['cell', { picked: isSel(s.strategy_id, it.code) }]">
            <div class="cell-top">
              <NCheckbox :checked="isSel(s.strategy_id, it.code)"
                         @update:checked="toggle(s.strategy_id, it.code)"
                         @click.stop />
              <div class="stock" role="button" tabindex="0"
                   :aria-label="`${it.name} ${it.code} 详情`"
                   @click="openChart(it)" @keydown.enter="openChart(it)">
                <span class="name">{{ it.name }}</span>
                <span class="code">{{ it.code }}</span>
              </div>
              <div class="quote">
                <span class="price">{{ it.price != null ? it.price.toFixed(2) : '—' }}</span>
                <span class="pct" :class="(it.pct_change ?? 0) >= 0 ? 'up' : 'down'">{{ pctText(it.pct_change) }}</span>
              </div>
            </div>
            <div class="tags">
              <NTooltip v-if="it.extra.tech_pattern" trigger="hover">
                <template #trigger><span class="tag t-tech">{{ it.extra.tech_pattern.split('||')[0] }}</span></template>
                技术形态: {{ it.extra.tech_pattern.replace(/\|\|/g, ' · ') }}
              </NTooltip>
              <NTooltip v-if="it.extra.buy_signal" trigger="hover">
                <template #trigger><span class="tag t-buy">{{ it.extra.buy_signal.split('||')[0] }}</span></template>
                买入信号: {{ it.extra.buy_signal.replace(/\|\|/g, ' · ') }}
              </NTooltip>
              <span v-if="it.extra.concepts" class="tag t-concept">{{ it.extra.concepts.split(/[;,]/)[0] }}</span>
              <span v-else-if="it.extra.industry" class="tag t-concept">{{ it.extra.industry.split('-').slice(-1)[0] }}</span>
              <span v-if="it.extra.turnover != null" class="tag t-num">换手{{ it.extra.turnover.toFixed(1) }}%</span>
              <span v-if="it.extra.amount != null" class="tag t-num">额{{ amountText(it.extra.amount) }}</span>
            </div>
          </div>
        </div>
      </section>
    </div>

    <p class="foot-hint">
      候选由问财按其相关度排序, 仅供选股参考, 非买卖点信号; 真正买点以监控看板/实时推送为准。
    </p>
  </div>
</template>

<style scoped>
.wencai-view { max-width: 1200px; margin: 0 auto; padding: 16px; }
.page-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.title { display: flex; align-items: center; gap: 8px; }
.title h2 { margin: 0; font-size: 18px; }
.page-desc { color: #888; font-size: 13px; line-height: 1.6; margin: 8px 0 0; }

.empty { margin-top: 40px; }
.empty-hint { max-width: 460px; color: #999; font-size: 12.5px; line-height: 1.7; }

.strategies { margin-top: 16px; display: flex; flex-direction: column; gap: 16px; }
.strategy-card { background: #fff; border: 1px solid var(--border, #efeff5); border-radius: 8px; padding: 12px 14px; }
.s-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.s-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.s-name { font-size: 15px; font-weight: 600; color: rgba(0,0,0,0.85); }
.s-meta { font-size: 11px; color: #999; }
.s-actions { display: flex; align-items: center; gap: 10px; }

.s-query { display: flex; align-items: center; gap: 5px; margin-top: 8px; font-size: 12px; color: #2e9eff;
  background: rgba(46,158,255,0.06); padding: 4px 9px; border-radius: 6px; }
.s-query span { word-break: break-all; }
.s-error { display: flex; align-items: center; gap: 5px; margin-top: 8px; font-size: 12px; color: #f0a020; }
.s-empty { margin-top: 12px; color: #999; font-size: 13px; padding: 8px 0; }

.grid { margin-top: 10px; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 8px; }
.cell { border: 1px solid #efeff5; border-radius: 7px; padding: 8px 10px; transition: all 0.15s; }
.cell.picked { background: rgba(46,158,255,0.05); border-color: rgba(46,158,255,0.4); }
.cell-top { display: flex; align-items: center; gap: 8px; }
.stock { display: flex; align-items: baseline; gap: 6px; min-width: 0; flex: 1; cursor: pointer; touch-action: manipulation; }
.name { font-size: 13px; font-weight: 600; color: rgba(0,0,0,0.85); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.code { font-size: 11px; color: #999; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.quote { display: flex; align-items: baseline; gap: 6px; flex-shrink: 0; }
.price { font-size: 13px; font-weight: 600; color: rgba(0,0,0,0.8); font-variant-numeric: tabular-nums; }
.pct { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }

.tags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.tag { font-size: 10.5px; padding: 1px 7px; border-radius: 9px; white-space: nowrap; }
.t-tech { background: rgba(208,48,80,0.1); color: #d03050; }
.t-buy { background: rgba(24,160,88,0.12); color: #18a058; }
.t-concept { background: rgba(46,158,255,0.1); color: #2e9eff; }
.t-num { background: rgba(0,0,0,0.05); color: #888; font-variant-numeric: tabular-nums; }

.foot-hint { margin-top: 16px; font-size: 11.5px; color: #999; line-height: 1.6;
  background: rgba(0,0,0,0.02); padding: 8px 12px; border-radius: 6px; }

.up { color: #d03050; }
.down { color: #18a058; }

@media (max-width: 768px) {
  .wencai-view { padding: 10px; }
  .grid { grid-template-columns: 1fr; }
  .s-actions { width: 100%; justify-content: space-between; }
}
</style>
