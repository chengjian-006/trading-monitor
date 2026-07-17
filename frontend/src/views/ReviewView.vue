<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { NCard, NSkeleton, NSelect } from 'naive-ui'
import {
  fetchOutcomeCompare, fetchWeeklyTrend,
  type OutcomeCompare, type OutcomeSide, type WeeklyTrendWeek,
} from '../api/signals'
import IntervalReviewCard from '../components/review/IntervalReviewCard.vue'
import { useResponsive } from '../composables/useResponsive'

const { isPhone } = useResponsive()
const loading = ref(true)
const days = ref(90)
const weeks = ref(12)
const compare = ref<OutcomeCompare | null>(null)
const trend = ref<WeeklyTrendWeek[]>([])

const daysOptions = [
  { label: '近30天', value: 30 },
  { label: '近90天', value: 90 },
  { label: '近180天', value: 180 },
]

function rateColor(rate: number | null): string {
  if (rate == null) return 'var(--text2)'
  if (rate >= 55) return 'var(--up-fg)'
  if (rate >= 45) return 'var(--warn-fg)'
  return 'var(--down-fg)'
}
function fmtPct(v: number | null): string {
  return v == null ? '-' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

const sides = computed<{ key: 'buy' | 'sell'; label: string; emoji: string; data: OutcomeSide | undefined }[]>(() => [
  { key: 'buy', label: '买点', emoji: '🟢', data: compare.value?.buy },
  { key: 'sell', label: '卖点 / 减仓', emoji: '🔴', data: compare.value?.sell },
])

async function loadCompare() {
  compare.value = await fetchOutcomeCompare(days.value)
}
async function loadTrend() {
  trend.value = await fetchWeeklyTrend(weeks.value)
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadCompare(), loadTrend()])
  } finally {
    loading.value = false
  }
}

function onDaysChange(v: number) {
  days.value = v
  loadCompare()
}

onMounted(loadAll)
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">复盘 · 买卖点胜率</span>
      <NSelect :value="days" :options="daysOptions" size="small" style="width: 120px" @update:value="onDaysChange" />
    </div>

    <IntervalReviewCard />

    <NSkeleton v-if="loading" :repeat="3" text style="margin-top: 12px" />

    <template v-else>
      <!-- 买点 vs 卖点 并排对比 -->
      <div class="compare-grid">
        <NCard v-for="s in sides" :key="s.key" size="small" class="cmp-card">
          <div class="cmp-head">{{ s.emoji }} {{ s.label }}</div>
          <template v-if="s.data && s.data.evaluated > 0">
            <div class="cmp-rate" :style="{ color: rateColor(s.data.success_rate) }">
              {{ s.data.success_rate }}<span class="pct">%</span>
            </div>
            <div class="cmp-sub">成功 {{ s.data.success }} / 已评估 {{ s.data.evaluated }}（待评估 {{ s.data.pending }}）</div>
            <div class="cmp-bar">
              <span class="seg s" :style="{ width: (s.data.success / s.data.evaluated * 100) + '%' }" />
              <span class="seg n" :style="{ width: (s.data.neutral / s.data.evaluated * 100) + '%' }" />
              <span class="seg f" :style="{ width: (s.data.fail / s.data.evaluated * 100) + '%' }" />
            </div>
            <div class="cmp-legend">
              <span><i class="dot s" />成功 {{ s.data.success }}</span>
              <span><i class="dot n" />中性 {{ s.data.neutral }}</span>
              <span><i class="dot f" />失败 {{ s.data.fail }}</span>
            </div>
            <div class="cmp-avg">
              平均收益　1日 {{ fmtPct(s.data.avg_p1) }}　3日 {{ fmtPct(s.data.avg_p3) }}　5日 {{ fmtPct(s.data.avg_p5) }}
            </div>
          </template>
          <div v-else class="cmp-empty">暂无已评估样本（触发满5个交易日才计入）</div>
        </NCard>
      </div>

      <!-- 成功率按周趋势 -->
      <NCard title="成功率按周趋势" size="small" style="margin-top: 16px">
        <template #header-extra><span class="sub">近 {{ trend.length }} 周（实际收盘口径）</span></template>
        <div v-if="!trend.length" class="cmp-empty">暂无数据</div>
        <div v-else class="trend-scroll" :class="{ phone: isPhone }">
        <table class="trend-table">
          <thead>
            <tr><th>周（周一起）</th><th>买点</th><th>卖点</th></tr>
          </thead>
          <tbody>
            <tr v-for="w in trend" :key="w.week_start">
              <td class="wk">{{ w.week_start.slice(5) }}</td>
              <td>
                <div class="tr-cell">
                  <div class="tr-bar"><span class="tr-fill buy" :style="{ width: (w.buy.rate ?? 0) + '%' }" /></div>
                  <span class="tr-num" :style="{ color: rateColor(w.buy.rate) }">
                    {{ w.buy.rate == null ? '-' : w.buy.rate + '%' }}
                  </span>
                  <span class="tr-n">{{ w.buy.evaluated ? `${w.buy.success}/${w.buy.evaluated}` : '' }}</span>
                </div>
              </td>
              <td>
                <div class="tr-cell">
                  <div class="tr-bar"><span class="tr-fill sell" :style="{ width: (w.sell.rate ?? 0) + '%' }" /></div>
                  <span class="tr-num" :style="{ color: rateColor(w.sell.rate) }">
                    {{ w.sell.rate == null ? '-' : w.sell.rate + '%' }}
                  </span>
                  <span class="tr-n">{{ w.sell.evaluated ? `${w.sell.success}/${w.sell.evaluated}` : '' }}</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
        <div class="note">口径：触发后第5个交易日收盘相对触发价，买点≥+5%、卖点翻转后≥+5% 记成功；成功率=成功÷已评估。未满5个交易日的不计入。</div>
      </NCard>
    </template>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.page-title { font-size: 15px; font-weight: 600; color: var(--text1); }
.sub { font-size: 11px; color: var(--text2); }

.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 768px) { .compare-grid { grid-template-columns: 1fr; } }
.cmp-head { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.cmp-rate { font-size: 38px; font-weight: 700; font-family: monospace; line-height: 1.1; font-variant-numeric: tabular-nums; }
.cmp-rate .pct { font-size: 18px; margin-left: 2px; }
.cmp-sub { font-size: 12px; color: var(--text2); margin: 4px 0 8px; }
.cmp-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--border-muted); }
.cmp-bar .seg { height: 100%; }
.cmp-bar .seg.s { background: var(--up-fg); }
.cmp-bar .seg.n { background: var(--flat-fg); }
.cmp-bar .seg.f { background: var(--down-fg); }
.cmp-legend { display: flex; gap: 14px; font-size: 12px; color: var(--text2); margin-top: 6px; }
.cmp-legend .dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }
.cmp-legend .dot.s { background: var(--up-fg); }
.cmp-legend .dot.n { background: var(--flat-fg); }
.cmp-legend .dot.f { background: var(--down-fg); }
.cmp-avg { font-size: 12px; color: var(--text2); margin-top: 10px; font-family: monospace; font-variant-numeric: tabular-nums; }
.cmp-empty { color: var(--text2); font-size: 12px; padding: 18px 0; text-align: center; }

/* 趋势表横滚容器: 窄屏不挤压三列, 保留横向滚动 */
.trend-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.trend-scroll.phone .trend-table { min-width: 420px; }
.trend-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.trend-table th { text-align: left; color: var(--text2); font-weight: 500; padding: 4px 6px; border-bottom: 1px solid var(--border-default); }
.trend-table td { padding: 5px 6px; border-bottom: 1px dashed var(--border-default); }
.trend-table .wk { font-family: monospace; color: var(--text2); white-space: nowrap; font-variant-numeric: tabular-nums; }
.tr-cell { display: flex; align-items: center; gap: 8px; }
.tr-bar { flex: 1; height: 7px; background: var(--border-muted); border-radius: 4px; overflow: hidden; min-width: 60px; }
.tr-fill { display: block; height: 100%; }
.tr-fill.buy { background: var(--up-fg); }
.tr-fill.sell { background: var(--down-fg); }
.tr-num { font-family: monospace; font-weight: 600; width: 44px; text-align: right; font-variant-numeric: tabular-nums; }
.tr-n { font-family: monospace; color: var(--text2); width: 48px; font-variant-numeric: tabular-nums; }
.note { font-size: 11px; color: var(--text2); margin-top: 10px; line-height: 1.5; }
</style>
