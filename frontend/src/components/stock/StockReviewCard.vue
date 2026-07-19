<script setup lang="ts">
// AI 个股研判卡: 点一只票，把它的信号历史/同形态胜率/财务红旗/板块强弱/持仓成本
// 综合摆成事实速览 + 一段 AI 大白话叙述。摆事实不预测，非投资建议。
// 用法: <StockReviewCard v-model:show="show" :code="code" :name="name" />
import { ref, computed, watch } from 'vue'
import { NModal, NEmpty, NSpin, NTag } from 'naive-ui'
import { getStockReview, type StockReview } from '../../api/stockReview'
import { useGlobalMessage } from '../../composables/useGlobalMessage'
import { useResponsive } from '../../composables/useResponsive'

const props = defineProps<{ show: boolean; code: string; name: string }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()

const message = useGlobalMessage()
const { isMobile } = useResponsive()

const loading = ref(false)
const review = ref<StockReview | null>(null)

const show = computed({
  get: () => props.show,
  set: (v: boolean) => emit('update:show', v),
})

async function load() {
  if (!props.code) return
  loading.value = true
  review.value = null
  try {
    review.value = await getStockReview(props.code)
  } catch (e: any) {
    if (e?.response?.status === 429) {
      message.error('今日研判次数已达上限')
    } else {
      message.error('研判生成失败，请稍后重试')
    }
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (v) => { if (v) load() })

// ── 展示辅助: null/undefined/NaN 一律渲染为 "—", 不出现 NaN/null/undefined 字样 ──
function fmt(v: number | string | null | undefined, suffix = ''): string {
  if (v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v))) return '—'
  return `${v}${suffix}`
}
function pctColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'var(--text3)'
  return v >= 0 ? 'var(--up-fg)' : 'var(--down-fg)'
}

const facts = computed(() => review.value?.facts || null)
const narrative = computed(() => review.value?.narrative || null)

// 1) 信号历史
const signalRows = computed(() => facts.value?.signal_history?.recent || [])
const signalN = computed(() => facts.value?.signal_history?.n ?? 0)
const directionText = (d: string) => (d === 'buy' ? '买入' : d === 'sell' ? '卖出' : d || '—')

// 2) 同形态胜率
const winrateRows = computed(() => facts.value?.model_winrate || [])

// 3) 财务红旗
const riskFlags = computed(() => facts.value?.risk_flags || null)
const riskHasData = computed(() => !!riskFlags.value && (riskFlags.value as any).has_data === true)

// 4) 板块强弱
const sector = computed(() => facts.value?.sector || null)
const hotThemes = computed(() => sector.value?.hot_themes || [])

// 5) 持仓成本
const holding = computed(() => facts.value?.holding || null)
const isHolding = computed(() => !!holding.value && (holding.value as any).is_holding === true)

// 6) 临近买点
const nearBuy = computed(() => facts.value?.near_buy || null)
const isApproaching = computed(() => !!nearBuy.value && (nearBuy.value as any).approaching === true)

const hasAnyFacts = computed(() => !!facts.value)
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    style="max-width: 640px; width: 94vw"
    :block-scroll="false"
    title="AI 个股研判"
    @update:show="(v: boolean) => (show = v)"
  >
    <template #header-extra>
      <span class="src-code">{{ code }} {{ name }}</span>
    </template>

    <NSpin :show="loading">
      <template v-if="!hasAnyFacts && !loading">
        <NEmpty description="暂无研判数据" style="margin: 32px 0;" />
      </template>

      <template v-else-if="facts">
        <!-- 事实速览 -->
        <div class="src-section">
          <div class="src-title">信号历史</div>
          <div v-if="signalRows.length" class="src-signals">
            <NTag v-for="(s, i) in signalRows" :key="i" size="small" :bordered="false"
                  :type="s.direction === 'buy' ? 'error' : s.direction === 'sell' ? 'success' : 'default'">
              {{ s.date || '—' }} {{ directionText(s.direction) }} {{ s.signal_name || '—' }}
            </NTag>
          </div>
          <div v-else class="src-empty">无记录</div>
          <div class="src-hint">累计 {{ signalN }} 条历史信号</div>
        </div>

        <div class="src-section">
          <div class="src-title">同形态胜率(全市场近3月)</div>
          <div v-if="winrateRows.length" :class="isMobile ? 'src-mcards' : 'src-kv'">
            <div v-for="(m, i) in winrateRows" :key="i" class="src-row">
              <span>{{ m.model_name || '—' }}</span>
              <b>{{ fmt(m.win_rate_3m, m.win_rate_3m === null || m.win_rate_3m === undefined ? '' : '%') }}（{{ fmt(m.n_3m, m.n_3m === null || m.n_3m === undefined ? '' : '笔') }}）</b>
            </div>
          </div>
          <div v-else class="src-empty">无记录</div>
        </div>

        <div class="src-section">
          <div class="src-title">财务红旗</div>
          <div v-if="!riskHasData" class="src-empty">无财务红旗记录</div>
          <div v-else class="src-risk">
            <div class="src-row"><span>风险分</span><b>{{ fmt((riskFlags as any)?.score) }}</b></div>
            <div v-if="((riskFlags as any)?.flags || []).length" class="src-signals">
              <NTag v-for="(f, i) in (riskFlags as any).flags" :key="i" size="small" type="warning" :bordered="false">{{ f }}</NTag>
            </div>
            <div v-else class="src-hint">未触发具体红旗项</div>
          </div>
        </div>

        <div class="src-section">
          <div class="src-title">板块强弱</div>
          <div class="src-kv">
            <div class="src-row"><span>板块强度</span><b>{{ fmt(sector?.board_strength) }}</b></div>
            <div class="src-row"><span>板块内名次</span><b>{{ fmt(sector?.sector_rank) }}</b></div>
            <div class="src-row" v-if="hotThemes.length"><span>热门题材</span><b>{{ hotThemes.join('、') }}</b></div>
          </div>
        </div>

        <div class="src-section" v-if="isHolding">
          <div class="src-title">持仓成本</div>
          <div class="src-kv">
            <div class="src-row"><span>成本价</span><b>{{ fmt((holding as any)?.cost) }}</b></div>
            <div class="src-row"><span>浮动盈亏</span><b :style="{ color: pctColor((holding as any)?.float_pct) }">{{ fmt((holding as any)?.float_pct, (holding as any)?.float_pct == null ? '' : '%') }}</b></div>
            <div class="src-row"><span>建仓买点</span><b>{{ fmt((holding as any)?.entry_model) }}</b></div>
          </div>
        </div>
        <div class="src-section" v-else>
          <div class="src-title">持仓成本</div>
          <div class="src-empty">未持仓</div>
        </div>

        <div class="src-section" v-if="isApproaching">
          <div class="src-title">临近买点</div>
          <div class="src-kv">
            <div class="src-row"><span>模型</span><b>{{ fmt((nearBuy as any)?.model) }}</b></div>
            <div class="src-row"><span>距离</span><b>{{ fmt((nearBuy as any)?.gap_pct, (nearBuy as any)?.gap_pct == null ? '' : '%') }}</b></div>
          </div>
        </div>

        <div class="src-section narrative-section">
          <div class="src-title">AI 研判小结</div>
          <p v-if="narrative" class="narrative-text">{{ narrative }}</p>
          <p v-else class="narrative-fallback">AI 叙述暂不可用（数据仍完整）</p>
        </div>
      </template>
    </NSpin>

    <div class="disclaimer">客观历史数据 + AI 归纳，非投资建议、不预测涨跌</div>
  </NModal>
</template>

<style scoped>
.src-code { font-size: 12px; color: var(--text3); font-family: monospace; }
.src-section { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; margin-bottom: 10px; }
.src-title { font-size: 13px; font-weight: 700; color: var(--text1); margin-bottom: 8px; }
.src-empty { font-size: 12px; color: var(--text3); }
.src-hint { font-size: 12px; color: var(--text3); margin-top: 6px; }
.src-signals { display: flex; flex-wrap: wrap; gap: 6px; }
.src-kv, .src-mcards { display: flex; flex-direction: column; }
.src-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; font-size: 13px; color: var(--text2); gap: 12px; }
.src-row + .src-row { border-top: 1px dashed var(--border-muted); }
.src-row span { color: var(--text3); flex-shrink: 0; }
.src-row b { color: var(--text1); font-weight: 600; text-align: right; }
.src-risk { display: flex; flex-direction: column; gap: 8px; }

.narrative-section { white-space: normal; }
.narrative-text { white-space: pre-wrap; line-height: 1.7; font-size: 13px; color: var(--text2); margin: 0; }
.narrative-fallback { font-size: 13px; color: var(--text3); margin: 0; }

.disclaimer {
  margin-top: 4px;
  padding: 10px 14px;
  border-left: 3px solid var(--down-fg, #e33);
  background: rgba(227, 51, 51, 0.06);
  color: var(--text2);
  font-size: 12px;
  border-radius: 4px;
}

@media (max-width: 768px) {
  .src-section { padding: 10px 12px; }
}
</style>
