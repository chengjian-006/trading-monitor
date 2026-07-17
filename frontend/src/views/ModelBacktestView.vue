<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { NButton, NInputNumber, NSwitch, NIcon, NSkeleton, NTag, NProgress, NModal, NPopconfirm } from 'naive-ui'
import { PlayOutline, RefreshOutline, ChevronDownOutline, SyncOutline, TimeOutline, TrashOutline } from '@vicons/ionicons5'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { useResponsive } from '../composables/useResponsive'
import {
  listBacktestModels, runModelBacktest, getModelJob,
  listModelRuns, getModelRun, deleteModelRun,
  type BacktestModel, type ModelParams, type ModelRunResult, type JobProgress,
  type BacktestRunSummary, type BacktestRunDetail,
  type ModelTrade, type ModelTradeLeg,
} from '../api/backtest'
import { paramLabel } from '../data/paramLabels'

const message = useGlobalMessage()
const { isPhone } = useResponsive()

const loading = ref(true)
const models = ref<BacktestModel[]>([])

const LOOKBACK_OPTS = [
  { label: '近3月', value: 91 },
  { label: '近半年', value: 182 },
  { label: '近1年', value: 366 },
]

// 每个模型一份独立 UI 状态
interface ModelState {
  params: ModelParams            // 可编辑的临时参数
  defaults: ModelParams          // 默认值(恢复用)
  scope: 'pool' | 'all'
  koujing: 'daily' | '5m'
  lookback: number
  open: boolean                  // 参数区是否展开
  tradesOpen: boolean            // 交易明细是否展开
  running: boolean
  progress: JobProgress
  result: ModelRunResult | null
  error: string
}
const state = reactive<Record<string, ModelState>>({})
const timers: Record<string, number> = {}

// ── 历史记录 ──
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyRuns = ref<BacktestRunSummary[]>([])
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailRun = ref<BacktestRunDetail | null>(null)

async function loadHistory() {
  historyLoading.value = true
  try { historyRuns.value = await listModelRuns(100) }
  catch (e: any) { message.error('历史记录加载失败: ' + (e?.message || '')) }
  finally { historyLoading.value = false }
}
function toggleHistory() {
  historyOpen.value = !historyOpen.value
  if (historyOpen.value && !historyRuns.value.length) loadHistory()
}
async function openDetail(id: number) {
  detailOpen.value = true
  detailLoading.value = true
  detailRun.value = null
  try { detailRun.value = await getModelRun(id) }
  catch (e: any) { message.error('详情加载失败: ' + (e?.message || '')) }
  finally { detailLoading.value = false }
}
async function delRun(id: number) {
  try {
    await deleteModelRun(id)
    historyRuns.value = historyRuns.value.filter((r) => r.id !== id)
    message.success('已删除该记录')
  } catch (e: any) { message.error('删除失败: ' + (e?.message || '')) }
}
function scopeText(s: string) { return s === 'all' ? '全市场' : '自选股' }
function koujingText(k: string) { return k === '5m' ? '5分钟' : '日线' }

// 一次买入的出场分腿: 有 legs 用 legs(卖半→清剩堆叠), 旧历史记录缺 legs 时回退合成单腿
function tradeLegs(t: ModelTrade): ModelTradeLeg[] {
  if (t.legs && t.legs.length) return t.legs
  return [{ pos: 100, reason: t.exit_reason || '', date: t.exit_date || '',
            price: t.exit_price, ret_pct: t.ret_pct, hold: t.hold_days }]
}
function legDate(d: string) { return d && d.length >= 10 ? d.slice(5) : d }   // YYYY-MM-DD → MM-DD
// 持有期最高浮盈/最大浮亏: 旧记录无该字段时回退 '—'
function mfeText(t: ModelTrade) {
  return t.mfe_pct == null ? '—' : `${t.mfe_pct >= 0 ? '+' : ''}${t.mfe_pct.toFixed(1)}%` + (t.mfe_day ? ` T+${t.mfe_day}` : '')
}
function maeText(t: ModelTrade) {
  return t.mae_pct == null ? '—' : `${t.mae_pct > 0 ? '+' : ''}${t.mae_pct.toFixed(1)}%` + (t.mae_day ? ` T+${t.mae_day}` : '')
}

function numericKeys(p: ModelParams) { return Object.keys(p).filter((k) => typeof p[k] === 'number') }
function boolKeys(p: ModelParams) { return Object.keys(p).filter((k) => typeof p[k] === 'boolean') }

// 成交额类参数: 后端存/算用「元」, 界面一律按「亿元」展示与编辑(保留2位)
const AMOUNT_KEYS = new Set(['min_full_day_amount', 'min_amount_now'])
function isAmount(k: string) { return AMOUNT_KEYS.has(k) }
function getYi(p: ModelParams, k: string) { return Math.round((Number(p[k]) || 0) / 1e8 * 100) / 100 }
function setYi(p: ModelParams, k: string, v: number | null) { p[k] = Math.round((Number(v) || 0) * 1e8) }
// 参数值文案(详情用): 成交额→亿元2位, 布尔→开/关, 其余原值
function paramValueText(k: string, v: number | boolean) {
  if (typeof v === 'boolean') return v ? '开' : '关'
  if (isAmount(k)) return (Number(v) / 1e8).toFixed(2) + '亿'
  return String(v)
}

function resetParams(id: string) {
  state[id].params = { ...state[id].defaults }
}

function pfText(pf: number) { return pf >= 99 ? '无亏损' : pf.toFixed(2) }
function pct(p: JobProgress) { return p.total > 0 ? Math.round(p.done / p.total * 100) : 0 }
function winClass(win: number) { return win >= 60 ? 'good' : win >= 45 ? 'mid' : 'bad' }

async function run(id: string) {
  const s = state[id]
  if (s.running) return
  s.running = true
  s.result = null
  s.error = ''
  s.progress = { done: 0, total: 0, phase: '提交任务…', note: '' }
  try {
    const resp = await runModelBacktest({
      model_id: id, scope: s.scope, koujing: s.koujing,
      lookback_days: s.lookback, temp_config: { [id]: { ...s.params } },
    })
    if (!resp.ok) { s.error = resp.msg || '回测失败'; s.running = false; return }
    if (resp.status === 'done' && resp.result) { s.result = resp.result; s.running = false; return }
    if (resp.job_id) { s.progress = { done: 0, total: resp.total || 0, phase: '排队中', note: '' }; poll(id, resp.job_id) }
  } catch (e: any) {
    s.error = e?.message || '请求失败'
    s.running = false
  }
}

// 自调度 setTimeout(非 setInterval): 同一时刻只有一个在飞行的轮询请求, 杜绝异步回调重叠
// (重叠会出现: done 那次设好结果停表, 另一个在飞行的请求却因瞬时失败把"轮询中断"覆盖上去)。
// 重回测在同进程 asyncio 里跑, pandas 同步段会短暂阻塞事件循环, 个别状态请求可能超时 ——
// 故容忍连续 MAX_POLL_FAILS 次失败才判定中断; 结果一旦出来(running=false)即不再覆盖。
const MAX_POLL_FAILS = 4
function poll(id: string, jobId: string) {
  let fails = 0
  const tick = async () => {
    if (!state[id].running) return                      // 已结束/被中止, 不再轮询
    try {
      const j = await getModelJob(jobId)
      if (!state[id].running) return                    // 期间已被中止
      fails = 0
      state[id].progress = j.progress
      if (j.status === 'done') { state[id].result = j.result; state[id].running = false; stopPoll(id); loadHistory(); return }
      if (j.status === 'error') { state[id].error = j.error || '回测出错'; state[id].running = false; stopPoll(id); return }
    } catch {
      if (++fails >= MAX_POLL_FAILS) { state[id].error = '轮询中断'; state[id].running = false; stopPoll(id); return }
    }
    timers[id] = window.setTimeout(tick, 1500)          // 上一次彻底结束后才排下一次
  }
  timers[id] = window.setTimeout(tick, 1500)
}
function stopPoll(id: string) { if (timers[id]) { clearTimeout(timers[id]); delete timers[id] } }

onMounted(async () => {
  try {
    const list = await listBacktestModels()
    models.value = list
    for (const m of list) {
      state[m.id] = {
        params: { ...m.params }, defaults: { ...m.params },
        scope: 'pool', koujing: 'daily', lookback: 182, open: false, tradesOpen: false,
        running: false, progress: { done: 0, total: 0, phase: '', note: '' }, result: null, error: '',
      }
    }
  } catch (e: any) {
    message.error('加载模型清单失败: ' + (e?.message || ''))
  } finally {
    loading.value = false
  }
  loadHistory()
})
onUnmounted(() => { Object.keys(timers).forEach(stopPoll) })
</script>

<template>
  <div class="mb-view">
    <div class="mb-head">
      <h2>模型回测</h2>
      <p class="mb-sub">改临时参数(不影响线上)→ 选范围口径 → 点回测看战绩。<b>日线</b>=全天量口径(快);<b>5分钟</b>=真实可成交口径(慢,揭穿日线高估)。覆盖沪深A股~93.5%(北交所不含),存幸存者偏差(退市股不在内,胜率略偏高)。</p>
    </div>

    <!-- 历史记录面板(每次成功回测自动记录) -->
    <div class="mb-history-card">
      <div class="mb-hist-toggle" @click="toggleHistory">
        <NIcon :component="historyOpen ? ChevronDownOutline : TimeOutline" :class="{ rot: historyOpen }" />
        历史记录<span v-if="historyRuns.length">({{ historyRuns.length }})</span>
        <span class="mb-hist-hint">每次成功回测自动保存,最多留最近 200 条</span>
        <NButton v-if="historyOpen" size="tiny" quaternary style="margin-left:auto" @click.stop="loadHistory">
          <template #icon><NIcon :component="RefreshOutline" /></template>刷新
        </NButton>
      </div>
      <div v-show="historyOpen" class="mb-hist-body">
        <div v-if="historyLoading" style="padding:8px 0"><NSkeleton text :repeat="2" /></div>
        <div v-else-if="!historyRuns.length" class="mb-hist-empty">还没有记录,跑一次回测就会自动存下来。</div>
        <div v-else class="mb-hist-list">
          <div v-for="r in historyRuns" :key="r.id" class="mb-hrow">
            <div class="mb-hrow-main">
              <div class="mb-hrow-top">
                <span class="mb-hrow-model">{{ r.model_name }}</span>
                <span class="mb-hrow-scope">{{ scopeText(r.scope) }}·{{ koujingText(r.koujing) }}</span>
                <span class="mb-hrow-time">{{ r.created_at.slice(5, 16) }}</span>
              </div>
              <div class="mb-hrow-win">窗口 {{ r.window_start.slice(5) }} ~ {{ r.window_end.slice(5) }}</div>
            </div>
            <div class="mb-hrow-stats">
              <div class="mb-hstat"><span class="v">{{ r.overall.n }}</span><span class="l">笔数</span></div>
              <div class="mb-hstat"><span class="v" :class="winClass(r.overall.win)">{{ r.overall.win.toFixed(0) }}%</span><span class="l">胜率</span></div>
              <div class="mb-hstat"><span class="v" :class="r.overall.avg>=0?'up':'down'">{{ r.overall.avg>=0?'+':'' }}{{ r.overall.avg.toFixed(2) }}%</span><span class="l">均收</span></div>
              <div class="mb-hstat"><span class="v">{{ pfText(r.overall.pf) }}</span><span class="l">盈利因子</span></div>
            </div>
            <div class="mb-hrow-actions">
              <NButton size="tiny" secondary type="primary" @click="openDetail(r.id)">查看</NButton>
              <NPopconfirm @positive-click="delRun(r.id)" positive-text="删除" negative-text="取消">
                <template #trigger>
                  <NButton size="tiny" quaternary type="error" aria-label="删除该记录">
                    <template #icon><NIcon :component="TrashOutline" /></template>
                  </NButton>
                </template>
                确定删除这条回测记录?
              </NPopconfirm>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template v-if="loading">
      <NSkeleton text :repeat="4" style="margin-bottom:12px" />
    </template>

    <div v-else class="mb-list">
      <div v-for="m in models" :key="m.id" class="mb-card">
        <div class="mb-card-head">
          <div class="mb-title">
            <span class="mb-name">{{ m.name }}</span>
            <NTag size="small" :bordered="false" type="info">{{ m.id }}</NTag>
          </div>
          <div class="mb-controls">
            <div class="seg">
              <button :class="{ on: state[m.id].scope==='pool' }" @click="state[m.id].scope='pool'">自选股</button>
              <button :class="{ on: state[m.id].scope==='all' }" @click="state[m.id].scope='all'">全市场</button>
            </div>
            <div class="seg">
              <button :class="{ on: state[m.id].koujing==='daily' }" @click="state[m.id].koujing='daily'">日线</button>
              <button :class="{ on: state[m.id].koujing==='5m' }" @click="state[m.id].koujing='5m'">5分钟</button>
            </div>
            <div class="seg">
              <button v-for="o in LOOKBACK_OPTS" :key="o.value"
                      :class="{ on: state[m.id].lookback===o.value }" @click="state[m.id].lookback=o.value">{{ o.label }}</button>
            </div>
            <NButton size="small" type="primary" :loading="state[m.id].running" @click="run(m.id)">
              <template #icon><NIcon :component="PlayOutline" /></template>回测
            </NButton>
          </div>
        </div>

        <!-- 慢组合提示 -->
        <div v-if="state[m.id].scope==='all' || state[m.id].koujing==='5m'" class="mb-warn">
          {{ state[m.id].koujing==='5m' && state[m.id].scope==='all' ? '5分钟×全市场为长任务(可能 1-2 小时)'
             : state[m.id].scope==='all' ? '全市场约数分钟' : '5分钟·自选股约 1-3 分钟' }},后台跑带进度。
        </div>

        <!-- 参数区 -->
        <div class="mb-params-toggle" @click="state[m.id].open = !state[m.id].open">
          <NIcon :component="ChevronDownOutline" :class="{ rot: state[m.id].open }" />
          临时参数({{ numericKeys(state[m.id].params).length + boolKeys(state[m.id].params).length }})
          <NButton text size="tiny" style="margin-left:auto" @click.stop="resetParams(m.id)">
            <template #icon><NIcon :component="RefreshOutline" /></template>恢复默认
          </NButton>
        </div>
        <div v-show="state[m.id].open" class="mb-params">
          <div v-for="k in numericKeys(state[m.id].params)" :key="k" class="mb-param">
            <span class="mb-pk" :title="k">{{ paramLabel(k) }}</span>
            <NInputNumber v-if="isAmount(k)" :value="getYi(state[m.id].params, k)"
                          @update:value="(v:number|null) => setYi(state[m.id].params, k, v)"
                          size="small" :show-button="false" :precision="2" :min="0" style="width:120px" />
            <NInputNumber v-else v-model:value="(state[m.id].params[k] as number)" size="small" :show-button="false" style="width:120px" />
          </div>
          <div v-for="k in boolKeys(state[m.id].params)" :key="k" class="mb-param">
            <span class="mb-pk" :title="k">{{ paramLabel(k) }}</span>
            <NSwitch v-model:value="(state[m.id].params[k] as boolean)" size="small" />
          </div>
        </div>

        <!-- 进度 / 结果 -->
        <div v-if="state[m.id].running" class="mb-progress">
          <div class="mb-prog-head">
            <span class="mb-prog-phase">
              <NIcon :component="SyncOutline" class="mb-spin" />
              {{ state[m.id].progress.phase || '处理中…' }}
            </span>
            <span class="mb-prog-num">{{ state[m.id].progress.done }}/{{ state[m.id].progress.total }} 只 · {{ pct(state[m.id].progress) }}%</span>
          </div>
          <NProgress type="line" :percentage="pct(state[m.id].progress)" :height="12"
                     :processing="true" :indicator-placement="'inside'" />
          <div v-if="state[m.id].progress.note" class="mb-prog-note">正在处理：{{ state[m.id].progress.note }}</div>
        </div>
        <div v-if="state[m.id].error" class="mb-error">{{ state[m.id].error }}</div>

        <div v-if="state[m.id].result" class="mb-result">
          <div class="mb-overall">
            <div class="mb-kpi"><span class="lab">笔数</span><span class="val">{{ state[m.id].result!.overall.n }}</span></div>
            <div class="mb-kpi"><span class="lab">胜率</span><span class="val" :class="winClass(state[m.id].result!.overall.win)">{{ state[m.id].result!.overall.win.toFixed(1) }}%</span></div>
            <div class="mb-kpi"><span class="lab">均收</span><span class="val" :class="state[m.id].result!.overall.avg>=0?'up':'down'">{{ state[m.id].result!.overall.avg>=0?'+':'' }}{{ state[m.id].result!.overall.avg.toFixed(2) }}%</span></div>
            <div class="mb-kpi"><span class="lab" title="盈利因子=赚的总额÷亏的总额, 大于1才整体盈利, 越大越好; 显示『无亏损』表示这期一笔亏损都没有">盈利因子</span><span class="val">{{ pfText(state[m.id].result!.overall.pf) }}</span></div>
            <div class="mb-kpi"><span class="lab">口径</span><span class="val small">{{ state[m.id].result!.koujing==='5m'?'5分钟可成交':'日线' }}</span></div>
          </div>
          <div v-if="state[m.id].result!.monthly" class="mb-monthly">
            <div class="mb-mrow mb-mhead"><span>月份</span><span>笔数</span><span>胜率</span><span>均收</span><span title="盈利因子=赚的总额÷亏的总额, 大于1才整体盈利; 显示『无亏损』表示该月一笔亏损都没有">盈利因子</span></div>
            <div v-for="(st, ym) in state[m.id].result!.monthly" :key="ym" class="mb-mrow">
              <span>{{ ym }}</span><span>{{ st.n }}</span>
              <span :class="winClass(st.win)">{{ st.win.toFixed(0) }}%</span>
              <span :class="st.avg>=0?'up':'down'">{{ st.avg>=0?'+':'' }}{{ st.avg.toFixed(1) }}%</span>
              <span>{{ pfText(st.pf) }}</span>
            </div>
          </div>

          <!-- 逐笔交易明细 -->
          <div v-if="state[m.id].result!.trades && state[m.id].result!.trades!.length" class="mb-trades">
            <div class="mb-trades-toggle" @click="state[m.id].tradesOpen = !state[m.id].tradesOpen">
              <NIcon :component="ChevronDownOutline" :class="{ rot: state[m.id].tradesOpen }" />
              交易明细({{ state[m.id].result!.trades_total }} 笔<span v-if="state[m.id].result!.trades_truncated">,仅列最近 1000</span>)
            </div>
            <div v-show="state[m.id].tradesOpen" class="mb-trades-wrap">
              <table class="mb-ttable">
                <thead>
                  <tr>
                    <th>买入日期</th><th>股票</th>
                    <th title="鼠标悬停模型名可看触发详情">触发模型</th>
                    <th>买入价</th>
                    <th title="按腿堆叠: 卖半→清剩各一行(仓位% · 机制 · 日期 @价 · 该腿毛收益·持有天数)">出场明细</th>
                    <th title="持有期相对买入价的最高浮盈/最大浮亏及发生在第几个交易日。最高浮盈远高于实际收益=有卖飞嫌疑;最大浮亏深=持有难度大/插针风险">持有期 最高/最低</th>
                    <th>持股(交易日)</th><th title="净收益,已扣双边费0.3%;先止盈卖半的为两段加权">收益</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(t, idx) in state[m.id].result!.trades" :key="idx">
                    <td class="mb-date">{{ t.buy_date }}</td>
                    <td class="nowrap mb-stk"><span class="mb-stk-name">{{ t.name }}</span><span class="mb-stk-code">{{ t.code }}</span></td>
                    <td><span class="mb-chip" :title="t.detail || t.model">{{ t.model }}</span></td>
                    <td class="mb-px">{{ t.buy_price.toFixed(2) }}</td>
                    <td class="mb-exit">
                      <div v-for="(lg, li) in tradeLegs(t)" :key="li" class="mb-leg">
                        <span class="mb-leg-pos" :class="lg.pos < 100 ? 'half' : 'full'">{{ lg.pos }}%</span>
                        <span class="mb-leg-reason">{{ lg.reason }}</span>
                        <span class="mb-leg-meta">{{ legDate(lg.date) }} @{{ lg.price.toFixed(2) }}</span>
                        <span v-if="lg.ret_pct!=null" class="mb-leg-ret" :class="lg.ret_pct>=0?'up':'down'">{{ lg.ret_pct>=0?'+':'' }}{{ lg.ret_pct.toFixed(1) }}%<span v-if="lg.hold!=null" class="mb-leg-hold"> · T+{{ lg.hold }}</span></span>
                      </div>
                    </td>
                    <td class="mb-mm">
                      <div><span class="mb-mm-lab">高</span><span class="up">{{ mfeText(t) }}</span></div>
                      <div><span class="mb-mm-lab">低</span><span :class="(t.mae_pct ?? 0) < 0 ? 'down' : 'mut'">{{ maeText(t) }}</span></div>
                    </td>
                    <td class="ctr mb-hold">{{ t.hold_days }}</td>
                    <td><span class="mb-ret-pill" :class="t.ret_pct>=0?'up':'down'">{{ t.ret_pct>=0?'+':'' }}{{ t.ret_pct.toFixed(2) }}%</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 历史记录详情 -->
    <NModal v-model:show="detailOpen" preset="card" style="max-width:1280px;width:96vw" title="回测记录详情">
      <div v-if="detailLoading" style="padding:8px 0"><NSkeleton text :repeat="5" /></div>
      <div v-else-if="detailRun" class="mb-detail">
        <div class="mb-detail-meta">
          <NTag size="small" :bordered="false" type="info">{{ detailRun.model_name }}</NTag>
          <span>{{ scopeText(detailRun.scope) }} · {{ koujingText(detailRun.koujing) }}</span>
          <span>{{ detailRun.window_start }} ~ {{ detailRun.window_end }}</span>
          <span class="mb-detail-time">{{ detailRun.created_at }}</span>
        </div>
        <div class="mb-detail-params">
          <span v-for="(v, k) in detailRun.params" :key="k" class="mb-detail-pp">{{ paramLabel(String(k)) }}：<b>{{ paramValueText(String(k), v) }}</b></span>
        </div>
        <div class="mb-overall">
          <div class="mb-kpi"><span class="lab">笔数</span><span class="val">{{ detailRun.overall.n }}</span></div>
          <div class="mb-kpi"><span class="lab">胜率</span><span class="val" :class="winClass(detailRun.overall.win)">{{ detailRun.overall.win.toFixed(1) }}%</span></div>
          <div class="mb-kpi"><span class="lab">均收</span><span class="val" :class="detailRun.overall.avg>=0?'up':'down'">{{ detailRun.overall.avg>=0?'+':'' }}{{ detailRun.overall.avg.toFixed(2) }}%</span></div>
          <div class="mb-kpi"><span class="lab">盈利因子</span><span class="val">{{ pfText(detailRun.overall.pf) }}</span></div>
        </div>
        <div v-if="detailRun.monthly && Object.keys(detailRun.monthly).length" class="mb-monthly">
          <div class="mb-mrow mb-mhead"><span>月份</span><span>笔数</span><span>胜率</span><span>均收</span><span>盈利因子</span></div>
          <div v-for="(st, ym) in detailRun.monthly" :key="ym" class="mb-mrow">
            <span>{{ ym }}</span><span>{{ st.n }}</span>
            <span :class="winClass(st.win)">{{ st.win.toFixed(0) }}%</span>
            <span :class="st.avg>=0?'up':'down'">{{ st.avg>=0?'+':'' }}{{ st.avg.toFixed(1) }}%</span>
            <span>{{ pfText(st.pf) }}</span>
          </div>
        </div>
        <div v-if="detailRun.trades && detailRun.trades.length" class="mb-trades-wrap" style="margin-top:12px;max-height:50vh">
          <table class="mb-ttable">
            <thead><tr>
              <th>买入日期</th><th>股票</th><th>触发模型</th><th>买入价</th>
              <th>出场明细</th><th title="持有期相对买入价的最高浮盈/最大浮亏及第几个交易日">持有期 最高/最低</th><th>持股(交易日)</th><th>收益</th>
            </tr></thead>
            <tbody>
              <tr v-for="(t, idx) in detailRun.trades" :key="idx">
                <td class="mb-date">{{ t.buy_date }}</td>
                <td class="nowrap mb-stk"><span class="mb-stk-name">{{ t.name }}</span><span class="mb-stk-code">{{ t.code }}</span></td>
                <td><span class="mb-chip" :title="t.detail || t.model">{{ t.model }}</span></td>
                <td class="mb-px">{{ t.buy_price.toFixed(2) }}</td>
                <td class="mb-exit">
                  <div v-for="(lg, li) in tradeLegs(t)" :key="li" class="mb-leg">
                    <span class="mb-leg-pos" :class="lg.pos < 100 ? 'half' : 'full'">{{ lg.pos }}%</span>
                    <span class="mb-leg-reason">{{ lg.reason }}</span>
                    <span class="mb-leg-meta">{{ legDate(lg.date) }} @{{ lg.price.toFixed(2) }}</span>
                    <span v-if="lg.ret_pct!=null" class="mb-leg-ret" :class="lg.ret_pct>=0?'up':'down'">{{ lg.ret_pct>=0?'+':'' }}{{ lg.ret_pct.toFixed(1) }}%<span v-if="lg.hold!=null" class="mb-leg-hold"> · T+{{ lg.hold }}</span></span>
                  </div>
                </td>
                <td class="mb-mm">
                  <div><span class="mb-mm-lab">高</span><span class="up">{{ mfeText(t) }}</span></div>
                  <div><span class="mb-mm-lab">低</span><span :class="(t.mae_pct ?? 0) < 0 ? 'down' : 'mut'">{{ maeText(t) }}</span></div>
                </td>
                <td class="ctr mb-hold">{{ t.hold_days }}</td>
                <td><span class="mb-ret-pill" :class="t.ret_pct>=0?'up':'down'">{{ t.ret_pct>=0?'+':'' }}{{ t.ret_pct.toFixed(2) }}%</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.mb-view { padding: 16px; max-width: 1100px; margin: 0 auto; }
.mb-head h2 { margin: 0 0 4px; font-size: 20px; }
.mb-sub { margin: 0 0 16px; font-size: 12px; color: var(--fg-subtle); line-height: 1.6; }
.mb-list { display: flex; flex-direction: column; gap: 14px; }
/* 历史记录 */
.mb-history-card { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: 10px; padding: 12px 16px; margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.03); }
.mb-hist-toggle { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; color: var(--fg-default); cursor: pointer; user-select: none; touch-action: manipulation; }
.mb-hist-toggle :deep(.rot) { transform: rotate(180deg); transition: transform .2s; }
.mb-hist-hint { font-size: 11px; font-weight: 400; color: var(--fg-subtle); margin-left: 4px; }
.mb-hist-body { margin-top: 10px; }
.mb-hist-empty { font-size: 12px; color: var(--fg-subtle); padding: 6px 0; }
/* 历史记录·战绩行卡片 */
.mb-hist-list { display: flex; flex-direction: column; gap: 8px; }
.mb-hrow { display: flex; align-items: center; gap: 16px; padding: 10px 14px; background: var(--bg-default); border: 1px solid var(--border-muted); border-radius: 9px; transition: box-shadow .15s, border-color .15s, background .15s; }
.mb-hrow:hover { background: var(--bg-surface); border-color: color-mix(in srgb, var(--accent-fg) 25%, transparent); box-shadow: 0 2px 10px color-mix(in srgb, var(--accent-fg) 10%, transparent); }
.mb-hrow-main { flex: 1; min-width: 0; }
.mb-hrow-top { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.mb-hrow-model { font-size: 14px; font-weight: 700; color: var(--fg-default); }
.mb-hrow-scope { font-size: 11px; color: var(--accent-fg); background: var(--accent-bg-muted); padding: 1px 8px; border-radius: 4px; white-space: nowrap; }
.mb-hrow-time { font-size: 11px; color: var(--fg-subtle); margin-left: auto; white-space: nowrap; font-variant-numeric: tabular-nums; }
.mb-hrow-win { margin-top: 4px; font-size: 11px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; }
.mb-hrow-stats { display: flex; align-items: stretch; flex-shrink: 0; border: 1px solid var(--border-default); border-radius: 8px; overflow: hidden; background: var(--bg-surface); font-variant-numeric: tabular-nums; }
.mb-hstat { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; padding: 4px 14px; min-width: 54px; }
.mb-hstat + .mb-hstat { border-left: 1px solid var(--border-muted); }
.mb-hstat .v { font-size: 15px; font-weight: 700; color: var(--fg-default); line-height: 1.1; }
.mb-hstat .l { font-size: 10px; color: var(--fg-subtle); }
.mb-hstat .v.good { color: var(--success-fg); } .mb-hstat .v.bad { color: var(--danger-fg); } .mb-hstat .v.mid { color: var(--warn-fg); }
.mb-hstat .v.up { color: var(--up-fg); } .mb-hstat .v.down { color: var(--down-fg); }
.mb-hrow-actions { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
@media (max-width: 768px) {
  .mb-hrow { flex-direction: column; align-items: stretch; gap: 10px; padding: 12px; }
  .mb-hrow-stats { justify-content: space-between; }
  .mb-hstat { flex: 1; min-width: 0; padding: 4px 6px; }
  .mb-hrow-actions { justify-content: flex-end; }
}
.mb-detail-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 12px; color: var(--fg-muted); margin-bottom: 8px; }
.mb-detail-time { color: var(--fg-subtle); }
.mb-detail-params { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 11px; color: var(--fg-subtle); margin-bottom: 10px; padding: 8px; background: var(--bg-default); border-radius: 6px; }
.mb-detail-pp b { color: var(--fg-muted); }
.mb-card { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.03); }
.mb-card-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.mb-title { display: flex; align-items: center; gap: 8px; }
.mb-name { font-size: 15px; font-weight: 600; }
.mb-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.seg { display: inline-flex; border: 1px solid var(--border-default); border-radius: 6px; overflow: hidden; }
.seg button { border: none; background: var(--bg-surface); padding: 4px 10px; font-size: 12px; color: var(--fg-muted); cursor: pointer; touch-action: manipulation; }
.seg button.on { background: var(--accent-fg); color: var(--on-emphasis); }
.seg button + button { border-left: 1px solid var(--border-default); }
.mb-warn { margin-top: 8px; font-size: 11px; color: var(--warn-fg); background: var(--warn-bg-muted); border: 1px solid color-mix(in srgb, var(--warn-fg) 32%, transparent); border-radius: 6px; padding: 4px 8px; }
.mb-params-toggle { display: flex; align-items: center; gap: 6px; margin-top: 10px; font-size: 12px; color: var(--fg-muted); cursor: pointer; user-select: none; touch-action: manipulation; }
.mb-params-toggle :deep(.rot) { transform: rotate(180deg); transition: transform .2s; }
.mb-params { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px 20px; margin-top: 8px; padding: 10px; background: var(--bg-default); border-radius: 6px; }
.mb-param { display: grid; grid-template-columns: 1fr 120px; align-items: center; gap: 8px; min-width: 0; }
.mb-pk { font-size: 12px; color: var(--fg-muted); cursor: help; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mb-progress { margin-top: 12px; }
.mb-prog-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.mb-prog-phase { display: flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; color: var(--accent-fg); }
.mb-prog-num { font-size: 12px; color: var(--fg-subtle); white-space: nowrap; }
.mb-prog-note { margin-top: 5px; font-size: 11px; color: var(--fg-subtle); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mb-spin { animation: mb-rotate 1s linear infinite; font-size: 13px; }
@keyframes mb-rotate { to { transform: rotate(360deg); } }
.mb-prog-txt { font-size: 11px; color: var(--fg-subtle); white-space: nowrap; }
.mb-error { margin-top: 10px; font-size: 12px; color: var(--danger-fg); }
.mb-result { margin-top: 12px; }
.mb-overall { display: flex; flex-wrap: wrap; gap: 18px; padding: 10px 4px; border-top: 1px dashed var(--border-muted); font-variant-numeric: tabular-nums; }
.mb-kpi { display: flex; flex-direction: column; gap: 2px; }
.mb-kpi .lab { font-size: 11px; color: var(--fg-subtle); }
.mb-kpi .val { font-size: 17px; font-weight: 700; }
.mb-kpi .val.small { font-size: 13px; font-weight: 500; }
.val.good { color: var(--success-fg); } .val.mid { color: var(--warn-fg); } .val.bad { color: var(--danger-fg); }
/* 收益类按 A 股惯例: 正数红、负数绿(红涨绿跌); 胜率仍用 good/bad 表质量 */
.val.up { color: var(--up-fg); } .val.down { color: var(--down-fg); }
.mb-monthly { margin-top: 10px; font-size: 12px; font-variant-numeric: tabular-nums; }
.mb-mrow { display: grid; grid-template-columns: 1.4fr 1fr 1fr 1.2fr 1fr; padding: 4px 6px; }
.mb-mrow:nth-child(even) { background: var(--bg-default); }
.mb-mhead { font-weight: 600; color: var(--fg-subtle); border-bottom: 1px solid var(--border-muted); }
.mb-mrow .good { color: var(--success-fg); } .mb-mrow .bad { color: var(--danger-fg); }
.mb-mrow .up { color: var(--up-fg); } .mb-mrow .down { color: var(--down-fg); }
.mb-trades { margin-top: 12px; border-top: 1px dashed var(--border-muted); padding-top: 8px; }
.mb-trades-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--fg-muted); cursor: pointer; user-select: none; touch-action: manipulation; }
.mb-trades-toggle :deep(.rot) { transform: rotate(180deg); transition: transform .2s; }
.mb-trades-wrap { margin-top: 8px; overflow-x: auto; }
.mb-ttable { width: 100%; border-collapse: collapse; font-size: 12px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.mb-ttable th { text-align: left; font-weight: 600; color: var(--fg-subtle); font-size: 11px; letter-spacing: .3px; padding: 9px 12px; border-bottom: 1px solid var(--border-default); position: sticky; top: 0; background: var(--bg-surface); z-index: 1; }
.mb-ttable td { padding: 9px 12px; border-bottom: 1px solid var(--border-muted); vertical-align: middle; }
.mb-ttable tbody tr:hover { background: var(--bg-sunken); }
.mb-ttable .good { color: var(--success-fg); font-weight: 600; } .mb-ttable .bad { color: var(--danger-fg); font-weight: 600; }
.mb-ttable .up { color: var(--up-fg); font-weight: 600; } .mb-ttable .down { color: var(--down-fg); font-weight: 600; }
.mb-ttable .ctr { text-align: center; }
.mb-ttable .nowrap { white-space: nowrap; }
.mb-date { color: var(--fg-muted); }
/* 股票: 名称加粗 + 代码淡显小字, 上下两行 */
.mb-stk { line-height: 1.3; }
.mb-stk-name { display: block; font-weight: 600; color: var(--fg-default); }
.mb-stk-code { display: block; font-size: 11px; color: var(--fg-subtle); }
.mb-px { color: var(--fg-default); font-weight: 500; }
.mb-hold { color: var(--fg-muted); }
/* 触发模型 chip */
.mb-chip { display: inline-block; background: var(--bg-sunken); color: var(--fg-muted); border-radius: 5px; padding: 2px 8px; font-size: 11px; font-weight: 500; }
/* 收益药丸: 红涨绿跌, 视觉锚点 */
.mb-ret-pill { display: inline-block; padding: 3px 11px; border-radius: 999px; font-weight: 700; font-size: 13px; font-variant-numeric: tabular-nums; letter-spacing: .2px; }
.mb-ttable .mb-ret-pill.up { background: var(--up-bg-muted); } .mb-ttable .mb-ret-pill.down { background: var(--down-bg-muted); }
/* 出场明细: 一买入一单元格, 多腿(卖半→清剩)逐行堆叠对齐 */
.mb-ttable td.mb-exit { white-space: normal; min-width: 272px; vertical-align: middle; }
/* 历史详情弹窗内的表更紧凑, 配合加宽的弹窗去掉横向滚动条 */
.mb-detail .mb-ttable th, .mb-detail .mb-ttable td { padding: 7px 9px; }
.mb-detail .mb-ttable td.mb-exit { min-width: 248px; }
.mb-detail .mb-leg-ret { flex-basis: 66px; }
.mb-leg { display: flex; align-items: baseline; gap: 8px; line-height: 1.7; }
.mb-leg + .mb-leg { margin-top: 3px; padding-top: 3px; border-top: 1px dashed var(--border-muted); }
.mb-leg-pos { flex: 0 0 auto; min-width: 36px; text-align: center; font-weight: 700; font-size: 10px; border-radius: 4px; padding: 1px 6px; letter-spacing: .2px; }
.mb-leg-pos.half { background: var(--accent-bg-muted); color: var(--accent-fg); }
.mb-leg-pos.full { background: var(--bg-sunken); color: var(--fg-muted); }
.mb-leg-reason { flex: 1 1 auto; min-width: 0; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mb-leg-meta { flex: 0 0 auto; color: var(--fg-subtle); font-size: 11px; margin-left: auto; white-space: nowrap; font-variant-numeric: tabular-nums; }
.mb-leg-ret { flex: 0 0 72px; font-size: 11px; font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; text-align: right; }
.mb-leg-hold { color: var(--fg-subtle); font-weight: 400; }
/* 持有期最高/最低 */
.mb-ttable td.mb-mm { white-space: nowrap; font-size: 11px; font-variant-numeric: tabular-nums; line-height: 1.55; vertical-align: middle; }
.mb-mm-lab { color: var(--fg-subtle); font-size: 10px; margin-right: 5px; }
.mb-ttable .mb-mm .mut { color: var(--fg-subtle); }
.mb-mrow .mid { color: var(--warn-fg); }

@media (max-width: 768px) {
  .mb-view { padding: 10px; }
  .mb-card-head { flex-direction: column; align-items: stretch; }
  .mb-controls { justify-content: flex-start; }
  .seg button { padding: 6px 10px; }
  .mb-overall { gap: 12px; justify-content: space-between; }
  .mb-kpi .val { font-size: 15px; }
}
</style>
