<script setup lang="ts">
// 临近买点面板 — 嵌入监控看板 (v1.7.x)
// 扫全自选+持仓, 列出当前距买点(弱势极限/回踩10MA缩量后突破昨高/回踩20MA缩量后突破昨高/中继平台突破·逼近上沿/强势起点)触发或接近的票。
// 数据由后端 refresh_near_buy_snapshot 每 3min 定时算并落库, 本面板每 60s 轮询读快照。
import { ref, computed } from 'vue'
import { NSkeleton, NButton, NIcon, NTooltip } from 'naive-ui'
import { RefreshOutline, LocateOutline } from '@vicons/ionicons5'
import { fetchNearBuy, type NearBuySnapshot, type NearBuyItem, type NearBuyHit } from '../../api/near-buy'
import { useUiStore } from '../../stores/ui'
import { useVisiblePolling } from '../../composables/useVisiblePolling'

const ui = useUiStore()
const snap = ref<NearBuySnapshot | null>(null)
const loading = ref(false)

const items = computed<NearBuyItem[]>(() => snap.value?.items ?? [])
const triggered = computed(() => items.value.filter(i => i.tier >= 2))
const near = computed(() => items.value.filter(i => i.tier < 2))

function pctText(v: number) {
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}
// 点卡片: 弹出通用个股详情弹窗(分时+日K+大单, 全局单实例), 与股票池/连板梯队同源, 不跳整页
function openChart(it: NearBuyItem) {
  ui.openStock(it.code, it.name)
}
// 多买点时展开"主点": 优先触发, 否则第一条
function primaryHit(it: NearBuyItem) {
  return it.hits?.find(h => h.kind === '触发') ?? it.hits?.[0]
}
// ── 差距可视化(v1.7.536): 贴线度进度条 + 条件圆点, 替掉一排文字 ──
// 贴线度 0~1: 越贴近均线/上沿越满(触发=满格); = 1 - 距线/贴线带
function lineFill(h?: NearBuyHit): number {
  if (!h) return 0
  if (h.kind === '触发') return 1
  if (h.dist_pct == null || h.band_pct == null || h.band_pct <= 0) return 0
  return Math.max(0, Math.min(1, 1 - h.dist_pct / h.band_pct))
}
function distLabel(h?: NearBuyHit): string {
  if (!h) return ''
  if (h.kind === '触发') return '已越线'
  return h.dist_pct != null ? `距线${h.dist_pct.toFixed(1)}%` : ''
}
// 条件文案: 全满足 / 还差N项(短名), 完整含阈值见 hover
function condLabel(h?: NearBuyHit): string {
  const m = h?.miss ?? []
  if (!m.length) return '条件全满足'
  const names = m.map(s => s.split('(')[0]).join('·')
  return `还差${m.length}项: ${names}`
}
async function load() {
  loading.value = true
  try {
    snap.value = await fetchNearBuy()
  } finally {
    loading.value = false
  }
}

useVisiblePolling(load, 60000)   // 切走标签页暂停, 切回立即补刷
</script>

<template>
  <div class="nearbuy-panel">
    <div class="head">
      <div class="title">
        <NIcon :component="LocateOutline" :size="16" />
        <span>临近买点</span>
        <span v-if="snap?.computed_at" class="meta">
          扫{{ snap.scanned }}只 · 触发{{ triggered.length }}/接近{{ near.length }} · 更新 {{ String(snap.computed_at).slice(11, 16) }}
        </span>
      </div>
      <NButton quaternary circle size="tiny" :loading="loading" title="刷新" aria-label="刷新" @click="load">
        <template #icon><NIcon :component="RefreshOutline" :size="14" /></template>
      </NButton>
    </div>

    <NSkeleton v-if="loading && !snap" text :repeat="3" style="margin-top: 10px" />

    <div v-else-if="!items.length" class="empty">
      当前自选池暂无接近买点的票。接近度每 3 分钟于交易日自动计算(覆盖全部自选+持仓), 非交易日保留上一交易日结果。
    </div>

    <template v-else>
      <div class="legend">
        <span class="lg trig">触发</span>条件已全满足(口径同实时推送)
        <span class="lg near">接近</span>已贴线, 只差量能或站位
      </div>

      <div class="list">
        <div v-for="it in items" :key="it.code"
             :class="['row', it.tier >= 2 ? 'is-trig' : 'is-near']"
             role="button" tabindex="0"
             :aria-label="`${it.name} ${it.code} 详情`"
             @click="openChart(it)" @keydown.enter="openChart(it)">
          <div class="row-head">
            <div class="stock">
              <span class="name">{{ it.name }}</span>
              <span class="code">{{ it.code }}</span>
              <span v-if="it.status_label" class="status">{{ it.status_label }}</span>
            </div>
            <div class="quote">
              <span class="price">{{ it.price?.toFixed(2) }}</span>
              <span class="pct" :class="it.pct >= 0 ? 'up' : 'down'">{{ pctText(it.pct) }}</span>
            </div>
          </div>
          <div class="hits">
            <div class="chips">
              <span v-for="(h, idx) in it.hits" :key="idx" class="chip" :class="h.kind === '触发' ? 'c-trig' : 'c-near'">
                {{ h.kind }}·{{ h.buy_name }}
              </span>
            </div>
            <div v-if="primaryHit(it)" class="gap-viz">
              <!-- 贴线度: 越满=越贴近均线/上沿(触发=满格) -->
              <div class="viz-row" :title="primaryHit(it)?.note">
                <span class="viz-tag">贴线</span>
                <span class="bar-track">
                  <span class="bar-fill" :class="primaryHit(it)?.kind === '触发' ? 'f-trig' : 'f-near'"
                        :style="{ width: (lineFill(primaryHit(it)) * 100).toFixed(0) + '%' }" />
                </span>
                <span class="viz-val">{{ distLabel(primaryHit(it)) }}</span>
              </div>
              <!-- 条件圆点: 绿=已满足, 灰=还差 -->
              <div v-if="(primaryHit(it)?.total ?? 0) > 0" class="viz-row"
                   :title="condLabel(primaryHit(it))">
                <span class="viz-tag">条件</span>
                <span class="dots">
                  <i v-for="n in (primaryHit(it)?.total ?? 0)" :key="n"
                     class="dot" :class="n <= (primaryHit(it)?.met ?? 0) ? 'on' : 'off'" />
                </span>
                <span class="viz-val miss-val">{{ condLabel(primaryHit(it)) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <p class="foot-hint">
        点任一只弹出分时+日K图。"接近"是日线结构(主升浪回踩+缩量)判断, 仅供观察盯盘; 真正触发买点以实时推送为准。
      </p>
    </template>
  </div>
</template>

<style scoped>
.nearbuy-panel {
  background: var(--bg-surface);
  border: 1px solid var(--border-muted);
  border-radius: 6px;
  padding: 10px 12px;
}
/* 机构模块头 (v1.7.650): 发丝底线 + 加粗收紧标题 + mono 计数 */
.head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border-muted); }
.title { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 700; letter-spacing: 0.02em; color: var(--fg-default); }
.title .meta { font-family: var(--font-mono); font-size: 10.5px; font-weight: 500; color: var(--fg-subtle); margin-left: 4px; letter-spacing: 0.02em; }

.legend { margin-top: 10px; font-size: 11px; color: var(--fg-subtle); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.lg { display: inline-block; padding: 1px 7px; border-radius: 8px; font-size: 11px; color: var(--on-emphasis); margin-left: 6px; }
.lg.trig { background: var(--up-fg); }
.lg.near { background: var(--warn-fg); }

/* PC 多列网格用上横向空间; 窄屏自动回落单列。grid-auto-rows:1fr 让所有行等高, 卡片大小统一 */
.list { margin-top: 8px; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); grid-auto-rows: 1fr; gap: 6px; }
.row { border: 1px solid var(--border-muted); border-radius: 7px; padding: 6px 9px; cursor: pointer; touch-action: manipulation; transition: all 0.15s; height: 100%; display: flex; flex-direction: column; }
.row:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-color: var(--border-default); }
.row.is-trig { background: var(--up-bg-muted); border-color: var(--up-fg); }
.row.is-near { background: var(--warn-bg-muted); }

.row-head { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
.stock { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
.name { font-size: 13px; font-weight: 600; color: var(--fg-default); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.code { font-size: 11px; color: var(--fg-subtle); flex-shrink: 0; font-variant-numeric: tabular-nums; }
.status { font-size: 10px; color: var(--accent-fg); background: var(--accent-bg-muted); padding: 0 5px; border-radius: 4px; flex-shrink: 0; }
.quote { display: flex; align-items: baseline; gap: 6px; flex-shrink: 0; }
.price { font-size: 13px; font-weight: 600; color: var(--fg-default); font-variant-numeric: tabular-nums; }
.pct { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }

/* 多买点: 顶部一行 chips 列全部命中, 下面只展开主点(优先触发)一行说明 */
.hits { margin-top: 5px; display: flex; flex-direction: column; gap: 4px; flex: 1; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: 10.5px; font-weight: 600; color: var(--on-emphasis); padding: 1px 7px; border-radius: 9px; white-space: nowrap; }
.chip.c-trig { background: var(--up-fg); }
.chip.c-near { background: var(--warn-fg); }
/* 差距可视化: 贴线度进度条 + 条件圆点(v1.7.536), 一眼看出多接近/差几项, 文字退到 hover */
/* margin-top:auto 把贴线/条件两行顶到卡片底部, 同一行各卡这两行横向对齐(v1.7.537) */
.gap-viz { display: flex; flex-direction: column; gap: 3px; margin-top: auto; padding-top: 4px; }
.viz-row { display: flex; align-items: center; gap: 6px; font-size: 11px; min-width: 0; }
.viz-tag { color: var(--fg-subtle); flex-shrink: 0; width: 24px; }
.bar-track { position: relative; flex: 1; height: 7px; min-width: 40px; background: var(--bg-sunken); border-radius: 4px; overflow: hidden; }
.bar-fill { position: absolute; left: 0; top: 0; height: 100%; border-radius: 4px; transition: width 0.3s; }
.bar-fill.f-near { background: linear-gradient(90deg, #f7c873, var(--warn-fg)); }
.bar-fill.f-trig { background: linear-gradient(90deg, #f0708d, var(--up-fg)); }
.viz-val { flex-shrink: 0; color: var(--fg-subtle); font-variant-numeric: tabular-nums; white-space: nowrap; }
.viz-val.miss-val { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--warn-fg); font-weight: 600; }
.dots { display: inline-flex; align-items: center; gap: 3px; flex-shrink: 0; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.on { background: var(--success-fg); }
.dot.off { background: var(--border-default); }

.foot-hint { margin-top: 10px; font-size: 11px; color: var(--fg-subtle); line-height: 1.6; background: rgba(0,0,0,0.02); padding: 7px 10px; border-radius: 6px; }
.empty { margin-top: 16px; text-align: center; color: var(--fg-subtle); font-size: 13px; padding: 16px; line-height: 1.6; }

/* A股: 涨红跌绿 */
.up { color: var(--up-fg); }
.down { color: var(--down-fg); }

@media (max-width: 768px) {
  .nearbuy-panel { padding: 8px 10px; }
  .title { font-size: 13px; }
  .legend, .list { margin-top: 7px; }
  .list { grid-template-columns: 1fr; gap: 6px; }
  .row { padding: 6px 8px; }
  .hits { margin-top: 3px; }
  /* 窄屏: 条件文案不省略号截断, 换行显示全 */
  .viz-val.miss-val { white-space: normal; }
  .foot-hint { margin-top: 7px; padding: 6px 9px; }
}
</style>
