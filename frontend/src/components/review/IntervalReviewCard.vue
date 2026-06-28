<script setup lang="ts">
import { ref, h, onMounted } from 'vue'
import {
  NCard, NButton, NButtonGroup, NDatePicker, NCheckboxGroup, NCheckbox,
  NSpace, NDataTable, NSkeleton, NText, NTooltip,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  fetchReviewSignals, type ReviewSignalRow, type ReviewSummaryRow,
} from '../../api/signals'
import { exportReviewXlsx } from '../../utils/exportXlsx'
import { useGlobalMessage } from '../../composables/useGlobalMessage'

const message = useGlobalMessage()
const loading = ref(false)
const rows = ref<ReviewSignalRow[]>([])
const summary = ref<ReviewSummaryRow[]>([])
const latestKline = ref<string | null>(null)
const range = ref<[number, number] | null>(presetRange(5))
const categories = ref<string[]>(['buy', 'sell', 'reduce'])

const catOptions = [
  { label: '买点', value: 'buy' }, { label: '卖点', value: 'sell' },
  { label: '减仓', value: 'reduce' }, { label: '板块预警', value: 'sector' },
  { label: '大盘风控', value: 'plunge' },
]

// 近 n 个交易日近似: 回推 n*1.5 自然日(覆盖周末), 实际以库内交易日数据为准
function presetRange(n: number): [number, number] {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - Math.ceil(n * 1.5))
  return [start.getTime(), end.getTime()]
}
function fmtDate(ts: number): string {
  const d = new Date(ts)
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const day = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}
function setPreset(n: number) {
  range.value = presetRange(n)
  load()
}

const pctCell = (v: number | null) => {
  if (v == null) return h('span', { style: { color: 'var(--text3,#999)' } }, '—')
  const color = v >= 0 ? 'var(--red,#dc2626)' : 'var(--green,#16a34a)' // A股 正红负绿
  return h('span', { style: { color, fontWeight: 600, fontFamily: 'monospace', fontVariantNumeric: 'tabular-nums' } },
    `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`)
}
const outcomeCell = (o: string | null) => {
  const cfg: Record<string, [string, string]> = {
    success: ['成功', 'var(--red,#dc2626)'], fail: ['失败', 'var(--green,#16a34a)'],
    neutral: ['中性', '#888'],
  }
  const [label, color] = cfg[o ?? ''] ?? ['待评估', '#d97706']
  return h('span', { style: { color, fontWeight: 700, fontSize: '12px' } }, label)
}
// 计划出场单元格: label + 目标价 + 触及徽标(已触及橙色高亮)
const exitCell = (label: string, price: number | null, hit: boolean) => {
  if (!label) return h('span', { style: { color: 'var(--text3,#999)' } }, '—')
  const parts = [h('span', {}, `${label} @${price ?? '—'}`)]
  if (hit) parts.push(h('span', { style: { color: '#b45309', fontWeight: 700, marginLeft: '4px' } }, '已触及'))
  return h('span', { style: { fontSize: '12px' } }, parts)
}

const columns: DataTableColumns<ReviewSignalRow> = [
  { title: '代码', key: 'code', width: 72 },
  { title: '名称', key: 'name', width: 80 },
  { title: '信号类型', key: 'signal_name', width: 130 },
  { title: '方向', key: 'direction', width: 72 },
  { title: '触发日', key: 'trigger_date', width: 96 },
  { title: '触发价', key: 'trigger_price', width: 80,
    render: r => h('span', {}, r.trigger_price?.toFixed(2) ?? '—') },
  { title: '现价', key: 'cur_price', width: 80,
    render: r => h('span', {}, r.cur_price?.toFixed(2) ?? (r.frozen ? '冻结' : '—')) },
  { title: '当前收益', key: 'cur_ret_pct', width: 92, render: r => pctCell(r.cur_ret_pct) },
  { title: '区间最大浮盈', key: 'max_gain_pct', width: 104, render: r => pctCell(r.max_gain_pct) },
  { title: '区间最大浮亏', key: 'max_dd_pct', width: 104, render: r => pctCell(r.max_dd_pct) },
  { title: 'T+1', key: 't1_pct', width: 78, render: r => pctCell(r.t1_pct) },
  { title: 'T+3', key: 't3_pct', width: 78, render: r => pctCell(r.t3_pct) },
  { title: 'T+5', key: 't5_pct', width: 78, render: r => pctCell(r.t5_pct) },
  { title: '评估', key: 'outcome', width: 72, render: r => outcomeCell(r.outcome) },
  { title: '计划止盈', key: 'tp_label', width: 150, render: r => exitCell(r.tp_label, r.tp_price, r.tp_hit) },
  { title: '计划止损', key: 'sl_label', width: 130, render: r => exitCell(r.sl_label, r.sl_price, r.sl_hit) },
  { title: '时停/其他出场', key: 'other_exit', width: 170, ellipsis: { tooltip: true },
    render: r => r.other_exit || '—' },
  { title: '形态详情', key: 'detail', width: 240,
    render: r => h(NTooltip, null, {
      trigger: () => h('span', { style: { cursor: 'help' } },
        (r.detail ?? '').slice(0, 24) + ((r.detail?.length ?? 0) > 24 ? '…' : '')),
      default: () => r.detail,
    }) },
]

const summaryColumns: DataTableColumns<ReviewSummaryRow> = [
  { title: '信号类型', key: 'signal_name', width: 140,
    render: g => g.signal_id === '__ALL__' ? h('b', {}, '全部') : g.signal_name },
  { title: '笔数', key: 'count', width: 64 },
  { title: '胜率', key: 'win_rate', width: 80, render: g => pctCell(g.win_rate) },
  { title: '均当前收益', key: 'avg_cur_ret', width: 100, render: g => pctCell(g.avg_cur_ret) },
  { title: '中位', key: 'median_cur_ret', width: 90, render: g => pctCell(g.median_cur_ret) },
  { title: '均最大浮盈', key: 'avg_max_gain', width: 100, render: g => pctCell(g.avg_max_gain) },
  { title: '均最大浮亏', key: 'avg_max_dd', width: 100, render: g => pctCell(g.avg_max_dd) },
  { title: 'T+5均', key: 'avg_t5', width: 84, render: g => pctCell(g.avg_t5) },
  { title: 'success率', key: 'success_rate', width: 92, render: g => pctCell(g.success_rate) },
]

async function load() {
  if (!range.value) { message.warning('请选择区间'); return }
  if (!categories.value.length) { message.warning('至少勾选一个类别'); return }
  loading.value = true
  try {
    const resp = await fetchReviewSignals(
      fmtDate(range.value[0]), fmtDate(range.value[1]), categories.value)
    rows.value = resp.rows
    summary.value = resp.summary
    latestKline.value = resp.latest_kline_date
  } catch (e) {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

async function onExport() {
  if (!rows.value.length) { message.warning('无数据可导出'); return }
  await exportReviewXlsx(rows.value, summary.value,
    fmtDate(range.value![0]), fmtDate(range.value![1]))
}

onMounted(load)
</script>

<template>
  <NCard title="区间复盘清单" size="small" :bordered="false" style="margin-bottom:16px">
    <NSpace vertical :size="10">
      <NSpace align="center" :size="8" wrap>
        <NButtonGroup size="small">
          <NButton @click="setPreset(5)">近5日</NButton>
          <NButton @click="setPreset(10)">近2周</NButton>
          <NButton @click="setPreset(22)">近1月</NButton>
          <NButton @click="setPreset(66)">近3月</NButton>
        </NButtonGroup>
        <NDatePicker v-model:value="range" type="daterange" size="small" clearable to="body" />
        <NButton size="small" type="primary" @click="load">查询</NButton>
        <NButton size="small" @click="onExport">导出xlsx</NButton>
      </NSpace>
      <NCheckboxGroup v-model:value="categories">
        <NSpace :size="12">
          <NCheckbox v-for="o in catOptions" :key="o.value" :value="o.value" :label="o.label" />
        </NSpace>
      </NCheckboxGroup>
      <NText depth="3" style="font-size:12px">
        当前收益基准 = 行情库最新收盘 {{ latestKline ?? '—' }}; "冻结"= 个股已移出池, 用历史快照兜底。
      </NText>

      <NSkeleton v-if="loading" :repeat="4" text />
      <template v-else>
        <NDataTable :columns="columns" :data="rows" size="small" :bordered="false"
          :scroll-x="2220" :max-height="460" :row-key="(r:ReviewSignalRow)=>r.code+'|'+r.signal_id+'|'+r.trigger_date" />
        <NText strong style="margin-top:8px;display:block">按信号类型汇总</NText>
        <NDataTable :columns="summaryColumns" :data="summary" size="small" :bordered="false" />
      </template>
    </NSpace>
  </NCard>
</template>
