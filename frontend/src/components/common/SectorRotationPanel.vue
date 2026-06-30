<script setup lang="ts">
// 板块轮动·弱强转换 — 嵌入监控看板 (v1.7.461 重构)
// 重构信息架构, 让"转换"成为头条:
//   ① 今日转换流水: 后端检测到的状态跃迁(弱→强启动 / 强→弱退潮), 时间倒序单流——真正的"轮动"。
//   ② 当前强弱格局: 走强/退潮题材压成一行红绿点流式(不再每题材一张大卡), 冷区折叠。
//   ③ 次日预测: 启发式未回测, 默认折叠成一行概览, 展开看明细。
// 数据由后端定时算并落库, 本面板每 60s 轮询读快照。
import { ref, computed } from 'vue'
import { NSkeleton, NButton, NIcon } from 'naive-ui'
import { SwapHorizontalOutline, RefreshOutline } from '@vicons/ionicons5'
import {
  fetchSectorRotation,
  type SectorRotationData,
  type SectorRotationItem,
  type SectorTransition,
  type SectorPredictItem,
} from '../../api/sector-rotation'
import { useVisiblePolling } from '../../composables/useVisiblePolling'
import { useResponsive } from '../../composables/useResponsive'

const { isPhone } = useResponsive()

const data = ref<SectorRotationData | null>(null)
const loading = ref(false)
const loaded = ref(false)
const showCold = ref(false)        // 冷区折叠
const showPredict = ref(false)     // 次日预测折叠(默认收起)
const flowExpanded = ref(false)    // 转换流水超量展开

const items = computed<SectorRotationItem[]>(() => data.value?.items ?? [])
// 走强区: 启动/升温/高潮; 退潮区: 退潮; 冷区: 冷
const strong = computed(() => items.value.filter(i => ['启动', '升温', '高潮'].includes(i.state)))
const ebb = computed(() => items.value.filter(i => i.state === '退潮'))
const cold = computed(() => items.value.filter(i => i.state === '冷'))

// ── ① 今日转换流水 ──
const FLOW_LIMIT = 10
const transitions = computed<SectorTransition[]>(() => data.value?.transitions ?? [])
const w2sCount = computed(() => transitions.value.filter(t => t.direction === 'weak_to_strong').length)
const s2wCount = computed(() => transitions.value.filter(t => t.direction === 'strong_to_weak').length)
const flowShown = computed(() =>
  flowExpanded.value ? transitions.value : transitions.value.slice(0, FLOW_LIMIT))
const isW2S = (t: SectorTransition) => t.direction === 'weak_to_strong'
// 日基准口径: 昨X→今Y家; 转弱再带 · 炸M
function flowStat(t: SectorTransition): string {
  const yest = t.yest ?? 0
  if (isW2S(t)) return `昨${yest}→今${t.limit_up}家`
  return `昨${yest}→今${t.limit_up}家${t.broken > 0 ? ` · 炸${t.broken}` : ''}`
}
function samples3(arr: string[] | undefined): string[] {
  return (arr ?? []).slice(0, 3)
}

// ── ② 当前强弱格局 ──
// 走强题材按涨停数降序取前若干, 退潮全列; 冷区折叠
const STRONG_CHIP_MAX = 12
const strongChips = computed(() =>
  [...strong.value].sort((a, b) => b.limit_up - a.limit_up).slice(0, STRONG_CHIP_MAX))

// ── ③ 次日预测 ──
const predict = computed(() => data.value?.predict ?? null)
const hasPredict = computed(() => {
  const p = predict.value
  if (!p) return false
  return !!(p.弱转强候选?.length || p.强转弱候选?.length || p.强势延续?.length || p.疑似终结?.length)
})
const predictGroups = computed<{ key: string; title: string; cls: string; list: SectorPredictItem[] }[]>(() => {
  const p = predict.value
  if (!p) return []
  return [
    { key: '弱转强候选', title: '弱转强候选', cls: 'p-w2s', list: p.弱转强候选 ?? [] },
    { key: '强转弱候选', title: '强转弱候选', cls: 'p-s2w', list: p.强转弱候选 ?? [] },
    { key: '强势延续', title: '强势延续', cls: 'p-cont', list: p.强势延续 ?? [] },
    { key: '疑似终结', title: '疑似终结', cls: 'p-end', list: p.疑似终结 ?? [] },
  ].filter(g => g.list.length)
})
const predictOverview = computed(() =>
  predictGroups.value.map(g => `${g.title.replace('候选', '')} ${g.list.length}`).join(' · '))

function timeText(t: string | null | undefined): string {
  return t ? String(t).slice(11, 16) : ''
}

async function load() {
  loading.value = true
  try {
    data.value = await fetchSectorRotation()
  } finally {
    loading.value = false
    loaded.value = true
  }
}

useVisiblePolling(load, 60000) // 切走标签页暂停, 切回立即补刷
</script>

<template>
  <div class="rotation-panel">
    <div class="head">
      <div class="title">
        <NIcon :component="SwapHorizontalOutline" :size="16" />
        <span>板块轮动·弱强转换</span>
        <span v-if="data?.computed_at" class="meta">
          今日 <i class="up">↑转强{{ w2sCount }}</i> <i class="down">↓转弱{{ s2wCount }}</i> · 更新 {{ timeText(data.computed_at) }}
        </span>
      </div>
      <NButton quaternary circle size="tiny" :loading="loading" title="刷新" aria-label="刷新" @click="load">
        <template #icon><NIcon :component="RefreshOutline" :size="14" /></template>
      </NButton>
    </div>

    <NSkeleton v-if="!loaded" text :repeat="4" style="margin-top: 10px" />

    <div v-else-if="!items.length && !hasPredict" class="empty">
      盘中无明显轮动信号。题材轮动状态于交易日盘中定时计算(启动/升温/高潮/退潮),
      次日预测在 14:30 后生成。非交易日保留上一交易日结果。
    </div>

    <template v-else>
      <!-- ── ① 今日转换流水 (头条) ── -->
      <div class="block">
        <div class="block-title">今日转换流水<span class="bt-meta">按日比昨(昨→今) · 时间倒序</span></div>
        <div v-if="transitions.length" class="flow">
          <div v-for="(t, i) in flowShown" :key="i" class="flow-row" :class="isW2S(t) ? 'w2s' : 's2w'">
            <span class="fl-time">{{ t.at }}</span>
            <span class="fl-dir">{{ isW2S(t) ? '↑转强' : '↓转弱' }}</span>
            <span class="fl-theme">{{ t.theme }}</span>
            <span class="fl-stat">{{ flowStat(t) }}</span>
            <span v-if="samples3(t.samples).length" class="fl-samples">{{ samples3(t.samples).join('·') }}</span>
          </div>
          <div v-if="transitions.length > FLOW_LIMIT" class="flow-more" role="button" tabindex="0" @click="flowExpanded = !flowExpanded" @keydown.enter="flowExpanded = !flowExpanded">
            {{ flowExpanded ? '收起 ↑' : `展开全部 ${transitions.length} 条 ↓` }}
          </div>
        </div>
        <div v-else class="flow-empty">今日暂无明显弱强转换（题材状态尚未发生启动/退潮跃迁）。</div>
      </div>

      <!-- ── ② 当前强弱格局 (压缩) ── -->
      <div v-if="items.length" class="block">
        <div class="block-title">当前强弱格局
          <span class="bt-meta">走强{{ strong.length }} · 退潮{{ ebb.length }} · 冷{{ cold.length }}</span>
        </div>
        <div class="dots">
          <span v-for="it in strongChips" :key="it.theme" class="dot-chip strong" :title="`${it.theme} · ${it.state} · 涨停${it.limit_up}家`">
            <i class="dot"></i>{{ it.theme }}<b class="dc-lu">{{ it.limit_up }}</b>
          </span>
          <span v-for="it in ebb" :key="it.theme" class="dot-chip ebb" :title="`${it.theme} · 退潮 · 涨停${it.limit_up}家`">
            <i class="dot"></i>{{ it.theme }}<b class="dc-lu">↓{{ it.limit_up }}</b>
          </span>
        </div>
        <!-- 冷区折叠 -->
        <div v-if="cold.length" class="cold-zone">
          <span class="cold-toggle" role="button" tabindex="0" @click="showCold = !showCold" @keydown.enter="showCold = !showCold">冷区 {{ cold.length }} 个 {{ showCold ? '↑' : '↓' }}</span>
          <div v-if="showCold" class="cold-list">
            <span v-for="it in cold" :key="it.theme" class="cold-chip">{{ it.theme }}<span class="cc-lu">{{ it.limit_up }}</span></span>
          </div>
        </div>
      </div>

      <!-- ── ③ 次日预测 (默认折叠) ── -->
      <div v-if="hasPredict" class="block predict-block">
        <div class="block-title predict-head" role="button" tabindex="0" @click="showPredict = !showPredict" @keydown.enter="showPredict = !showPredict">
          <span>次日预测</span>
          <span class="bt-meta">启发式·未回测 · {{ predictOverview }}</span>
          <span class="pd-toggle">{{ showPredict ? '收起 ↑' : '展开 ↓' }}</span>
        </div>
        <template v-if="showPredict">
          <p class="disclaimer">收盘前启发式预判，未回测，仅供布局参考<span v-if="data?.predict_at"> · {{ timeText(data.predict_at) }} 生成</span></p>
          <div class="predict-grid" :class="{ phone: isPhone }">
            <div v-for="g in predictGroups" :key="g.key" class="pg" :class="g.cls">
              <div class="pg-head">{{ g.title }}<span class="pg-cnt">{{ g.list.length }}</span></div>
              <div v-for="(it, i) in g.list" :key="i" class="pg-row">
                <span class="pg-theme">{{ it.theme }}</span>
                <span class="pg-traj">{{ it.traj }}</span>
                <span class="pg-reason" :title="it.reason">{{ it.reason }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>

      <div v-if="items.length && !hasPredict" class="foot-hint">
        次日预测在交易日 14:30 后生成(弱转强/强转弱/强势延续/疑似终结)。
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 配色沿用全站纪律(同 ThemeHeatPanel/EmotionPanel): 转强红 / 转弱绿 / 中性灰, var(--x, 回退) 写法。
   无彩虹 emoji, 方向只用 ↑↓ 箭头 + 红绿; 选中/强调单一蓝 #2e9eff。 */
.rotation-panel { background: #fff; border: 1px solid var(--border, #efeff5); border-radius: 6px; padding: 10px 12px; }
.head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.title { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 600; color: var(--text1, rgba(0,0,0,0.85)); }
.title .meta { font-size: 11px; font-weight: 400; color: var(--text2, #999); margin-left: 4px; }
.title .meta i { font-style: normal; font-variant-numeric: tabular-nums; margin: 0 1px; }
.up { color: var(--red, #cf222e); }
.down { color: var(--green, #18a058); }

.empty { margin-top: 16px; text-align: center; color: var(--text2, #999); font-size: 13px; padding: 16px; line-height: 1.7; }

.block { margin-top: 10px; }
.block-title { font-size: 12px; font-weight: 700; color: var(--text2, #555); margin-bottom: 6px; display: flex; align-items: baseline; gap: 8px; }
.block-title .bt-meta { font-size: 10.5px; font-weight: 400; color: var(--text2, #aaa); }

/* ── ① 今日转换流水 ── */
.flow { display: flex; flex-direction: column; gap: 3px; }
.flow-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; padding: 4px 8px; border-radius: 6px; font-size: 12px; border-left: 3px solid transparent; }
.flow-row.w2s { background: rgba(207,34,46,0.045); border-left-color: var(--red, #cf222e); }
.flow-row.s2w { background: rgba(24,160,88,0.05); border-left-color: var(--green, #18a058); }
/* 时间+方向定宽 → 题材名各行左对齐成列, 保留 feed 感同时收齐左栏 */
.fl-time { font-size: 11px; color: var(--text2, #999); font-variant-numeric: tabular-nums; flex-shrink: 0; min-width: 30px; }
.fl-dir { font-size: 11px; font-weight: 700; flex-shrink: 0; min-width: 38px; }
.flow-row.w2s .fl-dir { color: var(--red, #cf222e); }
.flow-row.s2w .fl-dir { color: var(--green, #18a058); }
.fl-theme { font-size: 12.5px; font-weight: 600; color: var(--text1, rgba(0,0,0,0.85)); flex-shrink: 0; }
.fl-stat { font-size: 11.5px; color: var(--text2, #666); font-variant-numeric: tabular-nums; flex-shrink: 0; }
.fl-samples { font-size: 11px; color: var(--text2, #999); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.flow-more { margin-top: 4px; font-size: 11px; color: #2e9eff; cursor: pointer; touch-action: manipulation; user-select: none; padding: 2px 0 2px 8px; }
.flow-more:hover { text-decoration: underline; }
.flow-empty { font-size: 12px; color: var(--text2, #999); line-height: 1.6; background: rgba(0,0,0,0.02); padding: 8px 10px; border-radius: 6px; }

/* ── ② 当前强弱格局: 红绿点流式 chips ── */
.dots { display: flex; flex-wrap: wrap; gap: 5px; }
.dot-chip { display: inline-flex; align-items: baseline; gap: 4px; padding: 2px 8px; border-radius: 11px; font-size: 11.5px; color: var(--text1, rgba(0,0,0,0.82)); border: 1px solid var(--border, #efeff5); white-space: nowrap; cursor: default; }
.dot-chip .dot { align-self: center; width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dot-chip.strong { background: rgba(207,34,46,0.05); border-color: rgba(207,34,46,0.2); }
.dot-chip.strong .dot { background: var(--red, #cf222e); }
.dot-chip.ebb { background: rgba(24,160,88,0.05); border-color: rgba(24,160,88,0.22); }
.dot-chip.ebb .dot { background: var(--green, #18a058); }
.dc-lu { font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text2, #777); }
.dot-chip.strong .dc-lu { color: var(--red, #cf222e); }
.dot-chip.ebb .dc-lu { color: var(--green, #18a058); }

/* 冷区 */
.cold-zone { margin-top: 6px; }
.cold-toggle { font-size: 11px; font-weight: 600; color: var(--text2, #999); cursor: pointer; touch-action: manipulation; user-select: none; }
.cold-toggle:hover { color: var(--text2, #555); }
.cold-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 4px; }
.cold-chip { font-size: 11px; color: var(--text2, #999); background: rgba(0,0,0,0.03); padding: 1px 8px; border-radius: 9px; }
.cold-chip .cc-lu { margin-left: 4px; color: var(--text2, #bbb); font-variant-numeric: tabular-nums; }

/* ── ③ 次日预测 ── */
.predict-block { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--border, #eee); }
.predict-head { cursor: pointer; touch-action: manipulation; user-select: none; align-items: center; }
.predict-head .pd-toggle { margin-left: auto; font-size: 11px; font-weight: 600; color: #2e9eff; }
.disclaimer { margin: 0 0 8px; font-size: 11px; color: var(--text2, #b45309); background: rgba(240,160,32,0.08); padding: 4px 8px; border-radius: 5px; line-height: 1.5; }
.predict-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.predict-grid.phone { grid-template-columns: 1fr; }
.pg { border: 1px solid var(--border, #efeff5); border-radius: 7px; padding: 6px 9px; }
.pg-head { font-size: 11.5px; font-weight: 700; margin-bottom: 4px; display: flex; align-items: baseline; gap: 5px; }
.pg-head .pg-cnt { font-size: 10px; font-weight: 400; color: var(--text2, #aaa); }
.pg.p-w2s .pg-head { color: var(--red, #cf222e); }   /* 弱转强=机会, 但 A股红=涨, 故转强统一红 */
.pg.p-s2w .pg-head { color: var(--green, #18a058); }  /* 强转弱=退潮, 绿 */
.pg.p-cont .pg-head { color: var(--red, #cf222e); }   /* 强势延续=强, 红 */
.pg.p-end .pg-head { color: var(--text2, #888); }     /* 疑似终结=沉寂, 中性灰 */
.pg-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; padding: 2px 0; font-size: 12px; line-height: 1.5; }
.pg-theme { font-weight: 600; color: var(--text1, rgba(0,0,0,0.85)); flex-shrink: 0; }
.pg-traj { font-size: 11px; color: var(--text2, #666); font-variant-numeric: tabular-nums; background: rgba(0,0,0,0.04); padding: 0 6px; border-radius: 8px; white-space: nowrap; }
.pg-reason { font-size: 11px; color: var(--text2, #999); flex: 1; min-width: 0; }

.foot-hint { margin-top: 10px; font-size: 11px; color: var(--text2, #999); line-height: 1.6; background: rgba(0,0,0,0.02); padding: 7px 10px; border-radius: 6px; }

@media (max-width: 768px) {
  .rotation-panel { padding: 8px 10px; }
  .title { font-size: 13px; }
  .flow-row { gap: 6px; padding: 4px 6px; }
  .fl-samples { flex-basis: 100%; margin-left: 0; }
  .predict-grid { grid-template-columns: 1fr; }
  .pg-reason { flex-basis: 100%; }
}
</style>
