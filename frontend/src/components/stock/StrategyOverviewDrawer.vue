<script setup lang="ts">
// 功能B: 策略总览抽屉 — 把所有有策略的票列成卡片, 一屏批量过计划。
// 排序: 持仓 → 关注 → 其他, 组内按代码。火苗复用 resonanceLevel。
import { computed, ref } from 'vue'
import { NDrawer, NDrawerContent, NButton, NIcon, NTag, NEmpty } from 'naive-ui'
import { CreateOutline } from '@vicons/ionicons5'
import type { Stock, Signal } from '../../types'
import { useUiStore } from '../../stores/ui'
import { useAmountRank } from '../../composables/useAmountRank'
import { resonanceLevel } from '../../utils/poolFormat'
import StrategyText from './StrategyText.vue'
import StrategyEditModal from './StrategyEditModal.vue'

const props = defineProps<{ show: boolean; stocks: Stock[]; signalsByCode: Map<string, Signal[]> }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()

const ui = useUiStore()
const { rankMap: amountRankMap } = useAmountRank()

const list = computed(() => {
  const arr = props.stocks.filter(s => (s.strategy || '').trim())
  const grp = (s: Stock) => (s.status === 'hold' ? 0 : s.focused ? 1 : 2)
  return [...arr].sort((a, b) => grp(a) - grp(b) || a.code.localeCompare(b.code))
})

const flameMap = computed<Record<string, { level: string; bg: string }>>(() => {
  const m: Record<string, { level: string; bg: string }> = {}
  for (const s of props.stocks) {
    const lvl = resonanceLevel(s.popularity_rank, amountRankMap.value[s.code])
    if (lvl) m[s.code] = { level: lvl, bg: lvl === '超强' ? 'var(--up-fg)' : lvl === '强' ? 'var(--warn-fg)' : 'var(--fg-subtle)' }
  }
  return m
})

function signalSummary(code: string): { buy: number; sell: number } {
  const l = props.signalsByCode.get(code)
  if (!l || !l.length) return { buy: 0, sell: 0 }
  let buy = 0, sell = 0
  for (const s of l) {
    if (s.direction === 'buy' || s.direction === 'add') buy++
    else if (s.direction === 'sell' || s.direction === 'reduce') sell++
  }
  return { buy, sell }
}

// 编辑
const showEdit = ref(false)
const editCode = ref('')
const editName = ref('')
const editText = ref('')
const editRow = ref<Stock | null>(null)
function openEdit(s: Stock) {
  editCode.value = s.code
  editName.value = s.name
  editText.value = s.strategy || ''
  editRow.value = s
  showEdit.value = true
}
function onSaved(_code: string, text: string) {
  if (editRow.value) editRow.value.strategy = text
}
</script>

<template>
  <NDrawer :show="show" @update:show="emit('update:show', $event)" :width="420" placement="right">
    <NDrawerContent :title="`我的策略 (${list.length})`" closable :native-scrollbar="false">
      <NEmpty v-if="list.length === 0" description="还没有任何个股策略" style="margin-top: 40px">
        <template #extra>去股票池，点某只票「操作」列的铅笔按钮写计划</template>
      </NEmpty>
      <div v-else class="strat-list">
        <div v-for="s in list" :key="s.code" class="strat-card">
          <div class="strat-head">
            <div class="strat-head-left">
              <span
                v-if="flameMap[s.code]"
                class="strat-flame"
                :style="{ background: flameMap[s.code].bg }"
                :title="`双榜共振${flameMap[s.code].level}`"
              >🔥</span>
              <span class="strat-name" :class="{ hold: s.status === 'hold', focused: s.focused && s.status !== 'hold' }">{{ s.name }}</span>
              <span class="strat-code" role="button" tabindex="0" title="点击查看分时 + 日K" :aria-label="`查看 ${s.name} 分时 + 日K`" @click="ui.openStock(s.code, s.name)" @keydown.enter="ui.openStock(s.code, s.name)">{{ s.code }}</span>
              <NTag size="tiny" :type="s.status === 'hold' ? 'success' : 'default'" :bordered="false">{{ s.status === 'hold' ? '持仓' : '观察' }}</NTag>
              <span v-if="signalSummary(s.code).buy" class="strat-sig buy" :title="`${signalSummary(s.code).buy} 个买入信号`">买{{ signalSummary(s.code).buy }}</span>
              <span v-if="signalSummary(s.code).sell" class="strat-sig sell" :title="`${signalSummary(s.code).sell} 个卖出/减仓信号`">卖{{ signalSummary(s.code).sell }}</span>
            </div>
            <div class="strat-head-right">
              <span v-if="s.price != null" class="strat-price">{{ s.price.toFixed(2) }}</span>
              <span v-if="s.pct_change != null" class="strat-pct" :class="s.pct_change >= 0 ? 'up' : 'down'">{{ s.pct_change >= 0 ? '+' : '' }}{{ s.pct_change.toFixed(2) }}%</span>
              <NButton size="tiny" quaternary title="编辑策略" aria-label="编辑策略" @click="openEdit(s)">
                <template #icon><NIcon><CreateOutline /></NIcon></template>
              </NButton>
            </div>
          </div>
          <StrategyText :text="s.strategy || ''" class="strat-body" />
        </div>
      </div>
    </NDrawerContent>
  </NDrawer>
  <StrategyEditModal v-model:show="showEdit" :code="editCode" :name="editName" :text="editText" @saved="onSaved" />
</template>

<style scoped>
.strat-list { display: flex; flex-direction: column; gap: 10px; }
.strat-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--surface);
}
.strat-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.strat-head-left { display: flex; align-items: center; gap: 6px; min-width: 0; flex-wrap: wrap; }
.strat-flame {
  font-size: 10px;
  padding: 0 3px;
  border-radius: 3px;
  line-height: 16px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.18);
}
.strat-name { font-weight: 600; font-size: 14px; min-width: 0; }
.strat-name.hold { color: var(--accent-fg); }
.strat-name.focused { color: var(--up-fg); }
.strat-code {
  font-family: monospace;
  font-size: 12px;
  color: var(--accent-fg);
  cursor: pointer;
  text-decoration: underline dotted;
  touch-action: manipulation;
}
.strat-sig {
  font-size: 10px;
  padding: 0 4px;
  border-radius: 3px;
  color: var(--on-emphasis);
  line-height: 16px;
}
.strat-sig.buy { background: var(--up-fg); }
.strat-sig.sell { background: var(--down-fg); }
.strat-head-right { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
.strat-price { font-weight: 700; font-size: 14px; font-variant-numeric: tabular-nums; }
.strat-pct { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.strat-pct.up { color: var(--red); }
.strat-pct.down { color: var(--green); }
.strat-body { display: block; margin-top: 2px; }
</style>
