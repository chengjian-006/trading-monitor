<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { NTag, NButton, NIcon, NPopconfirm } from 'naive-ui'
import { useGlobalMessage } from '../../composables/useGlobalMessage'
import { formatYi } from '../../utils/formatAmount'
import { StarOutline, Star, TrashOutline, SparklesOutline, NotificationsOutline, Notifications, PricetagsOutline, Create, CreateOutline, HelpCircleOutline, ChatboxEllipsesOutline, OpenOutline } from '@vicons/ionicons5'
import type { Stock } from '../../types'
import { useStockStore } from '../../stores/stock'
import { useSignalStore } from '../../stores/signal'
// v1.7.x: SignalSummaryBar 移到 PoolView 顶部, 此处不再引入
import SubstanceCheckDrawer from './SubstanceCheckDrawer.vue'
import StockAlertModal from './StockAlertModal.vue'
import StockMetaModal from './StockMetaModal.vue'
import StrategyEditModal from './StrategyEditModal.vue'
import StockWencaiModal from './StockWencaiModal.vue'
import StrategyText from './StrategyText.vue'
import { useUiStore } from '../../stores/ui'
import { useStockAlerts } from '../../composables/useStockAlerts'
import { useKeyedSubmitGuard } from '../../composables/useSubmitGuard'
import { onMounted } from 'vue'

const stockStore = useStockStore()
const signalStore = useSignalStore()
const message = useGlobalMessage()
const ui = useUiStore()

// 点代码 → 全局通用个股详情弹窗
function openCharts(code: string, name: string) {
  ui.openStock(code, name)
}

import { useSignalGrouping } from '../../composables/useSignalGrouping'
const { signalsByCode } = useSignalGrouping(() => signalStore.signals)

// v1.7.x: 买入/卖出独立计数, 给红绿双气泡用
function signalCounts(code: string): { buy: number; sell: number; other: number } {
  const list = signalsByCode.value.get(code)
  if (!list || !list.length) return { buy: 0, sell: 0, other: 0 }
  let buy = 0, sell = 0
  for (const s of list) {
    if (s.direction === 'buy' || s.direction === 'add') buy++
    else if (s.direction === 'sell' || s.direction === 'reduce') sell++
  }
  return { buy, sell, other: list.length - buy - sell }
}

const props = defineProps<{ stocks: Stock[] }>()

// AI 核查抽屉状态
const substanceShow = ref(false)
const substanceStock = ref<Stock | null>(null)

function openSubstance(s: Stock) {
  substanceStock.value = s
  substanceShow.value = true
}

function formatAmount(val: number | null | undefined) {
  if (val == null || val === 0) return ''
  return formatYi(val)   // 成交额统一亿(2位)
}

// ── 涨停/跌停/连板 派生 (与 StockTable 同口径, 手机卡片复用) ──
function limitPct(s: Stock): number {
  const code = s.code || ''
  const name = s.name || ''
  if (/ST/i.test(name)) return 5
  if (/^(688|689)/.test(code)) return 20
  if (/^(300|301)/.test(code)) return 20
  if (/^(8|4|92)/.test(code)) return 30
  return 10
}
function isLimitUp(s: Stock): boolean {
  return s.pct_change != null && s.pct_change >= limitPct(s) - 0.5
}
function isLimitDown(s: Stock): boolean {
  return s.pct_change != null && s.pct_change <= -(limitPct(s) - 0.5)
}

async function handleDelete(code: string, name: string) {
  try {
    await stockStore.removeStock(code)
    message.success(() => h('span', {}, [h('b', name), `（${code}）已删除`]))
  } catch {
    message.error(() => h('span', {}, ['删除 ', h('b', name), `（${code}）失败`]))
  }
}

// 关注 切换在途保护(按卡片 code 分别守卫, 点这张卡不禁用别的卡):
// 防的是快速连点 true→false→true 请求体每次都不同(API 层相同请求去重拦不住),
// 多个请求乱序返回后最终关注状态与用户所点相反。
const { isBusy: focusBusy, guardKey: guardFocus } = useKeyedSubmitGuard()
const handleToggleFocus = guardFocus(
  (s: Stock) => s.code,
  async (s: Stock) => {
    const next = s.focused ? 0 : 1
    try {
      await stockStore.updateStock(s.code, { focused: String(next) })
      s.focused = next
    } catch {
      message.error('操作失败')
    }
  },
)

// ── 自定义预警 ──
const { loadAlerts, reloadAlerts, summaryFor } = useStockAlerts()
const showAlertModal = ref(false)
const alertStock = ref<Stock | null>(null)
function openAlert(s: Stock) {
  alertStock.value = s
  showAlertModal.value = true
}
// 个股「问财提问」(v1.7.777): 新标签打开同花顺问财
const showWencaiModal = ref(false)
const wencaiStock = ref<Stock | null>(null)
function openWencai(s: Stock) { wencaiStock.value = s; showWencaiModal.value = true }
// 个股外链跳转 (v1.7.783): 东方财富股吧 / 同花顺个股页
function openGuba(code: string) { window.open(`https://guba.eastmoney.com/list,${code}.html`, '_blank', 'noopener,noreferrer') }
function openThsPage(code: string) { window.open(`https://stockpage.10jqka.com.cn/${code}/`, '_blank', 'noopener,noreferrer') }
// 个股策略 (v1.7.721 补手机端): 此前只有宽表能看/能改策略, 手机卡片版完全没有入口。
// 卡片内直接展开策略正文(手机没有 hover, 悬浮富卡那套用不了), 点正文或底部「策略」按钮都进编辑。
const showStrategyModal = ref(false)
const strategyStock = ref<Stock | null>(null)
function openStrategy(s: Stock) { strategyStock.value = s; showStrategyModal.value = true }
function onStrategySaved(code: string, text: string) {
  const hit = props.stocks.find((s) => s.code === code)
  if (hit) hit.strategy = text
}
// 分组/标签/备注 (v1.7.670)
const showMetaModal = ref(false)
const metaStock = ref<Stock | null>(null)
function openMeta(s: Stock) { metaStock.value = s; showMetaModal.value = true }
const groupOptions = computed(() =>
  [...new Set(props.stocks.map((s) => s.grp).filter((g): g is string => !!g))].sort())
onMounted(() => { loadAlerts() })
</script>

<template>
  <div class="stock-list">
    <!-- v1.7.x: SignalSummaryBar 已上移到 PoolView 顶部统一渲染, 此处不重复 -->
    <div v-for="s in stocks" :key="s.code"
      :class="['stock-card', {
        focused: s.focused,
        signaled: signalsByCode.has(s.code),
        hold: s.status === 'hold'
      }]"
    >
      <div class="stock-top">
        <div class="stock-top-left">
          <span v-if="s.focused" class="stock-star">*</span>
          <span class="stock-name" :style="{ color: s.focused ? 'var(--up-fg)' : s.status === 'hold' ? 'var(--accent-fg)' : signalsByCode.has(s.code) ? 'var(--warn-fg)' : 'inherit' }">{{ s.name }}</span>
          <span class="stock-code" role="button" tabindex="0" :style="{ color: s.focused ? 'var(--up-fg)' : 'var(--accent-fg)', textDecoration: 'underline', textDecorationStyle: 'dotted', cursor: 'pointer' }" title="点击查看分时 + 日K" :aria-label="`查看 ${s.name} 分时 + 日K`" @click="openCharts(s.code, s.name)" @keydown.enter="openCharts(s.code, s.name)">{{ s.code }}</span>
          <template v-if="signalsByCode.has(s.code)">
            <span v-if="signalCounts(s.code).buy > 0" class="signal-badge signal-badge-buy" :title="`${signalCounts(s.code).buy} 个买入信号`">{{ signalCounts(s.code).buy }}</span>
            <span v-if="signalCounts(s.code).sell > 0" class="signal-badge signal-badge-sell" :title="`${signalCounts(s.code).sell} 个卖出/减仓信号`">{{ signalCounts(s.code).sell }}</span>
            <span v-if="signalCounts(s.code).buy === 0 && signalCounts(s.code).sell === 0" class="signal-badge signal-badge-other" :title="`${signalCounts(s.code).other} 个提示信号`">{{ signalCounts(s.code).other }}</span>
          </template>
          <!-- 涨停/跌停/连板: 游资核心标签, 与宽表同口径 -->
          <span v-if="isLimitUp(s)" class="mini-badge badge-limit-up">涨停</span>
          <span v-else-if="isLimitDown(s)" class="mini-badge badge-limit-down">跌停</span>
          <span v-if="s.limit_up_days != null && s.limit_up_days >= 1" class="mini-badge" :class="s.limit_up_days >= 2 ? 'badge-lianban' : 'badge-shouban'">{{ s.limit_up_days >= 2 ? `${s.limit_up_days}连板` : '首板' }}</span>
        </div>
        <NTag size="small" :type="s.trade_type === 'short' ? 'info' : 'warning'" :bordered="false">
          {{ s.trade_type === 'short' ? '短线' : '中线' }}
        </NTag>
      </div>
      <div class="stock-middle">
        <span class="stock-price">{{ s.price != null ? s.price.toFixed(2) : '-' }}</span>
        <span
          v-if="s.pct_change != null"
          :class="['stock-pct', s.pct_change >= 0 ? 'up' : 'down']"
        >
          {{ s.pct_change >= 0 ? '+' : '' }}{{ s.pct_change.toFixed(2) }}%
        </span>
        <span v-if="s.speed != null && s.speed !== 0" :class="['stock-pct', s.speed >= 0 ? 'up' : 'down']" style="font-size: 12px;">
          涨速{{ s.speed >= 0 ? '+' : '' }}{{ s.speed.toFixed(2) }}%
        </span>
      </div>
      <div v-if="signalsByCode.has(s.code)" class="stock-signals">
        <span v-for="sig in signalsByCode.get(s.code)" :key="sig.signal_name + sig.triggered_at" :class="['signal-tag', sig.direction === 'sell' || sig.direction === 'reduce' ? 'tag-sell' : 'tag-buy']">{{ sig.signal_name }}</span>
      </div>
      <div v-if="s.pct_5d != null || s.turnover != null || s.volume_ratio != null" class="stock-metrics">
        <span v-if="s.pct_5d != null">5日<b :class="s.pct_5d >= 0 ? 'up' : 'down'">{{ s.pct_5d >= 0 ? '+' : '' }}{{ s.pct_5d.toFixed(2) }}%</b></span>
        <span v-if="s.turnover != null">换手 <b>{{ s.turnover.toFixed(2) }}%</b></span>
        <span v-if="s.volume_ratio != null">量比 <b>{{ s.volume_ratio.toFixed(2) }}</b></span>
      </div>
      <div v-if="s.industry || s.amount" class="stock-extra">
        <span v-if="s.industry" class="stock-industry">{{ s.industry }}</span>
        <span v-if="s.sector_rank === 1" class="sector-leader-badge">板块最强</span>
        <!-- v1.7.758: 成交额>50亿 紫色突出, 与PC表格同口径 -->
        <span v-if="s.amount" class="stock-amount" :class="{ 'amount-huge': s.amount > 5e9 }">{{ formatAmount(s.amount) }}</span>
      </div>
      <div v-if="s.grp || s.tags || s.note" class="stock-meta-row">
        <span v-if="s.grp" class="grp-tag">{{ s.grp }}</span>
        <span v-for="t in (s.tags || '').split(',').filter(Boolean)" :key="t" class="m-tag">{{ t }}</span>
        <span v-if="s.note" class="m-note">{{ s.note }}</span>
      </div>
      <!-- 操作策略 (v1.7.721): 有策略才占位, 整块可点进编辑(手机上比小按钮好按) -->
      <div v-if="s.strategy && s.strategy.trim()" class="stock-strategy" role="button" tabindex="0"
        :aria-label="`编辑 ${s.name} 的操作策略`"
        @click="openStrategy(s)" @keydown.enter="openStrategy(s)">
        <span class="strat-label">策略</span>
        <StrategyText :text="s.strategy" />
      </div>
      <div class="stock-bottom">
        <NTag size="tiny" :type="s.status === 'hold' ? 'success' : 'default'" :bordered="false">
          {{ s.status === 'hold' ? '持仓' : '观察' }}
        </NTag>
        <div style="display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end">
          <NButton size="small" :type="(s.substance_score ?? 0) >= 4 ? 'success' : ((s.substance_score ?? 0) >= 1 ? 'warning' : 'primary')" secondary @click="openSubstance(s)">
            <template #icon><NIcon><SparklesOutline /></NIcon></template>
            <span v-if="s.substance_score && s.substance_score > 0">{{ '⭐'.repeat(s.substance_score) }}</span>
            <span v-else>AI 核查</span>
          </NButton>
          <NButton size="small" :type="summaryFor(s.code) ? (summaryFor(s.code)!.triggered > 0 ? 'warning' : 'primary') : 'default'" secondary @click="openAlert(s)">
            <template #icon><NIcon><component :is="summaryFor(s.code) ? Notifications : NotificationsOutline" /></NIcon></template>
            预警<span v-if="summaryFor(s.code)">{{ summaryFor(s.code)!.triggered > 0 ? '!' : summaryFor(s.code)!.active }}</span>
          </NButton>
          <NButton size="small" :type="(s.strategy && s.strategy.trim()) ? 'primary' : 'default'" secondary @click="openStrategy(s)">
            <template #icon><NIcon><component :is="(s.strategy && s.strategy.trim()) ? Create : CreateOutline" /></NIcon></template>
            策略
          </NButton>
          <NButton size="small" :type="(s.grp || s.tags || s.note) ? 'primary' : 'default'" secondary @click="openMeta(s)">
            <template #icon><NIcon><PricetagsOutline /></NIcon></template>
            标签
          </NButton>
          <NButton size="small" secondary @click="openWencai(s)">
            <template #icon><NIcon><HelpCircleOutline /></NIcon></template>
            问财
          </NButton>
          <NButton size="small" secondary @click="openGuba(s.code)">
            <template #icon><NIcon><ChatboxEllipsesOutline /></NIcon></template>
            股吧
          </NButton>
          <NButton size="small" secondary @click="openThsPage(s.code)">
            <template #icon><NIcon><OpenOutline /></NIcon></template>
            同花顺
          </NButton>
          <!-- 在途时禁点(仅本卡片): 防连点乱序返回导致最终关注状态与用户所点相反 -->
          <NButton size="small" :type="s.focused ? 'warning' : 'primary'" :secondary="!s.focused"
            :loading="focusBusy(s.code)" :disabled="focusBusy(s.code)" @click="handleToggleFocus(s)">
            <template #icon><NIcon><component :is="s.focused ? Star : StarOutline" /></NIcon></template>
            {{ s.focused ? '已关注' : '关注' }}
          </NButton>
          <NPopconfirm @positive-click="handleDelete(s.code, s.name)" positive-text="删除" negative-text="取消">
            <template #trigger>
              <NButton size="small" type="error" secondary>
                <template #icon><NIcon><TrashOutline /></NIcon></template>
                删除
              </NButton>
            </template>
            确认从股票池删除 {{ s.name }}（{{ s.code }}）?
          </NPopconfirm>
        </div>
      </div>
    </div>
    <div v-if="stocks.length === 0" class="empty">暂无股票</div>

    <!-- AI 真受益核查抽屉 -->
    <SubstanceCheckDrawer
      v-if="substanceStock"
      v-model:show="substanceShow"
      :code="substanceStock.code"
      :name="substanceStock.name"
      :industry="substanceStock.industry || ''"
      :strategy="substanceStock.strategy || ''"
    />
    <StockAlertModal
      v-model:show="showAlertModal"
      :code="alertStock?.code || ''"
      :name="alertStock?.name || ''"
      @changed="reloadAlerts"
    />
    <StrategyEditModal
      v-model:show="showStrategyModal"
      :code="strategyStock?.code || ''"
      :name="strategyStock?.name || ''"
      :text="strategyStock?.strategy || ''"
      @saved="onStrategySaved"
    />
    <StockMetaModal
      v-model:show="showMetaModal"
      :code="metaStock?.code || ''"
      :name="metaStock?.name || ''"
      :grp="metaStock?.grp || ''"
      :tags="metaStock?.tags || ''"
      :note="metaStock?.note || ''"
      :group-options="groupOptions"
      @changed="stockStore.loadStocks(true)"
    />
    <StockWencaiModal v-model:show="showWencaiModal" :row="wencaiStock" />
  </div>
</template>

<style scoped>
.stock-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.stock-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  touch-action: manipulation;
}
.stock-card.focused {
  font-weight: 700;
}
.stock-card.signaled {
  background: var(--warn-bg-muted);
  border-color: color-mix(in srgb, var(--warn-fg) 40%, transparent);
  border-left: 4px solid var(--warn-fg);
}
.stock-card.hold {
  background: var(--accent-bg-muted);
  border-color: color-mix(in srgb, var(--accent-fg) 20%, transparent);
}
.stock-card.hold.signaled {
  background: var(--accent-bg-muted);
  border-left: 4px solid var(--warn-fg);
}
.stock-top-left {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.signal-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  color: var(--on-emphasis);
  font-size: 10px;
  font-weight: 700;
  margin-left: 4px;
  line-height: 1;
  vertical-align: middle;
  flex: 0 0 auto;
}
.signal-badge-buy   { background: var(--up-fg); }
.signal-badge-sell  { background: var(--down-fg); }
.signal-badge-other { background: var(--fg-subtle); }
.stock-signals {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}
.signal-tag {
  color: var(--on-emphasis);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
}
.signal-tag.tag-buy {
  background: var(--up-bg-muted); color: var(--up-fg); border: 1px solid color-mix(in srgb, var(--up-fg) 40%, transparent);
}
.signal-tag.tag-sell {
  background: var(--down-bg-muted); color: var(--down-fg); border: 1px solid color-mix(in srgb, var(--down-fg) 40%, transparent);
}
.stock-star {
  color: var(--red);
  font-weight: 700;
  margin-right: 2px;
}
.stock-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.stock-name {
  font-weight: 600;
  font-size: 15px;
  margin-right: 8px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stock-code {
  font-family: monospace;
  color: var(--primary);
  font-size: 13px;
  touch-action: manipulation;
}
.stock-middle {
  margin-bottom: 8px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.stock-price {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.stock-pct {
  /* 涨幅放大两号突出(14px → 18px, v1.7.792 再加一号), 与宽表同步; 涨速仍走行内 12px 覆盖 */
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.stock-pct.up {
  color: var(--red);
}
.stock-pct.down {
  color: var(--green);
}
.stock-extra {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--text2);
}
.stock-industry {
  background: var(--bg);
  padding: 2px 6px;
  border-radius: 4px;
}
.stock-amount {
  color: var(--text2);
  font-variant-numeric: tabular-nums;
}
.stock-amount.amount-huge { color: #7c3aed; font-weight: 600; }   /* >50亿 紫色, 与PC同口径 */
/* 分组/标签/备注 (v1.7.670) */
.stock-meta-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.stock-meta-row .grp-tag { font-size: 11px; font-weight: 600; color: var(--accent-fg); background: var(--accent-bg-muted); border-radius: 4px; padding: 1px 8px; }
.stock-meta-row .m-tag { font-size: 11px; color: var(--tide-deep); background: var(--tide-bg-muted); border-radius: 4px; padding: 1px 7px; }
.stock-meta-row .m-note { font-size: 11px; color: var(--fg-subtle); }
.sector-leader-badge {
  padding: 0 4px;
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  border-radius: 3px;
  background: linear-gradient(135deg, #ff6b00, #ff3b00);
  color: var(--on-emphasis);
}
/* 名称行迷你徽章: 涨停/跌停/连板/首板 (A股 红涨绿跌) */
.mini-badge {
  margin-left: 4px;
  padding: 0 4px;
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  border-radius: 2px;
  flex: 0 0 auto;
}
.badge-limit-up {
  background: var(--up-bg-muted);
  color: var(--up-fg);
  border: 1px solid color-mix(in srgb, var(--up-fg) 40%, transparent);
}
.badge-limit-down {
  background: var(--down-bg-muted);
  color: var(--down-fg);
  border: 1px solid color-mix(in srgb, var(--down-fg) 40%, transparent);
}
.badge-lianban {
  background: var(--warn-bg-muted);
  color: var(--warn-fg);
  border: 1px solid color-mix(in srgb, var(--warn-fg) 40%, transparent);
}
.badge-shouban {
  background: var(--up-bg-muted);
  color: var(--up-fg);
  border: 1px solid color-mix(in srgb, var(--up-fg) 30%, transparent);
}
/* 关键指标行: 5日涨幅 / 换手 / 量比 */
.stock-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--text2);
}
.stock-metrics b {
  font-variant-numeric: tabular-nums;
  color: var(--text1);
}
.stock-metrics b.up {
  color: var(--red);
}
.stock-metrics b.down {
  color: var(--green);
}
/* 操作策略块 (v1.7.721 手机端): 紫色左边线沿用宽表策略列的紫色主题; 正文保留换行, 最多 4 行后截断 */
.stock-strategy {
  margin-bottom: 8px;
  padding: 6px 8px;
  border-left: 3px solid #7c3aed;
  border-radius: 0 4px 4px 0;
  background: color-mix(in srgb, #7c3aed 7%, transparent);
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
  touch-action: manipulation;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.stock-strategy .strat-label {
  display: inline-block;
  margin-right: 6px;
  padding: 0 5px;
  border: 1px solid color-mix(in srgb, #7c3aed 40%, transparent);
  border-radius: 3px;
  color: #7c3aed;
  font-size: 10px;
  font-weight: 700;
  line-height: 15px;
  vertical-align: 1px;
}
.stock-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.empty {
  text-align: center;
  padding: 40px;
  color: var(--text2);
}
</style>
