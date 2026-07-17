<script setup lang="ts">
// 按(买点模型 × 周)真实成功率矩阵 + 近2周最适合的模型 — 判断当前行情该重点盯哪个模型。
// 数据来自真实触发的买点信号 + 回填 1/3/5 日 outcome(自选样本, 量小, 故标注每格样本数)。
import { ref, onMounted, computed } from 'vue'
import { fetchModelWeekly, type ModelWeekly } from '../../api/signals'

const data = ref<ModelWeekly>({ weeks: [], models: [] })
const loading = ref(false)
const weeks = ref(8)

async function load() {
  loading.value = true
  try { data.value = await fetchModelWeekly(weeks.value) } catch { /* silent */ } finally { loading.value = false }
}
onMounted(load)

// 近2周有≥3个已评估样本里成功率最高的 = 当前最适合
const best = computed(() => data.value.models.find(m => m.recent_rate !== null && m.recent_eval >= 3) || null)
const hasData = computed(() => data.value.models.some(m => m.cells.some(c => c.evaluated > 0)))

function mmdd(w: string) { return w ? w.slice(5) : '' }
function cellStyle(rate: number | null, ev: number) {
  if (rate === null || ev === 0) return { background: 'var(--bg-sunken)', color: 'var(--fg-subtle)' }
  const faint = ev < 3                              // 样本<3淡显(噪音大)
  // 交通灯热度: 红=高胜率好用(up) / 黄=中(warn) / 绿=失效(down); faint 用低透明档保留噪音提示
  if (rate >= 60) return { background: `color-mix(in srgb, var(--up-fg) ${faint ? 8 : 22}%, transparent)`, color: 'var(--up-fg)' }
  if (rate >= 45) return { background: `color-mix(in srgb, var(--warn-fg) ${faint ? 8 : 22}%, transparent)`, color: 'var(--warn-fg)' }
  return { background: `color-mix(in srgb, var(--down-fg) ${faint ? 8 : 22}%, transparent)`, color: 'var(--down-fg)' }
}
</script>

<template>
  <div class="mw-card">
    <div class="mw-head">
      <span class="mw-title">📊 各买点模型 · 按周真实成功率</span>
      <select v-model.number="weeks" class="mw-sel" @change="load">
        <option :value="6">近6周</option>
        <option :value="8">近8周</option>
        <option :value="12">近12周</option>
      </select>
    </div>

    <div v-if="best" class="mw-best">
      当前行情最适合 <b>{{ best.signal_name }}</b>
      <span>近2周成功率 {{ best.recent_rate }}%（{{ best.recent_success }}/{{ best.recent_eval }}）→ 重点盯它</span>
    </div>
    <div v-else-if="hasData" class="mw-best mw-best-weak">近2周样本不足(各模型已评估<3)，暂难定论，看下方逐周趋势参考</div>

    <div v-if="hasData" class="mw-table-wrap">
      <table class="mw-table">
        <thead>
          <tr>
            <th class="mw-mname">模型</th>
            <th v-for="w in data.weeks" :key="w">{{ mmdd(w) }}</th>
            <th class="mw-recent">近2周</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in data.models" :key="m.signal_id">
            <td class="mw-mname">{{ m.signal_name }}</td>
            <td v-for="(c, i) in m.cells" :key="i" :style="cellStyle(c.rate, c.evaluated)">
              <template v-if="c.evaluated > 0">
                <span class="mw-rate">{{ c.rate }}%</span><span class="mw-n">{{ c.success }}/{{ c.evaluated }}</span>
              </template>
              <span v-else class="mw-empty">·</span>
            </td>
            <td class="mw-recent" :style="cellStyle(m.recent_rate, m.recent_eval)">
              <template v-if="m.recent_eval > 0">
                <span class="mw-rate">{{ m.recent_rate }}%</span><span class="mw-n">{{ m.recent_success }}/{{ m.recent_eval }}</span>
              </template>
              <span v-else class="mw-empty">·</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="mw-foot">红=高胜率好用 · 绿=失效 · 浅色=样本&lt;3噪音大 · 每格「成功/已评估」笔数 · 成功=买点后5日收盘≥+5%</div>
    </div>
    <div v-else class="mw-loading">{{ loading ? '加载中…' : '暂无已评估样本(买点触发后需 ≥5 交易日回填结果, 攒几天就有了)' }}</div>
  </div>
</template>

<style scoped>
.mw-card { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 8px; padding: 12px 14px; }
.mw-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.mw-title { font-weight: 700; font-size: 14px; color: var(--fg-default); }
.mw-sel { font-size: 12px; padding: 2px 6px; border: 1px solid var(--border-default); border-radius: 4px; color: var(--fg-muted); touch-action: manipulation; }
.mw-best { background: var(--warn-bg-muted); border: 1px solid color-mix(in srgb, var(--warn-fg) 32%, transparent); border-radius: 6px; padding: 6px 10px; font-size: 13px; color: var(--warn-fg); margin-bottom: 10px; }
.mw-best b { color: var(--up-fg); }
.mw-best span { color: var(--warn-fg); margin-left: 6px; }
.mw-best-weak { background: var(--bg-sunken); border-color: var(--border-default); color: var(--fg-muted); }
.mw-table-wrap { overflow-x: auto; overscroll-behavior-x: contain; }
.mw-table { border-collapse: collapse; width: 100%; font-size: 12px; font-variant-numeric: tabular-nums; }
.mw-table th { background: var(--bg-sunken); color: var(--fg-muted); font-weight: 600; padding: 4px 6px; text-align: center; white-space: nowrap; border-bottom: 1px solid var(--border-default); }
.mw-table td { padding: 4px 6px; text-align: center; white-space: nowrap; border-bottom: 1px solid var(--border-muted); }
.mw-mname { text-align: left !important; color: var(--fg-default); font-weight: 600; position: sticky; left: 0; background: var(--bg-surface); }
.mw-recent { font-weight: 700; }
.mw-rate { display: block; line-height: 1.1; }
.mw-n { display: block; font-size: 10px; opacity: 0.7; }
.mw-empty { color: var(--fg-subtle); }
.mw-foot { margin-top: 8px; font-size: 11px; color: var(--fg-subtle); }
.mw-loading { padding: 18px; text-align: center; color: var(--fg-subtle); font-size: 13px; }
</style>
