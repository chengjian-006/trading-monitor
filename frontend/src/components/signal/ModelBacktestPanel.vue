<script setup lang="ts">
// 各买入模型 全市场按周回测(路线B): 每周六服务器拉全A跑近半年5模型, 含卖半资金加权占用 + 资金效率。
// 比"真实自选信号"样本厚得多, 用来判断当前行情适合哪个模型。竞价高开弱转强无历史竞价数据不参与。
import { ref, onMounted, computed } from 'vue'
import { fetchModelBacktest, type ModelBacktest } from '../../api/signals'

const data = ref<ModelBacktest>({ run_date: null, window_start: null, models: [] })
const loading = ref(false)

async function load() {
  loading.value = true
  try { data.value = await fetchModelBacktest() } catch { /* silent */ } finally { loading.value = false }
}
onMounted(load)

// 综合最优 = 年化资金效率最高且样本≥50(已按年化降序返回)
const best = computed(() => data.value.models.find(m => m.n >= 50) || data.value.models[0] || null)
function pfColor(pf: number) {
  if (pf >= 2) return '#b91c1c'
  if (pf >= 1.5) return '#a16207'
  if (pf >= 1) return '#475569'
  return '#15803d'
}
</script>

<template>
  <div class="mb-card">
    <div class="mb-head">
      <span class="mb-title">🧪 各买入模型 · 全市场半年回测(含资金成本)</span>
      <span class="mb-date" v-if="data.run_date">{{ data.run_date }} 跑 · 窗口起 {{ data.window_start }}</span>
    </div>

    <div v-if="best" class="mb-best">
      综合最优 <b>{{ best.model_name }}</b>
      <span>年化资金效率 +{{ best.annualized }}% · 胜率 {{ best.win_rate }}% · 盈利因子 {{ best.pf }}（{{ best.n }}笔）</span>
    </div>

    <div v-if="data.models.length" class="mb-table-wrap">
      <table class="mb-table">
        <thead>
          <tr>
            <th class="mb-mname">模型</th>
            <th>笔数</th><th>胜率</th><th>清仓天</th><th>占用天<br>(卖半后)</th>
            <th>单笔净<br>(扣费)</th><th>扣资金<br>成本后</th><th>年化<br>资金效率</th><th>盈利<br>因子</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in data.models" :key="m.signal_id">
            <td class="mb-mname">{{ m.model_name }}</td>
            <td>{{ m.n }}</td>
            <td>{{ m.win_rate }}%</td>
            <td>{{ m.avg_span }}</td>
            <td class="mb-eff">{{ m.avg_eff }}</td>
            <td :class="m.net_mean >= 0 ? 'up' : 'down'">{{ m.net_mean >= 0 ? '+' : '' }}{{ m.net_mean }}%</td>
            <td :class="m.net_after_cost >= 0 ? 'up' : 'down'">{{ m.net_after_cost >= 0 ? '+' : '' }}{{ m.net_after_cost }}%</td>
            <td class="mb-ann">+{{ m.annualized }}%</td>
            <td :style="{ color: pfColor(m.pf), fontWeight: 700 }">{{ m.pf }}</td>
          </tr>
        </tbody>
      </table>
      <div class="mb-foot">
        每周六全市场重跑 · 各模型各自出场(右侧+7卖半/破10均/-6/T10, 弱势极限-12%止损/持有T15) ·
        卖半后只占半额资金 · 扣费0.30% · 资金成本年化6% · 年化资金效率=单笔净÷加权占用天×245(理想满仓轮动,仅看相对)
      </div>
    </div>
    <div v-else class="mb-empty">{{ loading ? '加载中…' : '暂无回测结果(首次将于本周六 08:17 自动跑;或手动触发任务)' }}</div>
  </div>
</template>

<style scoped>
.mb-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; }
.mb-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 4px; }
.mb-title { font-weight: 700; font-size: 14px; color: #1e293b; }
.mb-date { font-size: 12px; color: #94a3b8; }
.mb-best { background: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px; padding: 6px 10px; font-size: 13px; color: #9a3412; margin-bottom: 10px; }
.mb-best b { color: #b91c1c; }
.mb-best span { color: #c2410c; margin-left: 6px; }
.mb-table-wrap { overflow-x: auto; overscroll-behavior-x: contain; }
.mb-table { border-collapse: collapse; width: 100%; font-size: 12px; font-variant-numeric: tabular-nums; }
.mb-table th { background: #f8fafc; color: #64748b; font-weight: 600; padding: 4px 6px; text-align: center; white-space: nowrap; border-bottom: 1px solid #e2e8f0; line-height: 1.2; }
.mb-table td { padding: 5px 6px; text-align: center; white-space: nowrap; border-bottom: 1px solid #f1f5f9; color: #334155; }
.mb-mname { text-align: left !important; font-weight: 600; color: #1e293b; }
.mb-eff { font-weight: 600; color: #7c3aed; }
.mb-ann { font-weight: 700; color: #b91c1c; }
.up { color: #dc2626; }
.down { color: #16a34a; }
.mb-foot { margin-top: 8px; font-size: 11px; color: #94a3b8; line-height: 1.5; }
.mb-empty { padding: 18px; text-align: center; color: #94a3b8; font-size: 13px; }
</style>
