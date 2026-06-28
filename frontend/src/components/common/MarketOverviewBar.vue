<script setup lang="ts">
// v1.7.88: 实时市场概览条 — 三层布局(全球/A股/温度), 30s 自动刷新
// 取代盘面日报顶部那块静态展示, 让数据实时跟随
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { fetchMarketOverview, fetchRegime, type MarketOverview, type RegimeData } from '../../api/market-report'
import FreshnessBadge from './FreshnessBadge.vue'

const data = ref<MarketOverview | null>(null)
const regime = ref<RegimeData | null>(null)
const loading = ref(false)
let timer: number | null = null
let clockTimer: number | null = null

// 各市场交易时段(用 Intl 取当地时间, 自动含夏令时) → 判断"是否盘中", 驱动脉动指示
const SESSION: Record<string, { tz: string; win: [number, number][] }> = {
  美股: { tz: 'America/New_York', win: [[570, 960]] },             // 09:30-16:00
  欧洲: { tz: 'Europe/Berlin',    win: [[540, 1050]] },            // 09:00-17:30
  港股: { tz: 'Asia/Hong_Kong',  win: [[570, 720], [780, 960]] }, // 09:30-12:00 / 13:00-16:00
  日本: { tz: 'Asia/Tokyo',      win: [[540, 690], [750, 900]] }, // 09:00-11:30 / 12:30-15:00
}
const regionOpen = ref<Record<string, boolean>>({})
function _isOpen(cfg: { tz: string; win: [number, number][] }): boolean {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: cfg.tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date())
    const wd = parts.find(p => p.type === 'weekday')?.value
    if (wd === 'Sat' || wd === 'Sun') return false
    let hh = Number(parts.find(p => p.type === 'hour')?.value)
    if (hh === 24) hh = 0
    const mm = Number(parts.find(p => p.type === 'minute')?.value)
    const t = hh * 60 + mm
    return cfg.win.some(([a, b]) => t >= a && t <= b)
  } catch { return false }
}
function refreshSessions() {
  const m: Record<string, boolean> = {}
  for (const r in SESSION) m[r] = _isOpen(SESSION[r])
  regionOpen.value = m
}

// 数值变动闪烁: 轮询拿到新数据时, 变化的指数高亮一下, 让用户感知"还在盘中变化"
const flashSet = ref<Set<string>>(new Set())
const _prevPct: Record<string, number> = {}
let flashTimer: number | null = null
function detectChanges(list: { name: string; pct_change: number }[] | undefined) {
  if (!list) return
  const changed = new Set<string>()
  for (const g of list) {
    if (_prevPct[g.name] !== undefined && _prevPct[g.name] !== g.pct_change) changed.add(g.name)
    _prevPct[g.name] = g.pct_change
  }
  if (changed.size) {
    flashSet.value = changed
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = window.setTimeout(() => { flashSet.value = new Set() }, 1000)
  }
}

const REGIME_LABEL: Record<string, string> = {
  friendly: '友好',
  neutral: '中性',
  hostile: '危险',
}
const REGIME_STYLE: Record<string, { bg: string; color: string; ring: string }> = {
  friendly: { bg: '#dcfce7', color: '#15803d', ring: '#86efac' },
  neutral:  { bg: '#fef3c7', color: '#b45309', ring: '#fcd34d' },
  hostile:  { bg: '#fee2e2', color: '#b91c1c', ring: '#fca5a5' },
}
const regimeHint = computed(() => {
  const r = regime.value?.regime
  if (r === 'friendly') return '买点信号原样推送'
  if (r === 'neutral')  return '买点降级: 仅入库+前端, 不推送'
  if (r === 'hostile')  return '买点降级: 仅入库+前端, 不推送'
  return ''
})

// v1.7.97: 后端定时任务每 30s 写 DB, 前端读 DB; 显示用 snapshot_at(数据时间), 不用浏览器拉取时间
const snapshotTime = computed(() => {
  const s = data.value?.snapshot_at
  if (!s) return ''
  // snapshot_at 形如 "2026-05-28 00:33:54", 只取后 8 位
  return s.length >= 19 ? s.slice(11, 19) : s
})

async function load() {
  loading.value = true
  try {
    const [ov, rg] = await Promise.all([
      fetchMarketOverview(),
      fetchRegime().catch(() => null),
    ])
    data.value = ov
    detectChanges(ov?.global_indices)
    refreshSessions()
    if (rg) regime.value = rg
  } catch {
    /* silent — 监控面板有专门的错误展示 */
  } finally {
    loading.value = false
  }
}

const globalByRegion = computed(() => {
  const out: Record<string, MarketOverview['global_indices']> = {}
  for (const g of data.value?.global_indices || []) {
    const r = g.region || '其他'
    if (!out[r]) out[r] = []
    out[r].push(g)
  }
  return out
})

function pctColor(p: number) {
  if (p > 0) return '#e53e3e'
  if (p < 0) return '#16a34a'
  return '#666'
}
function pctText(p: number) {
  return (p >= 0 ? '+' : '') + p.toFixed(2) + '%'
}

// 标签页切走时跳过网络刷新, 切回立即补刷(时段状态是纯本地计算, 照刷)
function onVisibilityChange() {
  if (!document.hidden) load()
}

onMounted(() => {
  refreshSessions()
  load()
  timer = window.setInterval(() => { if (!document.hidden) load() }, 30_000)
  clockTimer = window.setInterval(refreshSessions, 30_000)  // 时段状态随时间推进刷新
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (clockTimer) clearInterval(clockTimer)
  if (flashTimer) clearTimeout(flashTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div v-if="data" class="market-overview">
    <!-- 大盘 regime 徽章 -->
    <div v-if="regime" class="regime-row">
      <div class="regime-main" :style="{ background: REGIME_STYLE[regime.regime].bg, color: REGIME_STYLE[regime.regime].color, borderColor: REGIME_STYLE[regime.regime].ring }">
        <span class="regime-label">大盘 {{ REGIME_LABEL[regime.regime] }}</span>
        <span class="regime-score">{{ regime.score }} 分</span>
      </div>
      <div class="regime-factors">
        <span v-for="f in regime.factors" :key="f.name" class="rf"
              :class="{ 'rf-pos': f.score > 0, 'rf-neg': f.score < 0 }"
              :title="f.reason">
          {{ f.name }} <b>{{ f.score >= 0 ? '+' : '' }}{{ f.score }}</b>
        </span>
      </div>
      <span class="regime-hint">{{ regimeHint }}</span>
    </div>

    <!-- 大盘大白话解读(随当时数据变化) -->
    <div v-if="regime && (regime.summary || regime.action)" class="regime-plain">
      <div v-if="regime.summary" class="rp-sum">💡 {{ regime.summary }}</div>
      <div v-if="regime.action" class="rp-act">👉 操作:{{ regime.action }}</div>
    </div>

    <!-- 全球股市 -->
    <div v-if="data.global_indices.length" class="global-row">
      <div
        v-for="(items, region) in globalByRegion"
        :key="region"
        class="global-card"
      >
        <div class="region-label">
          <span class="live-dot" :class="regionOpen[region] ? 'on' : 'off'" />
          {{ region }}
          <span class="region-status" :class="regionOpen[region] ? 'st-open' : 'st-closed'">
            {{ regionOpen[region] ? '盘中' : '休市' }}
          </span>
          <span v-if="items[0]?.update_time" class="region-time">{{ items[0].update_time }}</span>
          <span v-else-if="regionOpen[region]" class="region-time">实时</span>
        </div>
        <div v-for="g in items" :key="g.name" class="global-line">
          <span class="g-name">{{ g.name }}</span>
          <span class="g-price"><b>{{ g.price.toFixed(2) }}</b></span>
          <span class="g-pct" :class="{ 'g-flash': flashSet.has(g.name) }" :style="{ color: pctColor(g.pct_change) }">{{ pctText(g.pct_change) }}</span>
        </div>
      </div>
    </div>

    <!-- A 股四指数 + 涨跌/涨停家数已在下方「大盘指数概览」展示, 此处不重复 -->

    <!-- 数据新鲜度: 绝对定位到卡片右上角, 省掉底部一整行 -->
    <div class="corner-fresh" title="后端 30 秒入库, 前端轮询">
      <FreshnessBadge :updated-at="data?.snapshot_at" :stale-seconds="120" :error-seconds="600" />
      <span v-if="loading" class="loading-dot">·</span>
    </div>
  </div>
</template>

<style scoped>
.market-overview {
  background: #fff;
  border: 1px solid var(--border, #efeff5);
  border-radius: 6px;
  padding: 10px 12px;
  position: relative;
}

/* 数据新鲜度徽章 — 卡片右上角 */
.corner-fresh {
  position: absolute;
  top: 8px;
  right: 10px;
  display: inline-flex;
  align-items: center;
  z-index: 1;
}

/* 大盘 regime 徽章行 */
.regime-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.regime-main {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 14px;
  border: 1.5px solid;
  font-size: 12px;
  font-weight: 700;
}
.regime-score {
  font-weight: 800;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.regime-factors {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.rf {
  font-size: 11px;
  color: var(--text2);
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.04);
  cursor: help;
}
.rf b { font-weight: 700; }
.rf-pos b { color: #16a34a; }
.rf-neg b { color: #dc2626; }
.regime-hint {
  font-size: 11px;
  color: var(--text3);
  font-style: italic;
}
.regime-plain {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 8px;
  line-height: 1.45;
}
.rp-sum {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text1, #1f2328);
}
.rp-act {
  font-size: 12px;
  color: var(--text2, #656d76);
}

/* 全球股市行 */
.global-row {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}
.global-card {
  flex: 1;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 6px 8px;
}
.region-label {
  font-size: 11px;
  font-weight: 600;
  color: #2563eb;
  margin-bottom: 3px;
  display: flex;
  align-items: center;
  gap: 5px;
}
.region-time {
  font-size: 10px;
  font-weight: 400;
  color: #aaa;
  margin-left: auto;
}
/* 盘中脉动点 / 休市静态点 */
.live-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.live-dot.on { background: #16a34a; box-shadow: 0 0 0 0 rgba(22,163,74,0.6); animation: live-pulse 1.4s infinite; }
.live-dot.off { background: #c4c8cf; }
@keyframes live-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(22,163,74,0.55); }
  70%  { box-shadow: 0 0 0 6px rgba(22,163,74,0); }
  100% { box-shadow: 0 0 0 0 rgba(22,163,74,0); }
}
.region-status { font-size: 10px; font-weight: 600; padding: 0 5px; border-radius: 7px; }
.region-status.st-open { color: #15803d; background: rgba(22,163,74,0.12); }
.region-status.st-closed { color: #94a3b8; background: rgba(0,0,0,0.05); }
.global-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  padding: 2px 0;
  border-top: 1px dashed #eee;
}
.global-line:first-of-type {
  border-top: none;
}
.g-name {
  color: #444;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.g-price {
  margin-right: 6px;
  font-variant-numeric: tabular-nums;
}
.g-pct {
  font-weight: 600;
  min-width: 56px;
  text-align: right;
  border-radius: 3px;
  font-variant-numeric: tabular-nums;
}
/* 数值变动闪烁: 轮询拿到新值时高亮一下 */
.g-flash { animation: g-flash-bg 1s ease-out; }
@keyframes g-flash-bg {
  0% { background: rgba(37,99,235,0.28); }
  100% { background: transparent; }
}

.loading-dot {
  color: #2563eb;
  margin-left: 4px;
  animation: blink 1s infinite;
}
@keyframes blink {
  50% { opacity: 0.3; }
}

@media (max-width: 768px) {
  .market-overview { padding: 8px 10px; }
  .regime-row { gap: 6px; margin-bottom: 6px; }
  .global-row {
    flex-wrap: wrap;
    gap: 5px;
    margin-bottom: 6px;
  }
  .global-card {
    min-width: calc(50% - 4px);
    padding: 5px 7px;
  }
}
</style>
