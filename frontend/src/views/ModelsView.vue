<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, nextTick } from 'vue'
import { MODELS, METRIC_TIPS } from '../data/models'
import { useResponsive } from '../composables/useResponsive'
import {
  fetchModelBacktest, fetchSignalOutcomeStats, fetchModelWinrate,
  type ModelBacktest, type ModelBacktestRow, type SignalOutcomeStatsItem,
  type ModelWinrate,
} from '../api/signals'

const bt = ref<ModelBacktest>({ run_date: null, window_start: null, models: [] })
const outcome = ref<Record<string, SignalOutcomeStatsItem>>({})
const winrate = ref<ModelWinrate>({ run_date: null, models: [] })
const loading = ref(false)
const showVisual = ref(false)
const { isPhone } = useResponsive()

const winrateRanked = computed(() => winrate.value.models.filter(r => r.rank_3m != null))
function fmtNet(v: number | null): string { return v == null ? '—' : v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%` }
function fmtDelta3v6(r: any): string {
  if (r.net_3m == null || r.net_6m == null) return '—'
  const d = r.net_3m - r.net_6m
  return d >= 0 ? `↑+${d.toFixed(1)}%` : `↓${d.toFixed(1)}%`
}

async function load() {
  loading.value = true
  try { const [b, o, w] = await Promise.all([fetchModelBacktest(), fetchSignalOutcomeStats(180), fetchModelWinrate()]); bt.value = b; outcome.value = o; winrate.value = w } catch {} finally { loading.value = false }
}
onMounted(load)

const btMap = computed<Record<string, ModelBacktestRow>>(() => { const m: Record<string, ModelBacktestRow> = {}; for (const r of bt.value.models) m[r.signal_id] = r; return m })
const btOf = (id: string) => btMap.value[id] || null
const outOf = (id: string) => outcome.value[id] || null

const activeId = ref<string>(MODELS[0]?.id || '')
// 手机端横向 chips 导航: 跳转/滚动联动时把当前 chip 滚进可视区
function ensureNavChipVisible(id: string) {
  if (!isPhone.value) return
  nextTick(() => {
    const li = document.querySelector(`.mp-nav li[data-mid="${id}"]`) as HTMLElement | null
    li?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  })
}
function scrollTo(id: string) { document.getElementById('m-' + id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); activeId.value = id; ensureNavChipVisible(id) }

let observer: IntersectionObserver | null = null
onMounted(() => nextTick(() => {
  const targets = MODELS.map(m => document.getElementById('m-' + m.id)).filter(Boolean) as HTMLElement[]
  if (!targets.length) return
  observer = new IntersectionObserver((entries) => {
    const visible = entries.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
    if (visible.length) { const id = (visible[0].target as HTMLElement).id.replace(/^m-/, ''); if (id && MODELS.some(m => m.id === id) && id !== activeId.value) { activeId.value = id; ensureNavChipVisible(id) } }
  }, { rootMargin: '-80px 0px -50% 0px', threshold: 0.1 })
  targets.forEach(t => observer!.observe(t))
}))
onBeforeUnmount(() => { observer?.disconnect(); observer = null })

interface FlowStage { label: string; color: string; items: string[] }
const FLOW_STAGES: Record<string, FlowStage[]> = {
  BUY_PLATFORM_BREAKOUT: [{label:'前置条件',color:'#3b82f6',items:['前12日横盘·振幅≤15%','平台缓升台阶 后半↑0%~5%','前20日主升≥20%']},{label:'触发条件',color:'#16a34a',items:['收盘≥平台上沿×1.005','放量≥平台均量×1.2','成交额≥10亿','尾盘14:40起']},{label:'出场规则',color:'#ea580c',items:['+7%卖半','剩半破MA10×0.98','-6%止损','T+10时停']}],
  BUY_VOL_BREAKOUT: [{label:'前置条件',color:'#3b82f6',items:['昨量<均量×0.8','昨缩量整理','缩量日长下影线≥振幅0.4(承接)','昨日非涨停封板(排除假缩量)']},{label:'触发条件',color:'#16a34a',items:['放量≥2×昨量','≥1.5×均量','盘中最高>昨高×1.02','收盘站上MA10/20','外推全天额≥10亿','实时累计额≥5亿(不卡10点)','现价未逼近涨停板(距板≤1%不发)']},{label:'出场规则',color:'#ea580c',items:['+7%卖半','剩半破MA10×0.98','-6%止损','T+10时停']}],
  BUY_RALLY_MA20: [{label:'前置条件',color:'#3b82f6',items:['近30日≥15%主升浪','昨收贴MA20±3%','昨缩量<均量×0.8']},{label:'触发条件',color:'#16a34a',items:['盘中最高>昨高×1.025','放量确认≥近10日均量×1.5','累计额≥5亿底线','突破即触发(不卡10点)']},{label:'出场规则',color:'#ea580c',items:['+15%卖半','-7%止损','剩半破MA20×0.97','T+15清']}],
  BUY_RALLY_MA10: [{label:'前置条件',color:'#3b82f6',items:['近30日≥15%主升浪','昨收贴MA10±1%','昨缩量<均量×0.8']},{label:'触发条件',color:'#16a34a',items:['盘中最高>昨高×1.025','放量确认≥近10日均量×1.5','累计额≥5亿底线','突破即触发(不卡10点)']},{label:'出场规则',color:'#ea580c',items:['+7%卖半','剩半破MA10×0.98','-6%止损','T+10时停']}],
  BUY_STRONG_START: [{label:'前置条件',color:'#3b82f6',items:['满足弱势极限缩量地量基础']},{label:'触发条件',color:'#16a34a',items:['放量≥近期×2','涨幅≥2%','站上MA10/20','全天额≥10亿(外推)','实时累计额≥5亿(破即触发,不卡10点)','距弱势极限基准涨幅≤10%(挡晚到追高)','逼近涨停板(距板≤1%)不报(追不进)']},{label:'出场规则',color:'#ea580c',items:['+7%卖半','剩半破MA10×0.98','-6%止损','T+10时停']}],
  BUY_WEAK_EXTREME: [{label:'前置条件',color:'#3b82f6',items:['近30日≥15%主升浪','收>MA60>MA20','贴MA10/20±2%','前1日也满足']},{label:'触发条件',color:'#16a34a',items:['今量≤近10日最低×1.1','今量≤均量×0.70','10:00起']},{label:'出场规则',color:'#ea580c',items:['-12%硬止损','T+15清仓','不卖半·纯持有']}],
  BUY_AUCTION_STRENGTH: [{label:'前置条件',color:'#3b82f6',items:['收>MA20>MA60','近20日涨幅≥15%','昨缩量≤均×0.8','昨涨幅∈[-5%,+1%]','收>MA10']},{label:'触发·门控',color:'#16a34a',items:['竞价高开∈[3%,9%]','红盘≥3500或绿盘≥3500','竞价成交额≥5000万','9:26竞价起']},{label:'出场规则',color:'#ea580c',items:['+7%卖半','剩半破MA10×0.98','-6%止损','T+10时停']}],
}
</script>

<template>
<div class="models-page">
<nav class="mp-nav"><div class="mp-nav-hd">买点模型</div><ul><li v-for="m in MODELS" :key="m.id" :data-mid="m.id" :class="{ active: activeId === m.id }" role="button" tabindex="0" @click="scrollTo(m.id)" @keydown.enter="scrollTo(m.id)"><span class="mp-nav-dot" :class="m.side"></span><span class="mp-nav-label">{{ m.name }}</span><span v-if="m.isNew" class="mp-new">新</span></li></ul></nav>

<div class="mp-main">
  <div class="mp-head"><h2>买点模型图鉴</h2><span class="mp-sub">一页看懂每个买点性格、适用、规则、战绩 · 战绩自动更新</span><span class="mp-date" v-if="bt.run_date">全市场回测 {{ bt.run_date }}</span><button class="mp-viz-btn" :class="{ on: showVisual }" @click="showVisual = !showVisual">{{ showVisual ? '📋 详情' : '📊 可视化' }}</button></div>

  <div class="mp-card" v-if="winrateRanked.length"><div class="mp-card-t">🏆 近3月胜率榜<span class="mp-rk-sub">全市场回测 · 真实出场/扣费 · 每日17:30更新<span v-if="winrate.run_date"> · 截至 {{ winrate.run_date }}</span></span></div><div class="mp-tw"><table class="mp-tbl mp-rk"><thead><tr><th>排名</th><th>模型</th><th>近3月<br>胜率</th><th>近3月<br>单笔均收益</th><th class="sep3">近3月<br>样本</th><th class="bg6">近6月<br>胜率</th><th class="bg6">近6月<br>单笔均收益</th><th class="bg6">近6月<br>样本</th><th>对比收益<br>(3月-6月)</th><th>年化效率</th><th>盈利因子</th></tr></thead><tbody><tr v-for="r in winrateRanked" :key="r.signal_id"><td><span class="mp-rank-badge" :class="{ gold: r.rank_3m === 1 }">{{ r.rank_3m }}</span></td><td class="cell-name" @click="scrollTo(r.signal_id)">{{ r.model_name }}</td><td>{{ r.win_rate_3m?.toFixed(1) }}%</td><td :class="(r.net_3m || 0) >= 0 ? 'cell-up' : 'cell-down'">{{ fmtNet(r.net_3m) }}</td><td class="sep3">{{ r.n_3m }}笔</td><td class="bg6">{{ r.win_rate_6m != null ? r.win_rate_6m.toFixed(1) + '%' : '—' }}</td><td class="bg6" :class="(r.net_6m || 0) >= 0 ? 'cell-up' : 'cell-down'">{{ fmtNet(r.net_6m) }}</td><td class="bg6">{{ r.n_6m || 0 }}笔</td><td :class="(r.net_3m||0) >= (r.net_6m||0) ? 'cell-up' : 'cell-down'">{{ fmtDelta3v6(r) }}</td><td class="cell-up"><template v-if="btOf(r.signal_id)">+{{ btOf(r.signal_id)!.annualized }}%</template><span v-else>—</span></td><td><template v-if="btOf(r.signal_id)">{{ btOf(r.signal_id)!.pf }}</template><span v-else>—</span></td></tr></tbody></table></div><p class="mp-sec-foot">全市场回测模型参与排名 · 近3月不含最近约2周触发</p></div>

  <div class="mp-card"><div class="mp-card-t">📊 横评对比(点模型名跳详情)</div><div class="mp-tw"><table class="mp-tbl"><thead><tr><th>模型</th><th>侧</th><th>触发<br>频率</th><th>持有</th><th>适用行情</th><th :title="METRIC_TIPS.annualized">年化效率 ⓘ</th><th :title="METRIC_TIPS.pf">盈利因子 ⓘ</th></tr></thead><tbody><tr v-for="m in MODELS" :key="m.id" @click="scrollTo(m.id)"><td class="cell-name" :class="m.side">{{ m.name }}<span v-if="m.isNew" class="mp-new-tag">新</span><div class="cell-tag">{{ m.tag }}</div></td><td><span class="mp-side-pill" :class="m.side">{{ m.side==='left'?'左侧':'右侧' }}</span></td><td>{{ m.freq }}</td><td class="cell-dim">{{ m.hold }}</td><td class="cell-dim">{{ m.regime }}</td><td class="cell-up"><template v-if="btOf(m.id)">+{{ btOf(m.id)!.annualized }}%</template><span v-else class="cell-na">{{ m.isNew ? '观察中' : '—' }}</span></td><td class="cell-bold"><template v-if="btOf(m.id)">{{ btOf(m.id)!.pf }}</template><span v-else class="cell-na">—</span></td></tr></tbody></table></div><p class="mp-sec-foot">年化效率/盈利因子来自每周六全市场半年回测(各模型各自真实出场口径) · 鼠标移到表头ⓘ看大白话解释</p></div>

  <div v-for="m in MODELS" :key="m.id" :id="'m-' + m.id" class="mp-model-card" :class="m.side">
    <div class="mp-mc-hd"><div class="mp-mc-hd-l"><span class="mp-mc-name">{{ m.name }}</span><span class="mp-side-pill" :class="m.side">{{ m.side==='left'?'左侧':'右侧' }}</span><span class="mp-mc-tag">{{ m.tag }}</span><span v-if="m.isNew" class="mp-new-tag">新</span></div><span class="mp-mc-code">{{ m.id }}</span></div>
    <p class="mp-mc-oneliner">{{ m.oneLine }}</p>

    <div v-if="!showVisual" class="mp-mc-grid">
      <div class="mp-mc-blk"><h4 class="mp-mc-bt">特点</h4><ul><li v-for="(t,i) in m.traits" :key="i">{{ t }}</li></ul></div>
      <div class="mp-mc-blk"><h4 class="mp-mc-bt">适用范围</h4><p>{{ m.scope }}</p></div>
      <div class="mp-mc-blk"><h4 class="mp-mc-bt">注意事项</h4><ul><li v-for="(c,i) in m.caveats" :key="i">{{ c }}</li></ul></div>
      <div class="mp-mc-blk mp-mc-rules"><h4 class="mp-mc-bt">触发规则明细</h4><div class="mp-mc-rule-lines"><p v-if="m.rules.setup"><em>昨日/前置</em>{{ m.rules.setup }}</p><p><em>今日触发</em>{{ m.rules.trigger }}</p><p v-if="m.rules.gate"><em>门控</em>{{ m.rules.gate }}</p><p><em>触发时点</em>{{ m.rules.timing }}</p><p class="mp-mc-exit"><em>出场</em>{{ m.exit }}</p></div></div>
    </div>

    <div v-else class="mp-viz">
      <div class="mp-viz-chart">

        <!-- ═══ 1. 缩量后放量突破 ═══ -->
        <svg v-if="m.id==='BUY_VOL_BREAKOUT'" viewBox="0 0 480 140" class="mp-kline">
          <rect width="480" height="140" fill="#fbfcfd" rx="6"/>
          <line x1="0" y1="30" x2="480" y2="30" stroke="#eef1f5" stroke-width="0.5"/><line x1="0" y1="60" x2="480" y2="60" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="78" y1="0" x2="78" y2="120" stroke="#eef1f5" stroke-width="0.5"/><line x1="228" y1="0" x2="228" y2="120" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="0" y1="100" x2="480" y2="100" stroke="#dde1e6" stroke-width="1"/>
          <!-- MA10 --><line x1="20" y1="50" x2="460" y2="38" stroke="#3b82f6" stroke-width="1.2" stroke-dasharray="5,4" opacity="0.5"/><text x="478" y="36" font-size="8" fill="#3b82f6" opacity="0.7" font-weight="600" text-anchor="end">MA10</text>
          <!-- 昨高虚线 --><line x1="78" y1="46" x2="440" y2="46" stroke="#94a3b8" stroke-width="0.8" stroke-dasharray="3,5"/>
          <!-- 价格线 --><polyline points="30,68 80,66 120,72 180,63 212,62 232,60 290,38 350,33 410,31" fill="none" stroke="#1e293b" stroke-width="1.6"/>
          <!-- 缩量柱 --><rect x="90" y="115" width="110" height="10" fill="#d1d5db" rx="2"/><text x="145" y="130" text-anchor="middle" font-size="9" fill="#94a3b8">缩量 &lt; 均量×0.8</text>
          <!-- 放量柱 --><rect x="240" y="102" width="145" height="25" fill="#ef4444" rx="2"/><text x="312" y="115" text-anchor="middle" font-size="8" fill="#fff" font-weight="700">放量≥昨量×2</text><text x="312" y="125" text-anchor="middle" font-size="8" fill="#fff" font-weight="400">≥均量×1.5</text>
          <!-- 昨高虚线标注 --><text x="478" y="44" font-size="7" fill="#94a3b8" text-anchor="end">昨高</text>
          <!-- 箭头 --><line x1="275" y1="15" x2="275" y2="42" stroke="#ef4444" stroke-width="1.5"/><polygon points="270,20 275,10 280,20" fill="#ef4444"/><text x="305" y="18" font-size="10" fill="#ef4444" font-weight="700">突破昨高×1.02</text>
        </svg>

        <!-- ═══ 2. 中继平台突破 ═══ -->
        <svg v-else-if="m.id==='BUY_PLATFORM_BREAKOUT'" viewBox="0 0 480 140" class="mp-kline">
          <rect width="480" height="140" fill="#fbfcfd" rx="6"/>
          <line x1="0" y1="30" x2="480" y2="30" stroke="#eef1f5" stroke-width="0.5"/><line x1="0" y1="60" x2="480" y2="60" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="0" y1="100" x2="480" y2="100" stroke="#dde1e6" stroke-width="1"/>
          <line x1="20" y1="68" x2="460" y2="68" stroke="#3b82f6" stroke-width="1.2" stroke-dasharray="5,4" opacity="0.5"/><text x="478" y="70" font-size="8" fill="#3b82f6" opacity="0.7" font-weight="600" text-anchor="end">MA20</text>
          <rect x="60" y="40" width="280" height="18" fill="none" stroke="#f59e0b" stroke-width="1.2" stroke-dasharray="6,3" rx="3" opacity="0.7"/><text x="200" y="37" text-anchor="middle" font-size="9" fill="#f59e0b" font-weight="600">多日横盘平台 振幅≤15%</text>
          <polyline points="20,55 60,52 100,48 140,49 180,48 220,53 260,50 300,47 340,48 370,35 400,25 420,22" fill="none" stroke="#1e293b" stroke-width="1.6"/>
          <!-- 量柱从底部向上 --><rect x="58" y="112" width="20" height="8" fill="#d1d5db" rx="1"/><rect x="90" y="113" width="20" height="7" fill="#d1d5db" rx="1"/><rect x="122" y="110" width="20" height="10" fill="#d1d5db" rx="1"/><rect x="154" y="115" width="20" height="5" fill="#d1d5db" rx="1"/><rect x="186" y="112" width="20" height="8" fill="#d1d5db" rx="1"/><rect x="218" y="113" width="20" height="7" fill="#d1d5db" rx="1"/><rect x="250" y="111" width="20" height="9" fill="#d1d5db" rx="1"/><rect x="282" y="116" width="20" height="4" fill="#d1d5db" rx="1"/><rect x="314" y="113" width="20" height="7" fill="#d1d5db" rx="1"/><rect x="346" y="100" width="22" height="22" fill="#ef4444" rx="2"/><text x="357" y="114" text-anchor="middle" font-size="8" fill="#fff" font-weight="700">放量</text>
          <line x1="355" y1="12" x2="355" y2="33" stroke="#ef4444" stroke-width="1.5"/><polygon points="350,17 355,7 360,17" fill="#ef4444"/><text x="478" y="15" font-size="9" fill="#ef4444" font-weight="700" text-anchor="end">收盘≥上沿×1.005</text><text x="478" y="25" font-size="8" fill="#ef4444" text-anchor="end">放量≥均量×1.2</text>
        </svg>

        <!-- ═══ 3. 回踩20MA缩量后突破昨高 ═══ -->
        <svg v-else-if="m.id==='BUY_RALLY_MA20'" viewBox="0 0 480 140" class="mp-kline">
          <rect width="480" height="140" fill="#fbfcfd" rx="6"/>
          <line x1="0" y1="30" x2="480" y2="30" stroke="#eef1f5" stroke-width="0.5"/><line x1="0" y1="60" x2="480" y2="60" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="0" y1="100" x2="480" y2="100" stroke="#dde1e6" stroke-width="1"/>
          <line x1="20" y1="75" x2="460" y2="50" stroke="#3b82f6" stroke-width="1.2" stroke-dasharray="5,4" opacity="0.5"/><text x="478" y="48" font-size="8" fill="#3b82f6" opacity="0.7" font-weight="600" text-anchor="end">MA20</text>
          <polyline points="20,70 60,60 100,48 140,45 180,65 210,72 240,70 270,55 310,42 360,38 400,30 420,28" fill="none" stroke="#1e293b" stroke-width="1.6"/>
          <rect x="32" y="108" width="14" height="12" fill="#d1d5db" rx="1"/><rect x="62" y="105" width="14" height="15" fill="#d1d5db" rx="1"/><rect x="92" y="107" width="14" height="13" fill="#d1d5db" rx="1"/><rect x="122" y="106" width="14" height="14" fill="#d1d5db" rx="1"/><rect x="152" y="112" width="14" height="8" fill="#d1d5db" rx="1"/><rect x="182" y="115" width="14" height="5" fill="#94a3b8" rx="1"/><rect x="212" y="114" width="14" height="6" fill="#d1d5db" rx="1"/><rect x="242" y="104" width="14" height="16" fill="#ef4444" rx="2"/><rect x="272" y="106" width="14" height="14" fill="#ef4444" rx="2"/><rect x="302" y="103" width="14" height="17" fill="#ef4444" rx="2"/><rect x="332" y="105" width="14" height="15" fill="#ef4444" rx="2"/>
          <circle cx="225" cy="71" r="8" fill="none" stroke="#f59e0b" stroke-width="1.5"/><text x="180" y="82" font-size="8" fill="#f59e0b" font-weight="600">回踩MA20±3%</text><text x="180" y="92" font-size="7" fill="#94a3b8">昨日缩量&lt;均量×0.8</text>
          <line x1="280" y1="15" x2="280" y2="40" stroke="#ef4444" stroke-width="1.5"/><polygon points="275,20 280,10 285,20" fill="#ef4444"/><text x="290" y="18" font-size="9" fill="#ef4444" font-weight="700">突破昨高×1.025</text><text x="290" y="28" font-size="7" fill="#ef4444">不卡10点 实时触发</text>
        </svg>

        <!-- ═══ 4. 回踩10MA缩量后突破昨高 ═══ -->
        <svg v-else-if="m.id==='BUY_RALLY_MA10'" viewBox="0 0 480 140" class="mp-kline">
          <rect width="480" height="140" fill="#fbfcfd" rx="6"/>
          <line x1="0" y1="30" x2="480" y2="30" stroke="#eef1f5" stroke-width="0.5"/><line x1="0" y1="60" x2="480" y2="60" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="0" y1="100" x2="480" y2="100" stroke="#dde1e6" stroke-width="1"/>
          <line x1="20" y1="55" x2="460" y2="35" stroke="#3b82f6" stroke-width="1.2" stroke-dasharray="5,4" opacity="0.5"/><text x="478" y="33" font-size="8" fill="#3b82f6" opacity="0.6" text-anchor="end">MA10</text>
          <polyline points="20,58 60,50 100,42 140,38 180,56 210,60 240,55 280,38 330,30 380,24 420,22" fill="none" stroke="#1e293b" stroke-width="1.6"/>
          <rect x="32" y="108" width="14" height="12" fill="#d1d5db" rx="1"/><rect x="62" y="105" width="14" height="15" fill="#d1d5db" rx="1"/><rect x="92" y="106" width="14" height="14" fill="#d1d5db" rx="1"/><rect x="122" y="107" width="14" height="13" fill="#d1d5db" rx="1"/><rect x="152" y="113" width="14" height="7" fill="#94a3b8" rx="1"/><rect x="182" y="116" width="14" height="4" fill="#94a3b8" rx="1"/><rect x="212" y="105" width="14" height="15" fill="#ef4444" rx="2"/><rect x="242" y="102" width="14" height="18" fill="#ef4444" rx="2"/><rect x="272" y="104" width="14" height="16" fill="#ef4444" rx="2"/><rect x="302" y="103" width="14" height="17" fill="#ef4444" rx="2"/>
          <circle cx="200" cy="58" r="8" fill="none" stroke="#f59e0b" stroke-width="1.5"/><text x="155" y="74" font-size="8" fill="#f59e0b" font-weight="600">回踩MA10±1%</text><text x="155" y="84" font-size="7" fill="#94a3b8">昨日缩量&lt;均量×0.8</text>
          <text x="250" y="15" font-size="9" fill="#ef4444" font-weight="700">突破昨高×1.025</text><text x="250" y="25" font-size="7" fill="#ef4444">不卡10点 实时触发</text>
        </svg>

        <!-- ═══ 5. 强势起点 ═══ -->
        <svg v-else-if="m.id==='BUY_STRONG_START'" viewBox="0 0 480 140" class="mp-kline">
          <rect width="480" height="140" fill="#fbfcfd" rx="6"/>
          <line x1="0" y1="30" x2="480" y2="30" stroke="#eef1f5" stroke-width="0.5"/><line x1="0" y1="60" x2="480" y2="60" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="0" y1="100" x2="480" y2="100" stroke="#dde1e6" stroke-width="1"/>
          <line x1="20" y1="70" x2="460" y2="55" stroke="#3b82f6" stroke-width="1.2" stroke-dasharray="5,4" opacity="0.5"/><text x="478" y="53" font-size="8" fill="#3b82f6" opacity="0.7" font-weight="600" text-anchor="end">MA20</text>
          <rect x="20" y="53" width="240" height="28" fill="none" stroke="#94a3b8" stroke-width="0.8" stroke-dasharray="3,4" rx="4" opacity="0.5"/><text x="140" y="48" text-anchor="middle" font-size="8" fill="#94a3b8">缩量地量区(弱势极限前置)</text>
          <polyline points="20,72 60,68 100,78 140,72 180,76 220,70 260,68 280,55 310,38 350,30 390,25 420,22" fill="none" stroke="#1e293b" stroke-width="1.6"/>
          <rect x="32" y="115" width="14" height="5" fill="#d1d5db" rx="1"/><rect x="62" y="116" width="14" height="4" fill="#d1d5db" rx="1"/><rect x="92" y="114" width="14" height="6" fill="#d1d5db" rx="1"/><rect x="122" y="117" width="14" height="3" fill="#d1d5db" rx="1"/><rect x="152" y="115" width="14" height="5" fill="#d1d5db" rx="1"/><rect x="182" y="116" width="14" height="4" fill="#d1d5db" rx="1"/><rect x="212" y="117" width="14" height="3" fill="#94a3b8" rx="1"/><rect x="242" y="115" width="14" height="5" fill="#d1d5db" rx="1"/><rect x="272" y="100" width="18" height="20" fill="#ef4444" rx="2"/><rect x="302" y="96" width="18" height="24" fill="#ef4444" rx="2"/><rect x="332" y="100" width="18" height="20" fill="#ef4444" rx="2"/>
          <line x1="300" y1="12" x2="300" y2="36" stroke="#ef4444" stroke-width="1.5"/><polygon points="295,17 300,7 305,17" fill="#ef4444"/><text x="320" y="15" font-size="8" fill="#ef4444" font-weight="700">涨幅≥2%</text><text x="320" y="25" font-size="7" fill="#ef4444">放量≥近期×2 站上MA10/20</text>
        </svg>

        <!-- ═══ 6. 弱势极限 ═══ -->
        <svg v-else-if="m.id==='BUY_WEAK_EXTREME'" viewBox="0 0 480 140" class="mp-kline">
          <rect width="480" height="140" fill="#fbfcfd" rx="6"/>
          <line x1="0" y1="30" x2="480" y2="30" stroke="#eef1f5" stroke-width="0.5"/><line x1="0" y1="60" x2="480" y2="60" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="0" y1="100" x2="480" y2="100" stroke="#dde1e6" stroke-width="1"/>
          <line x1="20" y1="40" x2="460" y2="52" stroke="#3b82f6" stroke-width="1.2" stroke-dasharray="5,4" opacity="0.5"/><text x="478" y="50" font-size="8" fill="#3b82f6" opacity="0.7" font-weight="600" text-anchor="end">MA20</text><line x1="20" y1="55" x2="460" y2="60" stroke="#3b82f6" stroke-width="0.8" stroke-dasharray="3,5" opacity="0.35"/><text x="478" y="59" font-size="7" fill="#3b82f6" opacity="0.45" text-anchor="end">MA60</text>
          <polyline points="20,35 60,38 100,42 140,45 180,48 220,52 260,55 300,58 340,60 380,62 420,62" fill="none" stroke="#1e293b" stroke-width="1.6"/>
          <rect x="32" y="104" width="14" height="16" fill="#d1d5db" rx="1"/><rect x="62" y="106" width="14" height="14" fill="#d1d5db" rx="1"/><rect x="92" y="107" width="14" height="13" fill="#d1d5db" rx="1"/><rect x="122" y="108" width="14" height="12" fill="#d1d5db" rx="1"/><rect x="152" y="109" width="14" height="11" fill="#d1d5db" rx="1"/><rect x="182" y="110" width="14" height="10" fill="#d1d5db" rx="1"/><rect x="212" y="111" width="14" height="9" fill="#d1d5db" rx="1"/><rect x="242" y="113" width="14" height="7" fill="#d1d5db" rx="1"/><rect x="272" y="116" width="14" height="4" fill="#94a3b8" rx="1"/><rect x="302" y="117" width="14" height="3" fill="#94a3b8" rx="1"/><rect x="332" y="118" width="14" height="2" fill="#94a3b8" rx="1"/>
          <circle cx="350" cy="61" r="12" fill="none" stroke="#1d4ed8" stroke-width="2"/><line x1="350" y1="14" x2="350" y2="47" stroke="#1d4ed8" stroke-width="1.3"/><polygon points="345,19 350,9 355,19" fill="#1d4ed8"/><text x="478" y="15" font-size="9" fill="#1d4ed8" font-weight="700" text-anchor="end">左侧潜伏·地量</text><text x="478" y="26" font-size="7" fill="#1d4ed8" text-anchor="end">今量≤最低×1.1 且≤均量×0.7</text><text x="340" y="78" font-size="8" fill="#94a3b8">量柱递减→</text>
        </svg>

        <!-- ═══ 7. 竞价弱转强 ═══ -->
        <svg v-else-if="m.id==='BUY_AUCTION_STRENGTH'" viewBox="0 0 480 140" class="mp-kline">
          <rect width="480" height="140" fill="#fbfcfd" rx="6"/>
          <line x1="0" y1="30" x2="480" y2="30" stroke="#eef1f5" stroke-width="0.5"/><line x1="0" y1="60" x2="480" y2="60" stroke="#eef1f5" stroke-width="0.5"/>
          <line x1="0" y1="100" x2="480" y2="100" stroke="#dde1e6" stroke-width="1"/>
          <line x1="215" y1="0" x2="215" y2="100" stroke="#7c3aed" stroke-width="1" stroke-dasharray="4,4" opacity="0.4"/><text x="218" y="10" font-size="8" fill="#7c3aed" font-weight="700">9:26</text>
          <rect x="80" y="48" width="120" height="16" fill="#d1d5db" rx="2"/><line x1="140" y1="42" x2="140" y2="70" stroke="#d1d5db" stroke-width="1.5"/><rect x="80" y="58" width="120" height="4" fill="#dbe0e6"/>
          <line x1="205" y1="52" x2="225" y2="35" stroke="#ef4444" stroke-width="2.5"/><line x1="225" y1="35" x2="270" y2="35" stroke="#ef4444" stroke-width="2.5"/><text x="195" y="50" font-size="9" fill="#ef4444" font-weight="700">高开3-9%</text>
          <rect x="230" y="20" width="130" height="32" fill="#ef4444" rx="2"/><line x1="295" y1="18" x2="295" y2="55" stroke="#ef4444" stroke-width="1.5"/>
          <rect x="88" y="112" width="105" height="8" fill="#c4c9d1" rx="1"/><text x="140" y="128" text-anchor="middle" font-size="8" fill="#94a3b8">昨缩量小调</text>
          <rect x="238" y="104" width="118" height="20" fill="#ef4444" rx="2"/><text x="297" y="117" text-anchor="middle" font-size="9" fill="#fff" font-weight="700">竞价放量≥1亿</text>
          <rect x="310" y="82" width="120" height="26" fill="#fef3c7" stroke="#f59e0b" stroke-width="1" rx="4"/><text x="370" y="98" text-anchor="middle" font-size="8" fill="#92400e">红盘≥3500</text><text x="370" y="107" text-anchor="middle" font-size="7" fill="#92400e">或绿盘≥3500</text>
        </svg>

        <div v-else class="mp-viz-chart-na">暂无示意图</div>
      </div>

      <div class="mp-flow" v-if="FLOW_STAGES[m.id]">
        <div v-for="(stage, si) in FLOW_STAGES[m.id]" :key="si" class="mp-flow-stage">
          <div class="mp-flow-head" :style="{ background: stage.color }">{{ stage.label }}</div>
          <div class="mp-flow-body">
            <div v-for="(item, ii) in stage.items" :key="ii" class="mp-flow-card" :style="{ borderLeftColor: stage.color }">
              <span class="mp-flow-card-dot" :style="{ background: stage.color }"></span>
              <span class="mp-flow-card-text">{{ item }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="mp-mc-perf">
      <div class="mp-mc-perf-col"><h4 class="mp-mc-perf-t">全市场半年回测<span>厚样本 · 自动更新</span></h4><div v-if="btOf(m.id)" class="mp-mc-perf-row"><span class="mp-mc-kv">胜率<b>{{ btOf(m.id)!.win_rate }}%</b></span><span class="mp-mc-kv" :title="METRIC_TIPS.annualized">年化效率<b class="up">+{{ btOf(m.id)!.annualized }}%</b></span><span class="mp-mc-kv" :title="METRIC_TIPS.pf">盈利因子<b>{{ btOf(m.id)!.pf }}</b></span><span class="mp-mc-kv" :title="METRIC_TIPS.net_after_cost">单笔净<b class="up">+{{ btOf(m.id)!.net_after_cost }}%</b></span><span class="mp-mc-kv" :title="METRIC_TIPS.avg_eff">资金占用<b>{{ btOf(m.id)!.avg_eff }}天</b></span><span class="mp-mc-kv">样本<b>{{ btOf(m.id)!.n }}笔</b></span></div><div v-else class="mp-mc-perf-na">{{ m.isNew ? '无历史竞价数据,上线后向前验' : loading ? '加载中…' : '本周六首跑后有数据' }}</div></div>
      <div class="mp-mc-perf-col"><h4 class="mp-mc-perf-t">自选真实信号<span>你的实盘口径 · 攒数据中</span></h4><div v-if="outOf(m.id) && outOf(m.id)!.evaluated > 0" class="mp-mc-perf-row"><span class="mp-mc-kv">胜率<b>{{ outOf(m.id)!.success_rate }}%</b></span><span class="mp-mc-kv">5日均收益<b :class="(outOf(m.id)!.avg_p5_pct||0)>=0?'up':'down'">{{ (outOf(m.id)!.avg_p5_pct||0)>=0?'+':'' }}{{ outOf(m.id)!.avg_p5_pct }}%</b></span><span class="mp-mc-kv">已评估<b>{{ outOf(m.id)!.evaluated }}笔</b></span></div><div v-else class="mp-mc-perf-na">样本太少(自选触发后需≥5日回填,攒几周就有)</div></div>
    </div>
  </div>
</div>
</div>
</template>

<style scoped>
.models-page{display:flex;gap:0;min-height:calc(100vh - 56px);background:var(--bg)}
.mp-nav{width:186px;flex-shrink:0;position:sticky;top:0;align-self:flex-start;max-height:100vh;overflow-y:auto;padding:16px 0 20px;background:var(--surface);border-right:1px solid var(--border)}
.mp-nav-hd{display:flex;align-items:center;gap:6px;padding:0 16px 12px;font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.8px}
.mp-nav ul{list-style:none;margin:0;padding:0}
.mp-nav li{display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 16px;font-size:12.5px;color:var(--text2);border-left:3px solid transparent;transition:all .15s;line-height:1.35;margin:1px 0;touch-action:manipulation}
.mp-nav li:hover{background:#f3f5f8;color:var(--text1)}
.mp-nav li.active{background:linear-gradient(90deg,rgba(9,105,218,.06) 0%,transparent 100%);color:var(--primary);font-weight:700;border-left-color:var(--primary)}
.mp-nav-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;background:#b0b8c1}
.mp-nav-dot.left{background:#3b82f6;box-shadow:0 0 0 2px rgba(59,130,246,.15)}.mp-nav-dot.right{background:#ef4444;box-shadow:0 0 0 2px rgba(239,68,68,.15)}
.mp-nav .mp-new{font-size:8px;padding:0 3px;margin-left:auto}
.mp-main{flex:1;min-width:0;max-width:900px;padding:20px 24px 40px;overflow-y:auto}
.mp-head{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:2px 0 10px}
.mp-head h2{margin:0;font-size:17px;color:#1e293b}.mp-sub{font-size:12px;color:#64748b}.mp-date{font-size:11px;color:#94a3b8;margin-left:auto}
.mp-viz-btn{margin-left:8px;padding:4px 14px;font-size:12px;font-weight:600;color:#64748b;background:#f1f5f9;border:1px solid #dde4ed;border-radius:16px;cursor:pointer;transition:all .15s}
.mp-viz-btn:hover{background:#e2e8f0}.mp-viz-btn.on{color:#fff;background:linear-gradient(135deg,#3b82f6,#2563eb);border-color:transparent}
.mp-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px 10px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.mp-card-t{font-weight:700;font-size:13px;color:#1e293b;margin-bottom:6px}.mp-tw{overflow-x:auto}
.mp-tbl{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
.mp-tbl th{background:#f6f8fa;color:var(--text2);font-weight:700;font-size:11px;padding:6px 8px;text-align:center;white-space:nowrap;border-bottom:2px solid var(--border);line-height:1.2}
.mp-tbl td{padding:6px 8px;text-align:center;border-bottom:1px solid #f0f2f5;color:var(--text1);vertical-align:middle}
.mp-tbl tbody tr{cursor:pointer;transition:background .12s;touch-action:manipulation}.mp-tbl tbody tr:hover{background:#f8fafc}
.cell-name{text-align:left!important;font-weight:700}.cell-name.left{color:#1d4ed8}.cell-name.right{color:#b91c1c}
.cell-tag{font-size:10px;color:var(--text2);font-weight:400}.cell-bold{font-weight:700}
.cell-up{color:var(--red);font-weight:700}.cell-down{color:var(--green);font-weight:700}
.cell-dim{color:var(--text2);font-size:11.5px}.cell-na{color:#cbd5e1}
.mp-rank-badge{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:#eff2f5;color:var(--text2);font-weight:800;font-size:12px;font-family:Consolas,monospace}
.mp-rank-badge.gold{background:linear-gradient(135deg,#f59e0b,#ea580c);color:#fff;box-shadow:0 2px 6px rgba(234,88,12,.3)}
.mp-side-pill{display:inline-block;font-size:10.5px;font-weight:600;padding:2px 7px;border-radius:5px;letter-spacing:.3px}
.mp-side-pill.left{color:#1d4ed8;background:#eff3ff}.mp-side-pill.right{color:#b91c1c;background:#fef2f2}
.mp-new-tag{display:inline-block;font-size:9px;color:#fff;background:#7c3aed;border-radius:4px;padding:1px 5px;margin-left:4px;vertical-align:middle;font-weight:600}
.mp-sec-foot{margin:6px 0 0;font-size:10.5px;color:var(--text2);line-height:1.4}
.mp-rk-sub{font-size:10px;font-weight:400;color:#94a3b8;margin-left:6px}
.sep3{border-right:3px solid #d0d5dd!important}.bg6{background:#f8fafc}
.mp-model-card{background:var(--surface);border:1px solid var(--border);border-left:4px solid #d1d5db;border-radius:10px;padding:16px 18px 14px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.04);transition:box-shadow .2s}
.mp-model-card:hover{box-shadow:0 3px 16px rgba(0,0,0,.07)}.mp-model-card.right{border-left-color:#ef4444}.mp-model-card.left{border-left-color:#3b82f6}
.mp-mc-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.mp-mc-hd-l{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mp-mc-name{font-size:16px;font-weight:800;color:var(--text1)}
.mp-mc-tag{font-size:11px;color:var(--text2);background:#f6f8fa;padding:1px 8px;border-radius:4px;font-weight:500}
.mp-mc-code{font-size:10px;color:#c4c9d1;font-family:Consolas,monospace;flex-shrink:0}
.mp-mc-oneliner{margin:6px 0 10px;font-size:13px;color:var(--text1);line-height:1.5;padding:8px 12px;background:#fafbfc;border-radius:6px;border-left:3px solid #e5e7eb}
.mp-mc-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 20px}.mp-mc-rules{grid-column:1/-1}
.mp-mc-bt{font-size:11px;font-weight:700;color:#6d28d9;margin:0 0 4px;text-transform:uppercase;letter-spacing:.5px}
.mp-mc-blk ul{margin:0;padding-left:17px}.mp-mc-blk li{font-size:12.5px;color:var(--text1);line-height:1.55}
.mp-mc-blk p{margin:0 0 2px;font-size:12.5px;color:var(--text1);line-height:1.55}
.mp-mc-rule-lines p{margin:0 0 3px}
.mp-mc-rule-lines em{display:inline-block;font-style:normal;font-size:10.5px;font-weight:600;color:var(--text2);background:#f6f8fa;padding:1px 7px;border-radius:4px;margin-right:6px;min-width:60px;text-align:center}
.mp-mc-exit{color:var(--orange)!important;font-weight:600}.mp-mc-exit em{color:#fff!important;background:var(--orange)!important}
.mp-mc-perf{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;padding-top:12px;border-top:1px solid #f0f2f5}
.mp-mc-perf-col{background:#fafbfc;border-radius:8px;padding:10px 12px}
.mp-mc-perf-t{font-size:11px;font-weight:700;color:var(--text2);margin:0 0 6px}.mp-mc-perf-t span{font-weight:400;color:#9ca3af;margin-left:2px}
.mp-mc-perf-row{display:flex;flex-wrap:wrap;gap:3px 14px;font-size:12.5px;color:var(--text1);font-variant-numeric:tabular-nums}
.mp-mc-kv b{color:var(--text1);font-weight:700}.mp-mc-kv .up{color:var(--red)}.mp-mc-kv .down{color:var(--green)}
.mp-mc-perf-na{font-size:11px;color:#9ca3af;font-style:italic}
.mp-viz{margin:6px 0 2px;max-width:min(100%,780px)}.mp-viz-chart{margin-bottom:10px;border-radius:8px;overflow:hidden;border:1px solid #eef1f5;max-width:min(100%,720px)}
.mp-kline{display:block;width:100%;height:auto;max-width:min(100%,720px)}.mp-viz-chart-na{padding:30px;text-align:center;color:#94a3b8;font-size:12px;background:#fafbfc}
.mp-flow{display:grid;grid-template-columns:repeat(3,minmax(180px,260px));gap:0;align-items:stretch;max-width:min(100%,780px)}
.mp-flow-stage{padding:0 8px;position:relative;display:flex;flex-direction:column}.mp-flow-stage:first-child{padding-left:0}.mp-flow-stage:last-child{padding-right:0}
.mp-flow-body{display:flex;flex-direction:column;gap:4px;flex:1;justify-content:flex-start}
.mp-flow-head{font-size:11px;font-weight:700;color:#fff;text-align:center;padding:4px 0;border-radius:6px;margin-bottom:6px;letter-spacing:.5px}
.mp-flow-card{display:flex;align-items:flex-start;gap:7px;padding:5px 8px 5px 6px;background:#fff;border-left:3px solid;border-radius:0 5px 5px 0;font-size:11px;line-height:1.4;box-shadow:0 1px 2px rgba(0,0,0,.03);transition:box-shadow .12s}
.mp-flow-card:hover{box-shadow:0 2px 6px rgba(0,0,0,.06)}
.mp-flow-card-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;margin-top:4px}
.mp-flow-card-text{flex:1}
.mp-flow-pill{display:none}
.mp-flow-stage:not(:last-child)::after{content:'→';position:absolute;right:-6px;top:12px;font-size:18px;color:#94a3b8;font-weight:700;z-index:1}
@media(max-width:768px){
.models-page{flex-direction:column}
/* 侧边导航 → 顶部横向滚动 chips, 单行不换行不竖排, 固定在内容上方 */
.mp-nav{position:sticky;top:0;z-index:5;width:100%;max-height:none;overflow-y:visible;border-right:none;border-bottom:1px solid var(--border);padding:6px 0}
.mp-nav ul{display:flex;flex-wrap:nowrap;gap:5px;padding:0 10px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.mp-nav ul::-webkit-scrollbar{display:none}
.mp-nav li{flex:0 0 auto;white-space:nowrap;border-left:none!important;border:1px solid var(--border);border-radius:14px;padding:5px 11px;font-size:12px;background:var(--surface);margin:0}
.mp-nav li.active{background:#eff6ff;border-color:var(--primary);color:var(--primary);font-weight:700}
.mp-nav-hd{display:none}
.mp-main{max-width:100%;padding:12px 10px 28px}
/* 表格: 保留 .mp-tw 横滚容器, 首列 sticky 锁定 */
.mp-tbl th:first-child,.mp-tbl td:first-child{position:sticky;left:0;z-index:1;background:#fff}
.mp-tbl thead th:first-child{background:#f6f8fa}
.mp-tbl tbody tr:hover td:first-child{background:#f8fafc}
.mp-mc-grid{grid-template-columns:1fr}
.mp-mc-perf{grid-template-columns:1fr}
.mp-flow{grid-template-columns:1fr;gap:8px}
.mp-flow-stage:not(:last-child)::after{content:'↓';right:50%;transform:translateX(50%);top:auto;bottom:-14px}
.mp-flow-stage:last-child{padding-left:0}
.mp-flow-stage{padding:0}
}
</style>
