<script setup lang="ts">
import { ref, computed, h, onMounted } from 'vue'
import { NDatePicker, NButton, NSpin, NEmpty, NDataTable, type DataTableColumns } from 'naive-ui'
import { getCoachReport, type CoachReport } from '../api/coach'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { useResponsive } from '../composables/useResponsive'

const message = useGlobalMessage()
const { isMobile } = useResponsive()

const RED = 'var(--up-fg)'
const GREEN = 'var(--down-fg)'

// ── 区间选择: 默认近一月 ──
function tsToDay(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const now = Date.now()
const range = ref<[number, number]>([now - 30 * 86400000, now])

const loading = ref(false)
const report = ref<CoachReport | null>(null)

async function generate() {
  if (!range.value || range.value.length !== 2) {
    message.warning('请先选择区间')
    return
  }
  const start = tsToDay(range.value[0])
  const end = tsToDay(range.value[1])
  loading.value = true
  try {
    report.value = await getCoachReport(start, end)
  } catch {
    message.error('复盘生成失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

onMounted(generate)

// ── 展示辅助: null/undefined 一律显示为 "—", 不出现 NaN/null 字样 ──
function fmt(v: number | null | undefined, suffix = ''): string {
  return v === null || v === undefined || Number.isNaN(v) ? '—' : `${v}${suffix}`
}
function fmtDays(v: number | null | undefined): string {
  return v === null || v === undefined ? '样本不足' : `${v} 天`
}
function pctColor(v: number | null | undefined): string {
  return v === null || v === undefined ? 'var(--text3)' : v >= 0 ? RED : GREEN
}
function renderPct(v: number | null | undefined) {
  return h('span', { style: { color: pctColor(v) } }, fmt(v, v === null || v === undefined ? '' : '%'))
}

const facts = computed(() => report.value?.facts || null)
const narrative = computed(() => report.value?.narrative || null)

// ── 1) 听模型 vs 自作主张 ──
interface ListenRow { label: string; n: number; win_rate: number; avg_pnl_pct: number }
const listenRows = computed<ListenRow[]>(() => {
  const lvs = facts.value?.listen_vs_self
  if (!lvs) return []
  return [
    { label: '听模型', ...lvs.listen },
    { label: '自作主张', ...lvs.self },
  ]
})
const listenColumns: DataTableColumns<ListenRow> = [
  { title: '类型', key: 'label', width: 100 },
  { title: '笔数', key: 'n', width: 80, align: 'center' },
  { title: '胜率', key: 'win_rate', width: 90, align: 'right', render: (r) => `${r.win_rate}%` },
  { title: '均收益率', key: 'avg_pnl_pct', width: 110, align: 'right', render: (r) => renderPct(r.avg_pnl_pct) },
]

// ── 2) 按模型成绩 ──
interface ModelRow {
  model_name: string
  n: number
  win_rate: number
  avg_pnl_pct: number | null
  market_win_rate_3m: number | null
  exec_gap: number | null
}
const modelRows = computed<ModelRow[]>(() => facts.value?.by_model || [])
const modelColumns: DataTableColumns<ModelRow> = [
  { title: '模型', key: 'model_name', width: 150 },
  { title: '笔数', key: 'n', width: 70, align: 'center' },
  { title: '实盘胜率', key: 'win_rate', width: 90, align: 'right', render: (r) => `${r.win_rate}%` },
  { title: '均收益率', key: 'avg_pnl_pct', width: 100, align: 'right', render: (r) => renderPct(r.avg_pnl_pct) },
  { title: '全市场胜率(近3月)', key: 'market_win_rate_3m', width: 140, align: 'right', render: (r) => fmt(r.market_win_rate_3m, r.market_win_rate_3m === null ? '' : '%') },
  { title: '执行差', key: 'exec_gap', width: 90, align: 'right', render: (r) => renderPct(r.exec_gap) },
]

// ── 3) 盈亏/持仓周期 ──
interface KVRow { label: string; value: string }
const cycleRows = computed<KVRow[]>(() => {
  const c = facts.value?.cycle
  if (!c) return []
  return [
    { label: '平均持仓天数', value: fmtDays(c.hold_days_avg) },
    { label: '赢家平均持仓', value: fmtDays(c.winner_hold_avg) },
    { label: '输家平均持仓', value: fmtDays(c.loser_hold_avg) },
    { label: '最佳单笔收益率', value: fmt(c.pnl_dist?.best_pct, '%') },
    { label: '最差单笔收益率', value: fmt(c.pnl_dist?.worst_pct, '%') },
    { label: '平均收益率', value: fmt(c.pnl_dist?.avg_pct, '%') },
  ]
})

// ── 4) 买卖习惯 ──
const holdsLongerText = computed(() => {
  const v = facts.value?.habits?.loser_holds_longer
  if (v === null || v === undefined) return '样本不足'
  return v ? '输家扛得更久（有扛单倾向）' : '赢家扛得更久'
})
const habitRows = computed<KVRow[]>(() => {
  const h = facts.value?.habits
  if (!h) return []
  return [
    { label: '赢家/输家持仓对比', value: holdsLongerText.value },
    { label: '分批止盈占比', value: fmt(h.scaled_out_ratio, '%') },
    { label: '止损离场笔数', value: `${h.stop_discipline.stop_exit_rounds} 笔` },
    { label: '止损离场占比', value: fmt(h.stop_discipline.stop_exit_ratio, '%') },
  ]
})

const kvColumns: DataTableColumns<KVRow> = [
  { title: '指标', key: 'label', width: 160 },
  { title: '数值', key: 'value', align: 'right' },
]

const hasData = computed(() => !!facts.value && facts.value.n_closed > 0)
</script>

<template>
  <div class="trade-coach">
    <div class="page-header">
      <h2>交易复盘</h2>
      <p class="sub">按你真实交割单算出的成绩规律 + AI 大白话复盘，每周日自动推一份，也可按需生成</p>
      <div class="toolbar">
        <NDatePicker v-model:value="range" type="daterange" size="small" clearable to="body"
                     :class="{ 'range-picker-mobile': isMobile }" />
        <NButton type="primary" size="small" :loading="loading" @click="generate">生成复盘</NButton>
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="!facts">
        <NEmpty description="暂无数据，点击「生成复盘」试试" style="margin: 48px 0;" />
      </template>

      <template v-else-if="!hasData">
        <NEmpty description="所选区间内没有已平仓的交易记录" style="margin: 48px 0;" />
      </template>

      <template v-else>
        <div class="section">
          <div class="section-title">听模型 vs 自作主张</div>
          <NDataTable v-if="!isMobile" :columns="listenColumns" :data="listenRows" :bordered="false" size="small" />
          <div v-else class="mobile-cards">
            <div v-for="row in listenRows" :key="row.label" class="mobile-card">
              <div class="mc-title">{{ row.label }}</div>
              <div class="mc-row"><span>笔数</span><b>{{ row.n }}</b></div>
              <div class="mc-row"><span>胜率</span><b>{{ row.win_rate }}%</b></div>
              <div class="mc-row"><span>均收益率</span><b :style="{ color: pctColor(row.avg_pnl_pct) }">{{ fmt(row.avg_pnl_pct, '%') }}</b></div>
            </div>
          </div>
        </div>

        <div class="section">
          <div class="section-title">按买点模型归因成绩</div>
          <template v-if="modelRows.length">
            <NDataTable v-if="!isMobile" :columns="modelColumns" :data="modelRows" :bordered="false" size="small" :row-key="(r: ModelRow) => r.model_name" />
            <div v-else class="mobile-cards">
              <div v-for="row in modelRows" :key="row.model_name" class="mobile-card">
                <div class="mc-title">{{ row.model_name }}</div>
                <div class="mc-row"><span>笔数</span><b>{{ row.n }}</b></div>
                <div class="mc-row"><span>实盘胜率</span><b>{{ row.win_rate }}%</b></div>
                <div class="mc-row"><span>均收益率</span><b :style="{ color: pctColor(row.avg_pnl_pct) }">{{ fmt(row.avg_pnl_pct, '%') }}</b></div>
                <div class="mc-row"><span>全市场胜率(近3月)</span><b>{{ fmt(row.market_win_rate_3m, row.market_win_rate_3m === null ? '' : '%') }}</b></div>
                <div class="mc-row"><span>执行差</span><b :style="{ color: pctColor(row.exec_gap) }">{{ fmt(row.exec_gap, '%') }}</b></div>
              </div>
            </div>
          </template>
          <NEmpty v-else description="区间内没有听模型买入的记录" size="small" style="margin: 16px 0;" />
        </div>

        <div class="section">
          <div class="section-title">盈亏 / 持仓周期</div>
          <NDataTable v-if="!isMobile" :columns="kvColumns" :data="cycleRows" :bordered="false" size="small" :show-header="false" />
          <div v-else class="mobile-cards">
            <div class="mobile-card kv-card">
              <div v-for="row in cycleRows" :key="row.label" class="mc-row"><span>{{ row.label }}</span><b>{{ row.value }}</b></div>
            </div>
          </div>
        </div>

        <div class="section">
          <div class="section-title">买卖习惯</div>
          <NDataTable v-if="!isMobile" :columns="kvColumns" :data="habitRows" :bordered="false" size="small" :show-header="false" />
          <div v-else class="mobile-cards">
            <div class="mobile-card kv-card">
              <div v-for="row in habitRows" :key="row.label" class="mc-row"><span>{{ row.label }}</span><b>{{ row.value }}</b></div>
            </div>
          </div>
        </div>

        <div class="section narrative-section">
          <div class="section-title">AI 复盘小结</div>
          <p v-if="narrative" class="narrative-text">{{ narrative }}</p>
          <p v-else class="narrative-fallback">AI 叙述暂不可用（数据仍完整）</p>
        </div>
      </template>
    </NSpin>

    <div class="disclaimer">客观历史数据 + AI 归纳，非投资建议、不预测涨跌</div>
  </div>
</template>

<style scoped>
.trade-coach { padding: 4px 0 24px; }

.page-header { margin-bottom: 16px; }
.page-header h2 { font-size: 18px; font-weight: 700; color: var(--text1); margin: 0 0 4px; }
.page-header .sub { font-size: 12px; color: var(--text3); margin: 0 0 10px; }
.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
@media (max-width: 768px) {
  .toolbar { width: 100%; }
  .range-picker-mobile { flex: 1 1 auto; width: 100%; }
}

.section { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 14px; margin-bottom: 12px; }
.section-title { font-size: 14px; font-weight: 700; color: var(--text1); margin-bottom: 10px; }

.mobile-cards { display: flex; flex-direction: column; gap: 8px; }
.mobile-card { border: 1px solid var(--border-muted); border-radius: 6px; padding: 10px 12px; background: var(--bg-elevated, transparent); }
.mc-title { font-weight: 700; color: var(--text1); margin-bottom: 6px; font-size: 13px; }
.mc-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; font-size: 13px; color: var(--text2); min-height: 28px; }
.mc-row + .mc-row { border-top: 1px dashed var(--border-muted); }
.mc-row span { color: var(--text3); }
.mc-row b { color: var(--text1); font-weight: 600; }
.kv-card .mc-row:first-child { border-top: none; }

.narrative-section { white-space: normal; }
.narrative-text { white-space: pre-wrap; line-height: 1.7; font-size: 13px; color: var(--text2); margin: 0; }
.narrative-fallback { font-size: 13px; color: var(--text3); margin: 0; }

.disclaimer {
  margin-top: 16px;
  padding: 10px 14px;
  border-left: 3px solid var(--down-fg, #e33);
  background: rgba(227, 51, 51, 0.06);
  color: var(--text2);
  font-size: 12px;
  border-radius: 4px;
}
</style>
