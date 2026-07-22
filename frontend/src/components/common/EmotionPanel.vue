<script setup lang="ts">
// 短线情绪面板 — 嵌入监控看板 (原 EmotionDashboardView 迁移而来, v1.7.x)
// 情绪温度计 + 六指标 + 连板梯队 + 打法切换, 数据每 60s 轮询 /api/emotion/current
import { ref, computed } from 'vue'
import { NSkeleton, NButton, NIcon, NTooltip } from 'naive-ui'
import { RefreshOutline, FlameOutline, SnowOutline } from '@vicons/ionicons5'
import { fetchCurrentEmotion, type EmotionSnapshot, type BoardLadderItem, type BoardStock } from '../../api/emotion'
import EmotionTrendChart from './EmotionTrendChart.vue'
import { useUiStore } from '../../stores/ui'
import { useVisiblePolling } from '../../composables/useVisiblePolling'

type Style = 'pullback' | 'dabanchao'

const ui = useUiStore()

const snap = ref<EmotionSnapshot | null>(null)
const loading = ref(false)
const style = ref<Style>('dabanchao')

// 情绪温度色阶(沸腾→冰点)按涨跌语义走 --up-fg/--down-fg, 中间档用 color-mix 保留层次:
//   高潮=纯红(--up-fg) → 启动=暖(--warn-fg) → 修复=强调蓝(--accent-fg) → 中性=灰(--fg-muted)
//   → 退潮=纯绿(--down-fg) → 冰点=浅绿(down-fg 提亮, 与退潮区分层次); 底色统一 color-mix 透明档
const PHASE_META: Record<string, { color: string; bg: string; desc: string }> = {
  高潮: { color: 'var(--up-fg)', bg: 'color-mix(in srgb, var(--up-fg) 10%, transparent)', desc: '高连板+高封板率+涨停家数高位，情绪最热，警惕见顶' },
  启动: { color: 'var(--warn-fg)', bg: 'color-mix(in srgb, var(--warn-fg) 12%, transparent)', desc: '连板抬升、封板率稳，赚钱效应回升，可进攻' },
  修复: { color: 'var(--accent-fg)', bg: 'color-mix(in srgb, var(--accent-fg) 10%, transparent)', desc: '昨涨停溢价转正，情绪修复中，试错仓位' },
  中性: { color: 'var(--fg-muted)', bg: 'color-mix(in srgb, var(--fg-default) 5%, transparent)', desc: '无明显方向，轻仓观望' },
  退潮: { color: 'var(--down-fg)', bg: 'color-mix(in srgb, var(--down-fg) 10%, transparent)', desc: '封板率偏弱(<50%)且 昨涨停溢价转负 或 封板率较前一档骤降，赚钱效应转差，减仓避险' },
  冰点: { color: 'color-mix(in srgb, var(--down-fg) 72%, white)', bg: 'color-mix(in srgb, var(--down-fg) 12%, transparent)', desc: '最高连板≤3且昨涨停溢价不正，情绪冰点，空仓或潜伏' },
  数据降级: { color: 'var(--fg-subtle)', bg: 'color-mix(in srgb, var(--fg-default) 4%, transparent)', desc: '涨停池数据源不可用，仅有涨跌停家数，部分指标不可判' },
}

const phaseMeta = computed(() => PHASE_META[snap.value?.emotion_phase ?? ''] || PHASE_META['中性'])

// 短线四阶段(冰点·回暖·高潮·退潮) —— 面向短线的更清晰归并, 配一句操作建议
const CYCLE_META: Record<string, { color: string; desc: string }> = {
  高潮: { color: 'var(--up-fg)', desc: '情绪高潮，做龙头但防高潮见顶' },
  回暖: { color: 'var(--warn-fg)', desc: '情绪回暖，可试仓、超跌反抽' },
  退潮: { color: 'var(--down-fg)', desc: '情绪退潮，减仓、只做低吸' },
  冰点: { color: 'color-mix(in srgb, var(--down-fg) 70%, white)', desc: '情绪冰点，空仓等启动信号' },
}
const cycleMeta = computed(() => CYCLE_META[snap.value?.emotion_cycle ?? ''] || { color: 'var(--fg-muted)', desc: '' })

// 风控三档(仓位天花板): 正常=可攻 / 谨慎=控仓 / 危险=停开新仓 (交通灯语义, 非A股涨跌)
const RISK_META: Record<string, { color: string; hint: string }> = {
  正常: { color: 'var(--down-fg)', hint: '仓位可攻' },
  谨慎: { color: 'var(--warn-fg)', hint: '控制仓位' },
  危险: { color: 'var(--up-fg)', hint: '停开新仓/别抄底' },
}
const riskMeta = computed(() => RISK_META[snap.value?.risk?.tier ?? ''] || { color: 'var(--fg-muted)', hint: '' })

function volClass(v: number | null | undefined) {
  if (v == null) return ''
  return v >= 0 ? 'up' : 'down'
}
function volText(v: number | null | undefined) {
  if (v == null) return '量能 —'
  return v >= 0 ? `放量 +${v.toFixed(0)}%` : `缩量 ${v.toFixed(0)}%`
}

const sourceLabel = computed(() => {
  const s = snap.value?.source
  if (s === 'ths') return '同花顺'
  if (s === 'eastmoney') return '东财(备源)'
  if (s === 'quote_estimate') return '降级·仅涨跌停家数'
  return '—'
})
const degraded = computed(() => snap.value?.source === 'quote_estimate')

const ladder = computed<BoardLadderItem[]>(() => snap.value?.board_ladder ?? [])
// 首板家数(连板梯队只列≥2板, 首板太多只显示汇总数)
const firstBoardCount = computed(() => ladder.value.find(l => l.height === 1)?.count ?? 0)
// 连板个股按高度分档 (后端已按高度降序、同档涨幅降序; 这里仅分组)
const boardTiers = computed<{ height: number; stocks: BoardStock[] }[]>(() => {
  const list = snap.value?.board_stocks ?? []
  const groups: { height: number; stocks: BoardStock[] }[] = []
  for (const s of list) {
    let g = groups.find(x => x.height === s.height)
    if (!g) { g = { height: s.height, stocks: [] }; groups.push(g) }
    g.stocks.push(s)
  }
  return groups.sort((a, b) => b.height - a.height)
})
const highestBoardLabel = computed(() =>
  snap.value?.highest_board ? boardLabel(snap.value.highest_board) : '—')

// 点卡片 → 全局通用个股详情弹窗(与股票池/整页同源)
function openIntraday(s: BoardStock) {
  ui.openStock(s.code, s.name)
}
// 连板股标签副信息: 涨幅 + 炸板提示
function stockPctText(v: number | null) {
  return v == null ? '' : (v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`)
}
// 断板标签: 同花顺"N天M板"表示中途断过板(M<N), 真连板已用 height 体现, 这里把原始描述做标签亮出来
function brokeLabel(s: BoardStock) {
  const lbl = s.streak_label || ''
  return lbl.includes('天') ? lbl : ''
}

function premiumClass(v: number | null | undefined) {
  if (v == null) return ''
  return v >= 0 ? 'up' : 'down'
}
function premiumText(v: number | null | undefined) {
  if (v == null) return '—'
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}
function sealText(v: number | null | undefined) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(0)}%`
}
function boardLabel(h: number) {
  return h <= 1 ? '首板' : `${h}连板`
}

async function load() {
  loading.value = true
  try {
    const data = await fetchCurrentEmotion()
    snap.value = (data && (data as EmotionSnapshot).trade_date) ? (data as EmotionSnapshot) : null
  } finally {
    loading.value = false
  }
}

useVisiblePolling(load, 60000)   // 切走标签页暂停, 切回立即补刷
</script>

<template>
  <div class="emotion-panel">
    <div class="head">
      <div class="title">
        <NIcon :component="FlameOutline" :size="16" />
        <span>短线情绪</span>
        <span v-if="snap" class="meta">{{ snap.trade_date }} · {{ sourceLabel }} · 更新 {{ snap.captured_at?.slice(11, 16) }}</span>
      </div>
      <div class="head-right">
        <div class="style-tabs">
          <button :class="['style-tab', { active: style === 'dabanchao' }]" @click="style = 'dabanchao'">打板·超短</button>
          <button :class="['style-tab', { active: style === 'pullback' }]" @click="style = 'pullback'">趋势回踩</button>
        </div>
        <NButton quaternary circle size="tiny" :loading="loading" title="刷新" aria-label="刷新" @click="load">
          <template #icon><NIcon :component="RefreshOutline" :size="14" /></template>
        </NButton>
      </div>
    </div>

    <NSkeleton v-if="loading && !snap" text :repeat="3" style="margin-top: 10px" />

    <div v-else-if="!snap" class="empty">
      暂无情绪快照数据。情绪温度每 3 分钟于交易日自动采集，非交易日不刷新。
    </div>

    <template v-else>
      <div v-if="degraded" class="degrade-banner">
        ⚠ 涨停池数据源暂不可用，当前仅有涨跌停家数，封板率/连板梯队不可判。
      </div>

      <!-- 短线总览: 情绪温度(当天出不出手) 并排 大盘风控三档(仓位天花板) -->
      <div v-if="snap.emotion_score != null" class="overview">
        <div class="ov-emotion">
          <div class="ov-top">
            <span class="ov-tag" :style="{ color: cycleMeta.color }">{{ snap.emotion_cycle || '—' }}</span>
            <span class="ov-score" :style="{ color: cycleMeta.color }">{{ snap.emotion_score }}</span>
            <span class="ov-unit">情绪温度</span>
            <span class="ov-vol" :class="volClass(snap.volume_ratio)">{{ volText(snap.volume_ratio) }}</span>
          </div>
          <div class="ov-gauge"><div class="ov-fill" :style="{ width: snap.emotion_score + '%', background: cycleMeta.color }"></div></div>
          <div class="ov-desc">{{ cycleMeta.desc }}</div>
        </div>
        <div v-if="snap.risk && snap.risk.tier" class="ov-risk">
          <div class="ov-risk-tier" :style="{ color: riskMeta.color }">{{ snap.risk.tier }}</div>
          <div class="ov-risk-lb">大盘风控</div>
          <div class="ov-risk-hint">{{ riskMeta.hint }}</div>
        </div>
      </div>

      <!-- 情绪温度计 -->
      <div class="phase-card" :style="{ background: phaseMeta.bg }">
        <div class="phase-main">
          <NIcon :component="snap.emotion_phase === '冰点' || snap.emotion_phase === '退潮' ? SnowOutline : FlameOutline"
                 :size="22" :style="{ color: phaseMeta.color }" />
          <div class="phase-text" :style="{ color: phaseMeta.color }">{{ snap.emotion_phase || '—' }}</div>
        </div>
        <div class="phase-desc">{{ phaseMeta.desc }}</div>
      </div>

      <!-- 核心指标 -->
      <div class="metrics">
        <div class="metric">
          <NTooltip><template #trigger><div class="m-label">涨停(封板) ⓘ</div></template>收盘仍封板的涨停家数 (同花顺口径)</NTooltip>
          <div class="m-value up">{{ snap.limit_up_count ?? '—' }}</div>
        </div>
        <div class="metric highlight">
          <NTooltip><template #trigger><div class="m-label">曾涨停 ⓘ</div></template>当日曾触及涨停的家数(含盘中炸板的), 比收盘封板更能反映活跃度</NTooltip>
          <div class="m-value up">{{ snap.limit_up_history ?? '—' }}</div>
        </div>
        <div class="metric">
          <div class="m-label">跌停</div>
          <div class="m-value down">{{ snap.limit_down_count ?? '—' }}</div>
        </div>
        <div class="metric highlight">
          <NTooltip><template #trigger><div class="m-label">封板率 ⓘ</div></template>封住/曾涨停 (同花顺官方封板成功率)，情绪退潮最灵敏指标</NTooltip>
          <div class="m-value">{{ sealText(snap.seal_rate) }}</div>
        </div>
        <div class="metric highlight">
          <div class="m-label">最高连板</div>
          <div class="m-value">{{ snap.highest_board ? boardLabel(snap.highest_board) : '—' }}</div>
        </div>
        <div class="metric highlight">
          <NTooltip><template #trigger><div class="m-label">昨涨停溢价 ⓘ</div></template>上一交易日涨停股今日平均涨幅，接力资金赚不赚钱</NTooltip>
          <div class="m-value" :class="premiumClass(snap.yest_limit_up_premium)">{{ premiumText(snap.yest_limit_up_premium) }}</div>
        </div>
      </div>

      <!-- 连板梯队(逐只列出 ≥2 板, 高板在上, 点标签跳分时) -->
      <div class="ladder-box">
        <div class="card-title">
          连板梯队
          <span class="lt-meta">最高 {{ highestBoardLabel }}</span>
          <span class="lt-legend">圈选=自选/持仓 · 橙边=今日开过板</span>
        </div>
        <div v-if="boardTiers.length" class="ladder2">
          <div v-for="t in boardTiers" :key="t.height" class="lt-row">
            <div class="lt-h">{{ boardLabel(t.height) }}</div>
            <div class="lt-chips">
              <span v-for="s in t.stocks" :key="s.code"
                    class="lt-chip" :class="{ 'in-pool': s.in_pool, broke: s.open_times > 0 }"
                    role="button" tabindex="0"
                    :title="(s.reason || '无题材') + (s.open_times > 0 ? ` · 今日开板${s.open_times}次` : '') + ' · 点击看分时'"
                    :aria-label="`${s.name} 分时`"
                    @click="openIntraday(s)" @keydown.enter="openIntraday(s)">
                <span class="lc-name">{{ s.name }}</span>
                <span v-if="brokeLabel(s)" class="lc-streak">{{ brokeLabel(s) }}</span>
                <span v-if="s.pct != null" class="lc-pct">{{ stockPctText(s.pct) }}</span>
                <span v-if="s.reason" class="lc-reason">{{ s.reason }}</span>
              </span>
            </div>
          </div>
        </div>
        <div v-else-if="degraded" class="empty-mini">数据降级，无连板梯队明细</div>
        <div v-else class="empty-mini">当前无 2 板及以上连板个股</div>
        <div v-if="firstBoardCount" class="lt-first">首板 {{ firstBoardCount }} 家（家数已计入情绪，不逐只展开）</div>
      </div>

      <!-- 当日情绪四线趋势 (涨停/跌停/上涨/下跌) -->
      <EmotionTrendChart />

      <p class="style-hint">
        <template v-if="style === 'dabanchao'">打板视角：盯<b>封板率/最高连板/昨涨停溢价</b>——退潮/冰点阶段空仓，启动/高潮阶段打首板或低位连板。</template>
        <template v-else>回踩视角：情绪温度作背景过滤——冰点/退潮阶段即使主升浪回踩到位也减小试错仓位，启动/修复阶段再正常执行回踩买点。</template>
      </p>
    </template>
  </div>
</template>

<style scoped>
.emotion-panel {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 6px;
  padding: 8px 12px;
}
/* 机构模块头 (v1.7.650): 发丝底线 + 加粗收紧标题 + mono 计数 */
.head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border-muted); }
.title { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 700; letter-spacing: 0.02em; color: var(--fg-default); }
.title .meta { font-family: var(--font-mono); font-size: 10.5px; font-weight: 500; color: var(--fg-subtle); margin-left: 4px; letter-spacing: 0.02em; }
.head-right { display: flex; align-items: center; gap: 10px; }
.style-tabs { display: inline-flex; background: var(--bg-sunken); border-radius: 7px; padding: 2px; }
.style-tab { border: none; background: transparent; padding: 4px 12px; font-size: 12px; border-radius: 5px; cursor: pointer; touch-action: manipulation; color: var(--fg-muted); transition: all 0.15s; }
.style-tab.active { background: var(--bg-surface); color: var(--accent-fg); font-weight: 600; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }

.degrade-banner { margin-top: 8px; padding: 5px 10px; background: var(--warn-bg-muted); color: var(--warn-fg); border-radius: 6px; font-size: 12px; }

/* 短线总览: 左情绪温度(0-100分+四阶段+量能) 并排 右风控三档 */
.overview { margin-top: 8px; display: grid; grid-template-columns: 1fr 116px; gap: 8px; }
.ov-emotion { background: var(--bg-sunken); border-radius: 8px; padding: 8px 12px; box-shadow: inset 0 0 0 1px var(--border-muted); }
.ov-top { display: flex; align-items: baseline; gap: 8px; }
.ov-tag { font-size: 16px; font-weight: 800; letter-spacing: 1px; }
.ov-score { font-family: var(--font-mono); font-size: 26px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.ov-unit { font-size: 11px; color: var(--fg-subtle); }
.ov-vol { margin-left: auto; font-family: var(--font-mono); font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.ov-gauge { margin-top: 7px; height: 6px; background: var(--bg-default); border-radius: 999px; overflow: hidden; }
.ov-fill { height: 100%; border-radius: 999px; transition: width 0.4s ease; }
.ov-desc { margin-top: 6px; font-size: 11.5px; color: var(--fg-muted); line-height: 1.4; }
.ov-risk { background: var(--bg-sunken); border-radius: 8px; padding: 8px 6px; box-shadow: inset 0 0 0 1px var(--border-muted); display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 2px; }
.ov-risk-tier { font-size: 20px; font-weight: 800; letter-spacing: 2px; }
.ov-risk-lb { font-size: 10.5px; color: var(--fg-subtle); }
.ov-risk-hint { font-size: 10.5px; color: var(--fg-muted); line-height: 1.3; }

.phase-card { margin-top: 8px; display: flex; align-items: center; gap: 12px; border-radius: 8px; padding: 7px 12px; }
.phase-main { display: flex; align-items: center; gap: 8px; }
.phase-text { font-size: 20px; font-weight: 800; letter-spacing: 1px; }
.phase-desc { font-size: 11.5px; color: var(--fg-muted); line-height: 1.45; }

/* KPI 六格 (v1.7.657 对齐机构终端效果图): mono 等宽大数字 + 安静素底高亮(不抢终端蓝) */
.metrics { display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; margin-top: 8px; }
.metric { background: var(--bg-default); border-radius: 6px; padding: 6px 5px; text-align: center; }
.metric.highlight { background: var(--bg-sunken); box-shadow: inset 0 0 0 1px var(--border-muted); }
.m-label { font-size: 11px; color: var(--fg-subtle); margin-bottom: 2px; }
.m-value { font-family: var(--font-mono); font-size: 18px; font-weight: 700; color: var(--fg-default); font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }

.ladder-box { margin-top: 8px; }
.card-title { font-size: 12px; font-weight: 600; color: var(--fg-default); margin-bottom: 6px; display: flex; align-items: baseline; flex-wrap: wrap; }
.lt-meta { font-weight: 400; color: var(--fg-subtle); margin-left: 6px; }
.lt-legend { font-weight: 400; color: var(--fg-subtle); font-size: 10.5px; margin-left: auto; }

/* 阶梯标签: 每档一行, 高板在上, 个股做可点标签 */
.ladder2 { display: flex; flex-direction: column; gap: 5px; }
.lt-row { display: flex; align-items: flex-start; gap: 8px; }
.lt-h { flex-shrink: 0; width: 46px; text-align: right; font-size: 12px; font-weight: 700; color: var(--up-fg); padding-top: 3px; }
.lt-chips { flex: 1; min-width: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 4px; }
.lt-chip { display: flex; align-items: baseline; gap: 4px; background: var(--up-bg-muted); border: 1px solid var(--border-muted); border-radius: 6px; padding: 2px 7px; cursor: pointer; touch-action: manipulation; line-height: 1.4; transition: all 0.12s; min-width: 0; }
.lt-chip:hover { box-shadow: 0 2px 6px rgba(0,0,0,0.12); border-color: var(--warn-fg); }
.lt-chip.in-pool { background: var(--accent-bg-muted); border-color: var(--accent-fg); }
.lt-chip.broke { border-style: dashed; border-color: var(--warn-fg); }
.lc-name { flex-shrink: 0; font-size: 12px; font-weight: 600; color: var(--fg-default); white-space: nowrap; }
.lc-pct { flex-shrink: 0; font-family: var(--font-mono); font-size: 11px; font-weight: 600; color: var(--up-fg); font-variant-numeric: tabular-nums; }
.lc-streak { flex-shrink: 0; font-size: 10px; color: var(--warn-fg); background: var(--warn-bg-muted); border-radius: 4px; padding: 0 4px; white-space: nowrap; }
.lc-reason { flex: 1; min-width: 0; font-size: 10.5px; color: var(--fg-subtle); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lt-first { margin-top: 6px; font-size: 11.5px; color: var(--fg-subtle); }
.empty-mini { color: var(--fg-subtle); font-size: 12px; }

.style-hint { margin-top: 8px; font-size: 11.5px; color: var(--fg-muted); line-height: 1.5; background: var(--bg-sunken); padding: 6px 10px; border-radius: 6px; }
.style-hint b { color: var(--up-fg); }

.empty { margin-top: 16px; text-align: center; color: var(--fg-subtle); font-size: 13px; padding: 16px; }

/* A股: 涨红跌绿 */
.up { color: var(--up-fg); }
.down { color: var(--down-fg); }

@media (max-width: 768px) {
  .emotion-panel { padding: 8px 10px; }
  .head { gap: 6px; }
  .title { font-size: 13px; }
  .overview { grid-template-columns: 1fr; gap: 6px; }
  .ov-risk { flex-direction: row; gap: 8px; padding: 7px 12px; justify-content: flex-start; }
  .ov-risk-hint { margin-left: auto; }
  .phase-card { margin-top: 8px; padding: 8px 10px; gap: 10px; }
  .phase-text { font-size: 20px; }
  .phase-desc { font-size: 11px; }
  .metrics { grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 8px; }
  .metric { padding: 5px 4px; }
  .m-value { font-size: 16px; }
  .m-label { margin-bottom: 2px; }
  .ladder-box { margin-top: 8px; }
  .lt-h { width: 40px; }
  .lt-chips { grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); }
  .lt-legend { display: none; }
  .style-hint, .degrade-banner { margin-top: 8px; padding: 6px 9px; }
}
</style>
