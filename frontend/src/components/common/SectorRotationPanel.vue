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

const items = computed<SectorRotationItem[]>(() => data.value?.items ?? [])
// 走强区: 启动/升温/高潮; 退潮区: 退潮; 冷区: 持平(有涨停但未超昨日) + 冷(涨停≤1家)
const strong = computed(() => items.value.filter(i => ['启动', '升温', '高潮'].includes(i.state)))
const ebb = computed(() => items.value.filter(i => i.state === '退潮'))
const cold = computed(() => items.value.filter(i => ['持平', '冷'].includes(i.state)))

// ── ① 今日转换流水 · 横向时间轴 (v1.7.732) ──
// 原为时间倒序竖排 feed。改横轴后 x=真实时间比例, 转强在轴上方(红)/转弱在轴下方(绿),
// 一眼能看出"轮动扎堆在哪个时段"——这本身是有效信息, 竖排看不出来。
const transitions = computed<SectorTransition[]>(() => data.value?.transitions ?? [])
const w2sCount = computed(() => transitions.value.filter(t => t.direction === 'weak_to_strong').length)
const s2wCount = computed(() => transitions.value.filter(t => t.direction === 'strong_to_weak').length)
const isW2S = (t: SectorTransition) => t.direction === 'weak_to_strong'

const LANE_H = 36        // 错行高度: 标签两行(题材名 + 家数/时间)
const MIN_GAP_PCT = 13   // 同行两标签最小间距, 按轨道最窄 680px 估 ~88px 标签宽
const MAX_LANE = 5       // 错行上限, 超了并回最后一行(极端密集日才会到)

const TL_OPEN = 9 * 60 + 30
const TL_LUNCH = 11 * 60 + 30
const TL_PM = 13 * 60
const TL_SPAN = 240      // 上午120分 + 下午120分; 午休不占宽度(11:30 与 13:00 同一刻度)

// HH:MM → 轴上分钟偏移。午休整段压成一个点, 否则 11:30-13:00 会白占 1/3 轴宽。
function minuteOf(at: string): number {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(at || ''))
  if (!m) return 0
  const t = +m[1] * 60 + +m[2]
  if (t <= TL_OPEN) return 0
  if (t <= TL_LUNCH) return t - TL_OPEN
  if (t <= TL_PM) return 120
  return Math.min(TL_SPAN, 120 + (t - TL_PM))
}

interface TlEvent {
  key: string; pct: number; lane: number; at: string; theme: string
  yest: number; limit_up: number; broken: number; samples: string[]; up: boolean
}

// 贪心分行: 同一行内与前一个标签间距不够就换下一行, 保证标签不重叠
function assignLanes(list: TlEvent[]): TlEvent[] {
  const lastPct: number[] = []
  for (const e of list) {
    let lane = 0
    while (lane < MAX_LANE && lastPct[lane] != null && e.pct - lastPct[lane] < MIN_GAP_PCT) lane++
    lastPct[lane] = e.pct
    e.lane = lane
  }
  return list
}

function toEvents(up: boolean): TlEvent[] {
  return assignLanes(
    transitions.value
      .filter(t => isW2S(t) === up)
      .map((t, i) => ({
        key: `${up ? 'u' : 'd'}${i}`,
        pct: (minuteOf(t.at) / TL_SPAN) * 100,
        lane: 0,
        at: String(t.at || '').slice(0, 5),
        theme: t.theme,
        yest: t.yest ?? 0,
        limit_up: t.limit_up,
        broken: t.broken ?? 0,
        samples: t.samples ?? [],
        up,
      }))
      .sort((a, b) => a.pct - b.pct))
}
const upEvents = computed(() => toEvents(true))
const downEvents = computed(() => toEvents(false))
const upLanes = computed(() => Math.max(1, ...upEvents.value.map(e => e.lane + 1)))
const downLanes = computed(() => Math.max(1, ...downEvents.value.map(e => e.lane + 1)))

const TL_TICKS = [
  { pct: 0, label: '09:30' },
  { pct: 25, label: '10:30' },
  { pct: 50, label: '11:30/13:00' },
  { pct: 75, label: '14:00' },
  { pct: 100, label: '15:00' },
]

// 明细条: 悬停临时看, 点击钉住(手机端没有 hover, 靠点)。放轴下方固定一条而非浮层——
// 轨道是横向滚动容器, 浮层会被裁掉。
const hoverKey = ref<string | null>(null)
const pinnedKey = ref<string | null>(null)
const activeKey = computed(() => pinnedKey.value ?? hoverKey.value)
const activeEvent = computed<TlEvent | null>(() =>
  [...upEvents.value, ...downEvents.value].find(e => e.key === activeKey.value) ?? null)
function toggleEvent(k: string) {
  pinnedKey.value = pinnedKey.value === k ? null : k
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
        <div class="block-title">今日转换流水<span class="bt-meta">按日比昨(昨→今) · 横向时间轴, 轴上=转强 / 轴下=转弱</span></div>
        <div v-if="transitions.length" class="tl">
          <div class="tl-scroll">
            <div class="tl-track">
              <!-- 轴上方: 转强(红), 按错行避让 -->
              <div class="tl-side up" :style="{ height: upLanes * LANE_H + 'px' }">
                <button v-for="e in upEvents" :key="e.key" type="button" class="tl-ev up"
                        :class="{ active: activeKey === e.key }"
                        :style="{ left: e.pct + '%', bottom: e.lane * LANE_H + 'px' }"
                        :title="`${e.at} ↑转强 ${e.theme} 昨${e.yest}→今${e.limit_up}家`"
                        @mouseenter="hoverKey = e.key" @mouseleave="hoverKey = null"
                        @focus="hoverKey = e.key" @blur="hoverKey = null"
                        @click="toggleEvent(e.key)">
                  <span class="ev-name">{{ e.theme }}</span>
                  <span class="ev-sub">{{ e.yest }}→{{ e.limit_up }}家 · {{ e.at }}</span>
                  <i class="ev-stem" :style="{ height: e.lane * LANE_H + 6 + 'px' }"></i>
                </button>
              </div>

              <!-- 时间轴本体: 刻度 + 事件圆点 -->
              <div class="tl-axis">
                <div class="tl-line"></div>
                <i v-for="e in upEvents" :key="'du' + e.key" class="tl-dot up"
                   :class="{ active: activeKey === e.key }" :style="{ left: e.pct + '%' }"></i>
                <i v-for="e in downEvents" :key="'dd' + e.key" class="tl-dot down"
                   :class="{ active: activeKey === e.key }" :style="{ left: e.pct + '%' }"></i>
                <span v-for="tk in TL_TICKS" :key="tk.label" class="tl-tick" :style="{ left: tk.pct + '%' }">
                  <i class="tk-mark"></i><em>{{ tk.label }}</em>
                </span>
              </div>

              <!-- 轴下方: 转弱(绿) -->
              <div class="tl-side down" :style="{ height: downLanes * LANE_H + 'px' }">
                <button v-for="e in downEvents" :key="e.key" type="button" class="tl-ev down"
                        :class="{ active: activeKey === e.key }"
                        :style="{ left: e.pct + '%', top: e.lane * LANE_H + 'px' }"
                        :title="`${e.at} ↓转弱 ${e.theme} 昨${e.yest}→今${e.limit_up}家`"
                        @mouseenter="hoverKey = e.key" @mouseleave="hoverKey = null"
                        @focus="hoverKey = e.key" @blur="hoverKey = null"
                        @click="toggleEvent(e.key)">
                  <i class="ev-stem" :style="{ height: e.lane * LANE_H + 21 + 'px' }"></i>
                  <span class="ev-name">{{ e.theme }}</span>
                  <span class="ev-sub">{{ e.yest }}→{{ e.limit_up }}家<template v-if="e.broken > 0"> · 炸{{ e.broken }}</template> · {{ e.at }}</span>
                </button>
              </div>
            </div>
          </div>

          <!-- 明细条: 悬停/点选某个时间点后显示成分个股 -->
          <div class="tl-detail" :class="activeEvent ? (activeEvent.up ? 'w2s' : 's2w') : ''">
            <template v-if="activeEvent">
              <span class="td-time">{{ activeEvent.at }}</span>
              <span class="td-dir">{{ activeEvent.up ? '↑转强' : '↓转弱' }}</span>
              <span class="td-theme">{{ activeEvent.theme }}</span>
              <span class="td-stat">昨{{ activeEvent.yest }}→今{{ activeEvent.limit_up }}家<template v-if="!activeEvent.up && activeEvent.broken > 0"> · 炸{{ activeEvent.broken }}</template></span>
              <span v-if="activeEvent.samples.length" class="td-samples">{{ activeEvent.samples.slice(0, 6).join(' · ') }}</span>
            </template>
            <span v-else class="td-hint">点(或悬停)轴上题材，看成分个股明细；再点一次取消钉住。</span>
          </div>
        </div>
        <div v-else class="flow-empty">今日暂无明显弱强转换（题材状态尚未发生启动/退潮跃迁）。</div>
      </div>

      <!-- ── ② 当前强弱格局 (压缩) ── -->
      <div v-if="items.length" class="block">
        <div class="block-title">当前强弱格局
          <span class="bt-meta">走强{{ strong.length }} · 退潮{{ ebb.length }} · 持平/冷{{ cold.length }}</span>
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
          <span class="cold-toggle" role="button" tabindex="0" @click="showCold = !showCold" @keydown.enter="showCold = !showCold">持平/冷区 {{ cold.length }} 个 {{ showCold ? '↑' : '↓' }}</span>
          <div v-if="showCold" class="cold-list">
            <span v-for="it in cold" :key="it.theme" class="cold-chip" :title="`${it.theme} · ${it.state} · 涨停${it.limit_up}家`">{{ it.theme }}<span class="cc-lu">{{ it.limit_up }}</span></span>
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
/* 配色沿用全站纪律(同 ThemeHeatPanel/EmotionPanel): 转强=涨=红(--up-fg) / 转弱=退潮=绿(--down-fg) / 中性灰(--fg-*)。
   无彩虹 emoji, 方向只用 ↑↓ 箭头 + 红绿; 选中/强调单一蓝 --accent-fg。 */
.rotation-panel { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: 6px; padding: 10px 12px; }
/* 机构模块头 (v1.7.650): 发丝底线 + 加粗收紧标题 + mono 计数 */
.head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border-muted); }
.title { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 700; letter-spacing: 0.02em; color: var(--fg-default); }
.title .meta { font-family: var(--font-mono); font-size: 10.5px; font-weight: 500; color: var(--fg-subtle); margin-left: 4px; letter-spacing: 0.02em; }
.title .meta i { font-style: normal; font-variant-numeric: tabular-nums; margin: 0 1px; }
.up { color: var(--up-fg); }
.down { color: var(--down-fg); }

.empty { margin-top: 16px; text-align: center; color: var(--fg-subtle); font-size: 13px; padding: 16px; line-height: 1.7; }

.block { margin-top: 10px; }
.block-title { font-size: 12px; font-weight: 700; color: var(--fg-muted); margin-bottom: 6px; display: flex; align-items: baseline; gap: 8px; }
.block-title .bt-meta { font-size: 10.5px; font-weight: 400; color: var(--fg-subtle); }

/* ── ① 今日转换流水 · 横向时间轴 ── */
/* 轨道最窄 800px(左右各留 60px 给首尾标签, 内容区 680px), 窄屏横向滚动看全天。
   内容区 680px 正是 MIN_GAP_PCT 的估算基准 —— 改这里要同步改脚本里那个常量。 */
.tl-scroll { overflow-x: auto; overflow-y: hidden; -webkit-overflow-scrolling: touch; padding-bottom: 2px; }
.tl-track { position: relative; box-sizing: border-box; min-width: 800px; padding: 4px 60px 0; }
.tl-side { position: relative; }
.tl-ev {
  position: absolute; transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center; gap: 1px;
  appearance: none; border: 1px solid transparent; background: transparent;
  font: inherit; padding: 2px 5px; border-radius: 6px; cursor: pointer;
  touch-action: manipulation; line-height: 1.25; max-width: 104px;
}
.tl-ev .ev-name { font-size: 12.5px; font-weight: 600; color: var(--fg-default); max-width: 92px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-ev .ev-sub { font-size: 10.5px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; white-space: nowrap; }
.tl-ev.up .ev-sub { color: color-mix(in srgb, var(--up-fg) 78%, var(--fg-subtle)); }
.tl-ev.down .ev-sub { color: color-mix(in srgb, var(--down-fg) 78%, var(--fg-subtle)); }
/* 引线: 从标签连到轴, 错行后仍能对上是哪个时间点 */
.tl-ev .ev-stem { position: absolute; left: 50%; width: 1px; }
.tl-ev.up .ev-stem { top: 100%; background: color-mix(in srgb, var(--up-fg) 35%, transparent); }
.tl-ev.down .ev-stem { bottom: 100%; background: color-mix(in srgb, var(--down-fg) 35%, transparent); }
.tl-ev:hover, .tl-ev:focus-visible { background: var(--bg-sunken); border-color: var(--border-muted); outline: none; }
.tl-ev.up.active { background: var(--up-bg-muted); border-color: color-mix(in srgb, var(--up-fg) 40%, transparent); }
.tl-ev.down.active { background: var(--down-bg-muted); border-color: color-mix(in srgb, var(--down-fg) 40%, transparent); }

.tl-axis { position: relative; height: 26px; }
.tl-line { position: absolute; left: 0; right: 0; top: 5px; height: 1px; background: var(--border-default); }
.tl-dot { position: absolute; top: 2px; width: 7px; height: 7px; border-radius: 50%; transform: translateX(-50%); border: 1px solid var(--bg-surface); }
.tl-dot.up { background: var(--up-fg); }
.tl-dot.down { background: var(--down-fg); }
.tl-dot.active { transform: translateX(-50%) scale(1.5); }
.tl-tick { position: absolute; top: 0; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; }
.tl-tick .tk-mark { width: 1px; height: 11px; background: var(--border-default); }
.tl-tick em { font-style: normal; font-size: 10px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; white-space: nowrap; margin-top: 1px; }

.tl-detail {
  margin-top: 8px; display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px;
  font-size: 12px; padding: 5px 9px; border-radius: 6px; min-height: 27px;
  background: var(--bg-sunken); border-left: 3px solid transparent;
}
.tl-detail.w2s { background: var(--up-bg-muted); border-left-color: var(--up-fg); }
.tl-detail.s2w { background: var(--down-bg-muted); border-left-color: var(--down-fg); }
.td-time { font-size: 11px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; }
.td-dir { font-size: 11px; font-weight: 700; }
.tl-detail.w2s .td-dir { color: var(--up-fg); }
.tl-detail.s2w .td-dir { color: var(--down-fg); }
.td-theme { font-size: 12.5px; font-weight: 600; color: var(--fg-default); }
.td-stat { font-size: 11.5px; color: var(--fg-muted); font-variant-numeric: tabular-nums; }
.td-samples { font-size: 11px; color: var(--fg-subtle); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-hint { font-size: 11px; color: var(--fg-subtle); }
.flow-empty { font-size: 12px; color: var(--fg-subtle); line-height: 1.6; background: var(--bg-sunken); padding: 8px 10px; border-radius: 6px; }

/* ── ② 当前强弱格局: 红绿点流式 chips ── */
.dots { display: flex; flex-wrap: wrap; gap: 5px; }
.dot-chip { display: inline-flex; align-items: baseline; gap: 4px; padding: 2px 8px; border-radius: 11px; font-size: 11.5px; color: var(--fg-default); border: 1px solid var(--border-muted); white-space: nowrap; cursor: default; }
.dot-chip .dot { align-self: center; width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dot-chip.strong { background: var(--up-bg-muted); border-color: color-mix(in srgb, var(--up-fg) 20%, transparent); }
.dot-chip.strong .dot { background: var(--up-fg); }
.dot-chip.ebb { background: var(--down-bg-muted); border-color: color-mix(in srgb, var(--down-fg) 22%, transparent); }
.dot-chip.ebb .dot { background: var(--down-fg); }
.dc-lu { font-weight: 700; font-variant-numeric: tabular-nums; color: var(--fg-muted); }
.dot-chip.strong .dc-lu { color: var(--up-fg); }
.dot-chip.ebb .dc-lu { color: var(--down-fg); }

/* 冷区 */
.cold-zone { margin-top: 6px; }
.cold-toggle { font-size: 11px; font-weight: 600; color: var(--fg-subtle); cursor: pointer; touch-action: manipulation; user-select: none; }
.cold-toggle:hover { color: var(--fg-muted); }
.cold-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 4px; }
.cold-chip { font-size: 11px; color: var(--fg-subtle); background: var(--bg-sunken); padding: 1px 8px; border-radius: 9px; }
.cold-chip .cc-lu { margin-left: 4px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; }

/* ── ③ 次日预测 ── */
.predict-block { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--border-muted); }
.predict-head { cursor: pointer; touch-action: manipulation; user-select: none; align-items: center; }
.predict-head .pd-toggle { margin-left: auto; font-size: 11px; font-weight: 600; color: var(--accent-fg); }
.disclaimer { margin: 0 0 8px; font-size: 11px; color: var(--warn-fg); background: var(--warn-bg-muted); padding: 4px 8px; border-radius: 5px; line-height: 1.5; }
.predict-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.predict-grid.phone { grid-template-columns: 1fr; }
.pg { border: 1px solid var(--border-muted); border-radius: 7px; padding: 6px 9px; }
.pg-head { font-size: 11.5px; font-weight: 700; margin-bottom: 4px; display: flex; align-items: baseline; gap: 5px; }
.pg-head .pg-cnt { font-size: 10px; font-weight: 400; color: var(--fg-subtle); }
.pg.p-w2s .pg-head { color: var(--up-fg); }   /* 弱转强=机会, 但 A股红=涨, 故转强统一红 */
.pg.p-s2w .pg-head { color: var(--down-fg); }  /* 强转弱=退潮, 绿 */
.pg.p-cont .pg-head { color: var(--up-fg); }   /* 强势延续=强, 红 */
.pg.p-end .pg-head { color: var(--fg-muted); }     /* 疑似终结=沉寂, 中性灰 */
.pg-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; padding: 2px 0; font-size: 12px; line-height: 1.5; }
.pg-theme { font-weight: 600; color: var(--fg-default); flex-shrink: 0; }
.pg-traj { font-size: 11px; color: var(--fg-muted); font-variant-numeric: tabular-nums; background: var(--bg-sunken); padding: 0 6px; border-radius: 8px; white-space: nowrap; }
.pg-reason { font-size: 11px; color: var(--fg-subtle); flex: 1; min-width: 0; }

.foot-hint { margin-top: 10px; font-size: 11px; color: var(--fg-subtle); line-height: 1.6; background: var(--bg-sunken); padding: 7px 10px; border-radius: 6px; }

@media (max-width: 768px) {
  .rotation-panel { padding: 8px 10px; }
  .title { font-size: 13px; }
  /* 手机端时间轴同样横向滚动(左右滑看全天), 明细条里的个股换行显示不截断 */
  .tl-track { padding: 4px 52px 0; }
  .td-samples { flex-basis: 100%; white-space: normal; }
  .predict-grid { grid-template-columns: 1fr; }
  .pg-reason { flex-basis: 100%; }
}
</style>
