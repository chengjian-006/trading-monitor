<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, h } from 'vue'
import { NCard, NDataTable, NButton, NInputNumber, NPopconfirm, NStatistic, NGrid, NGi, NTag } from 'naive-ui'
import { usePaperStore } from '../stores/paper-trading'
import { resetPaperAccount, updatePaperSettings, type AccountKey } from '../api/paper-trading'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import PaperEquityChart from '../components/common/PaperEquityChart.vue'

const store = usePaperStore()
const message = useGlobalMessage()
const initCap = ref(1000000)
const maxPos = ref(10)

// 当前账户是否"无限子弹"(现金可透支/不限持仓/可加仓)
const isUnlimited = computed(() => (store.summary?.unlimited_bullets ?? 0) === 1)

function syncSettingsFromSummary() {
  if (store.summary) { initCap.value = store.summary.initial_capital; maxPos.value = store.summary.max_positions }
}

let liveTimer: number | undefined
onMounted(async () => {
  await store.loadAll()
  syncSettingsFromSummary()
  // 盘中轮询: 每60s刷概览+持仓现价(浮盈随之联动); 页面不可见时暂停
  liveTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') store.refreshLive().catch(() => {})
  }, 60_000)
})
onUnmounted(() => { if (liveTimer) window.clearInterval(liveTimer) })

async function onSwitchAccount(key: string | number) {
  store.setAccount(key as AccountKey)
  await store.loadAll()
  syncSettingsFromSummary()
}

async function onSaveSettings() {
  try { await updatePaperSettings(initCap.value, maxPos.value, store.accountKey); message.success('设置已保存(本金在下次重置生效)'); await store.loadAll() }
  catch (e: any) { message.error('保存失败: ' + (e?.message || e)) }
}
async function onReset() {
  try { await resetPaperAccount(initCap.value, maxPos.value, store.accountKey); message.success('已重置当前账户'); await store.loadAll() }
  catch (e: any) { message.error('重置失败: ' + (e?.message || e)) }
}

function fmtTime(raw?: string): string {
  if (!raw) return '-'
  return raw.replace('T', ' ').slice(0, 19)  // 2026-06-10T10:02:09 → 2026-06-10 10:02:09
}
// 盈亏配色(A股: 红盈绿亏) + 金额/百分比格式化
function pnlColor(v?: number | null): string | undefined { return v == null ? undefined : (v >= 0 ? 'var(--up-fg)' : 'var(--down-fg)') }
function fmtMoney(v?: number | null): string { return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) }
function fmtPct(v?: number | null): string { return v == null ? '' : ` (${v >= 0 ? '+' : ''}${v.toFixed(2)}%)` }

const posCols = [
  { title: '名称', key: 'name' }, { title: '代码', key: 'code' },
  { title: '股数', key: 'qty' }, { title: '成本', key: 'avg_cost' },
  { title: '现价', key: 'price' },
  { title: '浮盈%', key: 'float_pct', render: (r: any) => (r.float_pct ?? '—') + (r.float_pct != null ? '%' : '') },
  { title: '买点', key: 'entry_model_name', render: (r: any) => r.entry_model_name || r.entry_signal_id || '—' },
]
const tradeCols = [
  { title: '时间', key: 'trade_time', render: (r: any) => fmtTime(r.trade_time) }, { title: '名称', key: 'name' },
  { title: '方向', key: 'side', render: (r: any) => (r.side === 'buy' ? '买' : '卖') },
  { title: '状态', key: 'status', render: (r: any) => r.status === 'failed'
      ? h('span', { style: { color: 'var(--danger-fg)' } }, '失败')
      : h('span', { style: { color: 'var(--success-fg)' } }, '成功') },
  { title: '股数', key: 'qty', render: (r: any) => r.status === 'failed' ? '—' : r.qty },
  { title: '价', key: 'price' },
  { title: '成交额', key: '_amount', render: (r: any) => (r.status !== 'failed' && r.qty != null && r.price != null) ? (Number(r.qty) * Number(r.price)).toFixed(2) : '—' },
  { title: '费', key: 'fee', render: (r: any) => r.status === 'failed' ? '—' : r.fee },
  { title: '信号', key: 'signal_name' },
  { title: '原因', key: 'fail_reason', render: (r: any) => r.fail_reason || '—' },
  { title: '盈亏', key: 'realized_pnl', render: (r: any) => {
      const isSell = r.side === 'sell' && r.realized_pnl != null
      const v = isSell ? Number(r.realized_pnl) : r._float_pnl
      if (v == null) return '—'
      const pct = isSell ? (r.realized_pnl_pct != null ? Number(r.realized_pnl_pct) : null) : r._float_pct
      return h('span', null, [
        h('span', { style: { color: pnlColor(v) } }, fmtMoney(v) + fmtPct(pct)),
        h('span', { style: { color: 'var(--fg-subtle)', marginLeft: '4px', fontSize: '12px' } }, isSell ? '实' : '浮'),
      ])
    } },
]
// 买入行实时浮盈: 该票仍在持仓时按 (现价−买入价)×剩余股数 计。
// 同一只票同时只有一笔持仓(已持仓跳过买入), 卖半后剩余股数也属这笔买入; 清仓后回「—」(盈亏在卖出行)。
const tradesView = computed(() => store.trades.map((t: any) => {
  if (t.side !== 'buy' || t.status === 'failed') return t
  const pos = store.positions.find((p: any) => p.code === t.code)
  const px = pos ? Number(pos.price) : 0
  const buyPx = Number(t.price)
  if (!pos || !(px > 0) || !(buyPx > 0)) return t
  return { ...t, _float_pnl: (px - buyPx) * Number(pos.qty), _float_pct: (px / buyPx - 1) * 100 }
}))
const modelCols = [
  { title: '买点模型', key: 'model' }, { title: '笔数', key: 'n' },
  { title: '胜', key: 'win' }, { title: '总盈亏', key: 'pnl' },
  { title: '平均%', key: 'avg_pct', render: (r: any) => Number(r.avg_pct).toFixed(2) + '%' },
]
</script>

<template>
  <div style="padding: 12px; display: flex; flex-direction: column; gap: 12px;">
    <!-- v1.7.590: 账户切换从铺满整行的 segment 改为「紧凑胶囊切换器 + 账户速览」工具栏 -->
    <div class="pt-bar">
      <div class="pt-seg" role="tablist">
        <button role="tab" :aria-selected="store.accountKey === 'default'"
          :class="{ on: store.accountKey === 'default' }" @click="onSwitchAccount('default')">
          <span class="dot std"></span>模拟账户
        </button>
        <button role="tab" :aria-selected="store.accountKey === 'unlimited'" class="unl"
          :class="{ on: store.accountKey === 'unlimited' }" @click="onSwitchAccount('unlimited')">
          <span class="dot fire"></span>无限子弹
        </button>
      </div>
      <div v-if="store.summary" class="pt-quick">
        <div class="q"><span class="l">总资产</span><span class="v">{{ Math.round(store.summary.total_equity).toLocaleString() }}</span></div>
        <div class="q"><span class="l">当日</span><span class="v" :style="{ color: pnlColor(store.summary.today_pnl) }">{{ fmtMoney(store.summary.today_pnl) }}</span></div>
        <div class="q"><span class="l">总盈亏</span><span class="v" :style="{ color: pnlColor(store.summary.total_pnl) }">{{ fmtMoney(store.summary.total_pnl) }}{{ fmtPct(store.summary.total_return_pct) }}</span></div>
      </div>
    </div>

    <NCard size="small">
      <template #header>
        <span>{{ store.summary?.account_name || '模拟账户' }} · 概览</span>
        <NTag v-if="store.summary" size="small" :type="isUnlimited ? 'warning' : 'default'" style="margin-left: 8px">
          每笔 {{ store.summary.buy_position_pct }}% 仓位
        </NTag>
        <NTag v-if="isUnlimited" size="small" type="error" style="margin-left: 6px">无限子弹 · 可透支/可加仓</NTag>
      </template>
      <!-- v1.7.571: responsive="screen" 只对响应式cols字符串生效, 原来写死 :cols="4" 等于没配,
           手机上9个统计块挤成4列7位数字溢出; 改用断点式cols: 手机2列/小屏3列/中屏起4列 -->
      <NGrid v-if="store.summary" cols="2 s:3 m:4" :x-gap="12" :y-gap="12" responsive="screen">
        <NGi><NStatistic label="总资产" :value="store.summary.total_equity" /></NGi>
        <NGi>
          <NStatistic label="当日盈亏">
            <span :style="{ color: pnlColor(store.summary.today_pnl) }">{{ fmtMoney(store.summary.today_pnl) }}{{ fmtPct(store.summary.today_pnl_pct) }}</span>
          </NStatistic>
        </NGi>
        <NGi>
          <NStatistic label="总体盈亏">
            <span :style="{ color: pnlColor(store.summary.total_pnl) }">{{ fmtMoney(store.summary.total_pnl) }}{{ fmtPct(store.summary.total_return_pct) }}</span>
          </NStatistic>
        </NGi>
        <NGi><NStatistic label="已实现胜率%" :value="store.summary.win_rate ?? '—'" /></NGi>
        <NGi><NStatistic label="盈亏因子" :value="store.summary.profit_factor ?? '—'" /></NGi>
        <NGi><NStatistic label="现金" :value="store.summary.cash" /></NGi>
        <NGi><NStatistic label="持仓市值" :value="store.summary.holdings_mv" /></NGi>
        <NGi><NStatistic label="最大回撤%" :value="store.summary.max_drawdown_pct" /></NGi>
        <NGi><NStatistic label="持仓数" :value="store.summary.position_count + (isUnlimited ? ' (不限)' : '/' + store.summary.max_positions)" /></NGi>
      </NGrid>
    </NCard>

    <NCard title="资金曲线" size="small">
      <PaperEquityChart :data="store.equity" />
    </NCard>

    <NCard title="账户设置" size="small">
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
        <span>初始资金</span><NInputNumber v-model:value="initCap" :min="10000" :step="10000" style="width: 160px" />
        <template v-if="!isUnlimited">
          <span>最大持仓数</span><NInputNumber v-model:value="maxPos" :min="1" :max="50" style="width: 120px" />
        </template>
        <span v-else style="color: var(--fg-subtle)">无限子弹账户不限持仓数</span>
        <NButton size="small" @click="onSaveSettings">保存设置</NButton>
        <NPopconfirm @positive-click="onReset">
          <template #trigger><NButton size="small" type="warning">重置账户</NButton></template>
          确认用初始资金 {{ initCap }} 重置(清空持仓/流水/曲线)?
        </NPopconfirm>
      </div>
    </NCard>

    <NCard title="当前持仓" size="small">
      <NDataTable :columns="posCols" :data="store.positions" :row-key="(r:any) => r.code" :bordered="false" size="small" :resizable-columns="true" />
    </NCard>
    <NCard title="按买点模型 · 已实现胜率" size="small">
      <NDataTable :columns="modelCols" :data="store.modelStats" :row-key="(r:any) => r.model" :bordered="false" size="small" :resizable-columns="true" />
    </NCard>
    <NCard title="成交流水" size="small">
      <NDataTable :columns="tradeCols" :data="tradesView" :row-key="(r:any) => r.id" :bordered="false" size="small" :resizable-columns="true" />
    </NCard>
  </div>
</template>

<style scoped>
/* 账户切换工具栏: 左=胶囊切换器 右=当前账户速览 */
.pt-bar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 10px; padding: 8px 14px; }
.pt-seg { display: inline-flex; background: var(--bg-sunken, var(--bg-default)); border: 1px solid var(--border-default); border-radius: 22px; padding: 3px; gap: 3px; }
.pt-seg button { appearance: none; border: 0; background: transparent; font: inherit; font-size: 13.5px; font-weight: 700; color: var(--fg-subtle); padding: 5px 18px; border-radius: 18px; cursor: pointer; display: inline-flex; align-items: center; gap: 7px; transition: color .18s, background .18s, box-shadow .18s; white-space: nowrap; }
.pt-seg button:hover { color: var(--fg-muted); }
.pt-seg button.on { background: var(--bg-surface); color: var(--accent-fg); box-shadow: 0 1px 4px rgba(40, 60, 100, .16); }
.pt-seg button.unl.on { color: var(--warn-fg); box-shadow: 0 1px 4px rgba(160, 90, 10, .18); }
.pt-seg .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.pt-seg .dot.std { background: var(--accent-fg); opacity: .35; }
.pt-seg .dot.fire { background: linear-gradient(135deg, var(--warn-fg), var(--up-fg)); opacity: .4; }
.pt-seg button.on .dot { opacity: 1; }
.pt-quick { margin-left: auto; display: flex; gap: 22px; align-items: center; }
.pt-quick .q { display: flex; align-items: baseline; gap: 7px; }
.pt-quick .l { font-size: 12px; color: var(--fg-subtle); }
.pt-quick .v { font-size: 15px; font-weight: 800; font-variant-numeric: tabular-nums; }

@media (max-width: 768px) {
  .pt-bar { padding: 8px 10px; }
  .pt-seg { width: 100%; }
  .pt-seg button { flex: 1; justify-content: center; }
  .pt-quick { margin-left: 0; width: 100%; justify-content: space-between; gap: 10px; }
  .pt-quick .q { flex-direction: column; gap: 1px; align-items: flex-start; }
  .pt-quick .v { font-size: 14px; }
}
</style>
