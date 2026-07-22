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

const CARD_H = 32        // 紧凑信息卡高度(v1.7.773: 时间移到轴上后卡内只剩两行数据, 压矮)
const SLOT_PX = 112      // 每张卡的水平槽宽(卡~104px + 间距), 轨道按事件数加宽横向滚动
const PAD_PX = 52        // 轨道左右留白(与 .tl-track padding 对齐), 防首尾卡被裁

// v1.7.767: 由"按真实时间定位"改为"按转换先后顺序全局均匀铺开"。
//   原来同一时刻(如13:01)多个转换全挤在同一 x, 错行避让又有上限, 溢出就重叠糊成一团。
//   现改: 所有转换按时间排序后, 沿轴等距铺开, 每个事件独占一槽 → 单行不重叠;
//   真实时间不再决定位置(位置只表先后), 时间仍在每个标签上显示(· 13:01)。
function minuteOf(at: string): number {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(at || ''))
  if (!m) return 0
  return +m[1] * 60 + +m[2]
}

interface TlEvent {
  key: string; pct: number; lane: number; at: string; theme: string
  yest: number; limit_up: number; broken: number; samples: string[]; up: boolean
  delta: number; height: number; sample: string; dotPx: number
}

// 全部转换按时间排序 → 全局等距铺开(转强/转弱共用同一条先后序列, 各自在轴上/下渲染)
const allEvents = computed<TlEvent[]>(() => {
  const sorted = transitions.value
    .map((t, i) => ({ t, m: minuteOf(t.at), i }))
    .sort((a, b) => a.m - b.m || a.i - b.i)
  const n = sorted.length
  return sorted.map((x, idx) => {
    const up = isW2S(x.t)
    const yest = x.t.yest ?? 0
    const delta = x.t.limit_up - yest
    const samples = x.t.samples ?? []
    return {
      key: `${up ? 'u' : 'd'}${x.i}`,
      pct: n <= 1 ? 50 : (idx / (n - 1)) * 100,
      lane: 0,
      at: String(x.t.at || '').slice(0, 5),
      theme: x.t.theme,
      yest,
      limit_up: x.t.limit_up,
      broken: x.t.broken ?? 0,
      samples,
      up,
      delta,                                        // 净变化(转强+ / 转弱-), 卡片直观显示力度
      height: x.t.max_height ?? 0,                  // 该题材最高连板高度(打板视角关键)
      sample: samples[0] || '',                     // 代表个股(第一只), 卡片内一眼认题材
      dotPx: 7 + Math.min(Math.abs(delta), 9),      // 轴上圆点大小随变化幅度(7~16px)
    }
  })
})
const upEvents = computed(() => allEvents.value.filter(e => e.up))
const downEvents = computed(() => allEvents.value.filter(e => !e.up))
// 轨道宽度随事件数增长(等距铺开需足够物理宽度, 不够就横向滚动)
const trackMinWidth = computed(() =>
  Math.max(800, PAD_PX * 2 + Math.max(0, allEvents.value.length - 1) * SLOT_PX))

// v1.7.773: 时间刻度轴 —— 同一时刻(如 13:01 六个转换)只标一次时间, 居中吸附在该簇正下方,
// 让"时间点"成为轴上的重点(去重防同刻时间重复多次糊成一片); 单事件时刻则与其圆点对齐。
const timeGroups = computed<{ at: string; pct: number }[]>(() => {
  const m = new Map<string, number[]>()
  for (const e of allEvents.value) {
    const arr = m.get(e.at) ?? []
    arr.push(e.pct)
    m.set(e.at, arr)
  }
  return [...m.entries()].map(([at, pcts]) => ({
    at,
    pct: pcts.reduce((s, p) => s + p, 0) / pcts.length,
  }))
})

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
        <span v-if="data?.stale" class="stale-tag" :title="`当天盘中还没算出(盘前/非交易日), 显示的是上一交易日 ${data?.trade_date} 的结果`">
          上一交易日 {{ (data?.trade_date || '').slice(5) }}
        </span>
        <span v-else-if="data?.computed_at" class="meta">
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
        <div class="block-title">今日转换流水<span class="bt-meta">▲转强/▼转弱 · 昨→今涨停家数+净变 · 圆点大小=力度 · 点看成分</span></div>
        <div v-if="transitions.length" class="tl">
          <div class="tl-scroll">
            <div class="tl-track" :style="{ minWidth: trackMinWidth + 'px' }">
              <!-- 轴上方: 转强(红) 信息卡 -->
              <div class="tl-side up" :style="{ height: CARD_H + 'px' }">
                <button v-for="e in upEvents" :key="e.key" type="button" class="tl-ev up"
                        :class="{ active: activeKey === e.key }" :style="{ left: e.pct + '%' }"
                        :title="`${e.at} ↑转强 ${e.theme} 昨${e.yest}→今${e.limit_up}家 净+${e.delta}${e.height ? ' 最高'+e.height+'板' : ''}${e.sample ? ' 代表'+e.sample : ''}`"
                        @mouseenter="hoverKey = e.key" @mouseleave="hoverKey = null"
                        @focus="hoverKey = e.key" @blur="hoverKey = null"
                        @click="toggleEvent(e.key)">
                  <div class="ev-r1">
                    <span class="ev-arrow">▲</span>
                    <span class="ev-name">{{ e.theme }}</span>
                    <span v-if="e.height >= 1" class="ev-hgt" :class="{ multi: e.height >= 2 }">{{ e.height }}板</span>
                  </div>
                  <div class="ev-r2">
                    <span class="ev-jump"><span class="ev-y">{{ e.yest }}</span><span class="ev-arw">→</span><span class="ev-n">{{ e.limit_up }}</span></span>
                    <span class="ev-delta">+{{ e.delta }}</span>
                  </div>
                  <i class="ev-stem"></i>
                </button>
              </div>

              <!-- 时间轴本体: 事件圆点(大小随变化幅度) -->
              <div class="tl-axis">
                <div class="tl-line"></div>
                <i v-for="e in upEvents" :key="'du' + e.key" class="tl-dot up"
                   :class="{ active: activeKey === e.key }"
                   :style="{ left: e.pct + '%', width: e.dotPx + 'px', height: e.dotPx + 'px' }"></i>
                <i v-for="e in downEvents" :key="'dd' + e.key" class="tl-dot down"
                   :class="{ active: activeKey === e.key }"
                   :style="{ left: e.pct + '%', width: e.dotPx + 'px', height: e.dotPx + 'px' }"></i>
              </div>

              <!-- 时间刻度: 去重后每个时刻一个粗体标签(带刻度线), 居中在该时刻转换簇正下方 —— 时间是重点 -->
              <div class="tl-times">
                <span v-for="g in timeGroups" :key="g.at" class="tl-time" :style="{ left: g.pct + '%' }">{{ g.at }}</span>
              </div>

              <!-- 轴下方: 转弱(绿) 信息卡 -->
              <div class="tl-side down" :style="{ height: CARD_H + 'px' }">
                <button v-for="e in downEvents" :key="e.key" type="button" class="tl-ev down"
                        :class="{ active: activeKey === e.key }" :style="{ left: e.pct + '%' }"
                        :title="`${e.at} ↓转弱 ${e.theme} 昨${e.yest}→今${e.limit_up}家 净${e.delta}${e.broken ? ' 炸'+e.broken : ''}${e.sample ? ' 代表'+e.sample : ''}`"
                        @mouseenter="hoverKey = e.key" @mouseleave="hoverKey = null"
                        @focus="hoverKey = e.key" @blur="hoverKey = null"
                        @click="toggleEvent(e.key)">
                  <i class="ev-stem"></i>
                  <div class="ev-r1">
                    <span class="ev-arrow">▼</span>
                    <span class="ev-name">{{ e.theme }}</span>
                    <span v-if="e.broken > 0" class="ev-broken">炸{{ e.broken }}</span>
                  </div>
                  <div class="ev-r2">
                    <span class="ev-jump"><span class="ev-y">{{ e.yest }}</span><span class="ev-arw">→</span><span class="ev-n">{{ e.limit_up }}</span></span>
                    <span class="ev-delta">{{ e.delta }}</span>
                  </div>
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
.title .stale-tag {
  font-size: 10.5px; font-weight: 600; color: var(--warn-fg); margin-left: 4px; cursor: help;
  background: var(--warn-bg-muted); padding: 1px 6px; border-radius: 4px; white-space: nowrap;
}
.up { color: var(--up-fg); }
.down { color: var(--down-fg); }

.empty { margin-top: 16px; text-align: center; color: var(--fg-subtle); font-size: 13px; padding: 16px; line-height: 1.7; }

.block { margin-top: 8px; }
.block-title { font-size: 12px; font-weight: 700; color: var(--fg-muted); margin-bottom: 4px; display: flex; align-items: baseline; gap: 8px; }
.block-title .bt-meta { font-size: 10.5px; font-weight: 400; color: var(--fg-subtle); }

/* ── ① 今日转换流水 · 按先后顺序等距铺开 + 信息卡 (v1.7.767/770) ── */
/* 轨道宽度由脚本 trackMinWidth 按事件数算(每个 SLOT_PX 一槽, 左右各留 PAD_PX=68px = 卡半宽,
   防首尾卡被裁), 事件多则轨道加宽横向滚动。 */
.tl-scroll { overflow-x: auto; overflow-y: hidden; -webkit-overflow-scrolling: touch; padding-bottom: 2px; }
.tl-track { position: relative; box-sizing: border-box; padding: 4px 52px 0; }
.tl-side { position: relative; }
/* ── 紧凑信息卡 (v1.7.771 压缩): 两行 —— 方向题材徽章 / 昨→今净变时间; 代表股收进悬停明细 ──
   机构盯盘风: 左侧方向色边条, 红=转强 绿=转弱; 高度砍半更紧凑。 */
.tl-ev {
  position: absolute; transform: translateX(-50%);
  display: flex; flex-direction: column; gap: 1px;
  appearance: none; font: inherit; cursor: pointer; text-align: left;
  width: 104px; padding: 1px 5px; border-radius: 4px;
  background: var(--bg-surface); border: 1px solid var(--border-muted);
  border-left-width: 3px; touch-action: manipulation; line-height: 1.15;
  transition: box-shadow 0.12s, transform 0.12s;
}
.tl-ev.up { border-left-color: var(--up-fg); bottom: 0; }
.tl-ev.down { border-left-color: var(--down-fg); top: 0; }
/* 行1: 方向 + 题材 + 连板/炸板徽章 */
.tl-ev .ev-r1 { display: flex; align-items: center; gap: 3px; }
.tl-ev .ev-arrow { font-size: 9px; line-height: 1; flex-shrink: 0; }
.tl-ev.up .ev-arrow { color: var(--up-fg); }
.tl-ev.down .ev-arrow { color: var(--down-fg); }
.tl-ev .ev-name { font-size: 12px; font-weight: 700; color: var(--fg-default); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-ev .ev-hgt { flex-shrink: 0; font-size: 9px; font-weight: 700; font-variant-numeric: tabular-nums; padding: 0 3px; border-radius: 3px; background: var(--fill-subtle, rgba(128,128,128,.14)); color: var(--fg-subtle); }
.tl-ev .ev-hgt.multi { background: var(--up-bg-muted); color: var(--up-fg); }
.tl-ev .ev-broken { flex-shrink: 0; font-size: 9px; font-weight: 700; font-variant-numeric: tabular-nums; padding: 0 3px; border-radius: 3px; background: var(--down-bg-muted); color: var(--down-fg); }
/* 行2: 昨→今家数 + 净变 + 时间 (一行紧凑, tabular 对齐) */
.tl-ev .ev-r2 { display: flex; align-items: baseline; gap: 4px; font-variant-numeric: tabular-nums; }
.tl-ev .ev-jump { display: inline-flex; align-items: baseline; }
.tl-ev .ev-y { font-size: 10px; color: var(--fg-subtle); }
.tl-ev .ev-arw { font-size: 9px; color: var(--fg-subtle); margin: 0 1px; }
.tl-ev .ev-n { font-size: 13px; font-weight: 700; color: var(--fg-default); }
.tl-ev .ev-delta { font-size: 10.5px; font-weight: 700; font-variant-numeric: tabular-nums; }
.tl-ev.up .ev-delta { color: var(--up-fg); }
.tl-ev.down .ev-delta { color: var(--down-fg); }
/* 引线: 卡片连到轴 */
.tl-ev .ev-stem { position: absolute; left: 50%; width: 1px; height: 6px; }
.tl-ev.up .ev-stem { top: 100%; background: color-mix(in srgb, var(--up-fg) 40%, transparent); }
.tl-ev.down .ev-stem { bottom: 100%; background: color-mix(in srgb, var(--down-fg) 40%, transparent); }
.tl-ev:hover, .tl-ev:focus-visible { box-shadow: 0 2px 7px rgba(0,0,0,.10); transform: translateX(-50%) translateY(-1px); outline: none; z-index: 3; }
.tl-ev.up.active { background: var(--up-bg-muted); box-shadow: 0 2px 7px rgba(0,0,0,.12); z-index: 3; }
.tl-ev.down.active { background: var(--down-bg-muted); box-shadow: 0 2px 7px rgba(0,0,0,.12); z-index: 3; }

.tl-axis { position: relative; height: 16px; }
.tl-line { position: absolute; left: 0; right: 0; top: 7px; height: 2px; border-radius: 1px; background: var(--border-default); }
/* 圆点: 大小随净变化幅度(脚本 dotPx), 居中吸附在轴线上 */
.tl-dot { position: absolute; top: 8px; border-radius: 50%; transform: translate(-50%, -50%); border: 2px solid var(--bg-surface); box-sizing: content-box; }
.tl-dot.up { background: var(--up-fg); }
.tl-dot.down { background: var(--down-fg); }
.tl-dot.active { box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-fg) 30%, transparent); }

/* ── 时间刻度轴 (v1.7.773): 时间是重点 —— 去重后粗体标签 + 一根短刻度线接上轴线, 居中在该时刻转换簇下方 ── */
.tl-times { position: relative; height: 15px; margin-top: 1px; }
.tl-time {
  position: absolute; transform: translateX(-50%); top: 3px;
  font-size: 12px; font-weight: 700; letter-spacing: 0.02em;
  color: var(--fg-default); font-variant-numeric: tabular-nums; white-space: nowrap;
}
/* 刻度线: 从时间标签上缘伸向轴线, 让它读作真正的"时间刻度" */
.tl-time::before {
  content: ''; position: absolute; left: 50%; top: -6px;
  width: 1px; height: 5px; background: var(--fg-subtle); transform: translateX(-50%);
}

.tl-detail {
  margin-top: 6px; display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px;
  font-size: 11.5px; padding: 4px 8px; border-radius: 5px; min-height: 22px;
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
