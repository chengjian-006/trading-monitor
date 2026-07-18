<script setup lang="ts">
// 仓位 / 风险计算器 (v1.7.668): 按"单笔可承受亏损"反推建议股数, 帮助纪律化仓位管理。
// 纯前端工具, 不落库。默认账户资金可从模拟账户带入(仅取一次, 可手改)。
import { computed, onMounted, ref } from 'vue'
import { NInputNumber } from 'naive-ui'
import { fetchPaperSummary } from '../api/paper-trading'

// ── 输入 ──
const capital = ref(1000000)   // 账户总资金
const riskPct = ref(2)         // 单笔可承受亏损占账户 %
const buyPrice = ref<number | null>(null)
const stopPrice = ref<number | null>(null)
const targetPrice = ref<number | null>(null)

onMounted(async () => {
  // 带入模拟账户总资产作默认(失败静默, 保持默认100万)
  try {
    const s = await fetchPaperSummary('default')
    if (s?.total_equity) capital.value = Math.round(s.total_equity)
  } catch { /* 忽略 */ }
})

const fmt = (v: number | null | undefined, d = 0) =>
  v == null || !Number.isFinite(v) ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })

// ── 计算 ──
const perShareRisk = computed(() => {
  if (buyPrice.value == null || stopPrice.value == null) return null
  const r = buyPrice.value - stopPrice.value
  return r > 0 ? r : null   // 止损须低于买入
})
const stopPct = computed(() =>
  buyPrice.value && perShareRisk.value != null ? (perShareRisk.value / buyPrice.value) * 100 : null)
const riskAmount = computed(() => capital.value * (riskPct.value / 100))   // 可承受亏损额
const rawShares = computed(() =>
  perShareRisk.value ? riskAmount.value / perShareRisk.value : null)
const shares = computed(() =>
  rawShares.value ? Math.floor(rawShares.value / 100) * 100 : null)         // 取整到手(100股)
const cost = computed(() =>
  shares.value != null && buyPrice.value ? shares.value * buyPrice.value : null)
const posPct = computed(() =>
  cost.value != null ? (cost.value / capital.value) * 100 : null)
const maxLoss = computed(() =>
  shares.value != null && perShareRisk.value != null ? shares.value * perShareRisk.value : null)

// 盈亏测算(可选目标价)
const gainPerShare = computed(() =>
  targetPrice.value != null && buyPrice.value != null ? targetPrice.value - buyPrice.value : null)
const gainAmount = computed(() =>
  gainPerShare.value != null && shares.value != null ? gainPerShare.value * shares.value : null)
const gainPct = computed(() =>
  gainPerShare.value != null && buyPrice.value ? (gainPerShare.value / buyPrice.value) * 100 : null)
const rr = computed(() =>   // 盈亏比
  gainPerShare.value != null && perShareRisk.value ? gainPerShare.value / perShareRisk.value : null)

const filled = computed(() => buyPrice.value != null && stopPrice.value != null)
</script>

<template>
  <div class="calc-view">
    <div class="calc-head">
      <h2>仓位 / 风险计算器</h2>
      <span class="sub">按「单笔可承受亏损」反推建议股数 · 纪律化仓位</span>
    </div>

    <div class="calc-grid">
      <!-- 输入 -->
      <div class="pnl">
        <div class="pnl-head"><span class="tt">输入</span></div>
        <div class="pnl-body">
          <div class="fld"><label>账户总资金</label><NInputNumber v-model:value="capital" :min="0" :step="10000" style="width:100%">
            <template #prefix>¥</template></NInputNumber></div>
          <div class="fld"><label>单笔可承受亏损（占账户）</label><NInputNumber v-model:value="riskPct" :min="0.1" :max="100" :step="0.5" style="width:100%">
            <template #suffix>%</template></NInputNumber>
            <span class="fld-hint">可承受亏损额 ¥{{ fmt(riskAmount) }}（{{ riskPct }}%）· 常用 1–2%</span></div>
          <div class="fld"><label>买入价</label><NInputNumber v-model:value="buyPrice" :min="0" :step="0.01" placeholder="必填" style="width:100%">
            <template #prefix>¥</template></NInputNumber></div>
          <div class="fld"><label>止损价</label><NInputNumber v-model:value="stopPrice" :min="0" :step="0.01" placeholder="必填 · 须低于买入价" style="width:100%">
            <template #prefix>¥</template></NInputNumber>
            <span v-if="stopPct != null" class="fld-hint">止损幅度 <b class="down">-{{ fmt(stopPct, 2) }}%</b> · 每股风险 ¥{{ fmt(perShareRisk, 2) }}</span>
            <span v-else-if="buyPrice != null && stopPrice != null" class="fld-hint err">止损价须低于买入价</span></div>
          <div class="fld"><label>目标价（可选，算盈亏比）</label><NInputNumber v-model:value="targetPrice" :min="0" :step="0.01" placeholder="选填" style="width:100%">
            <template #prefix>¥</template></NInputNumber></div>
        </div>
      </div>

      <!-- 结果 -->
      <div class="pnl">
        <div class="pnl-head"><span class="tt">建议</span></div>
        <div class="pnl-body">
          <div v-if="!filled || perShareRisk == null" class="calc-empty">填入买入价 + 止损价（止损低于买入）即可算出建议仓位。</div>
          <template v-else>
            <div class="hero-num">
              <div class="hn-lbl">建议买入</div>
              <div class="hn-val">{{ fmt(shares) }} <small>股</small></div>
              <div class="hn-sub">≈ {{ shares != null ? (shares / 100) : '—' }} 手</div>
            </div>
            <div class="kpi2">
              <div class="kc"><div class="l">占用资金</div><div class="v">¥{{ fmt(cost) }}</div></div>
              <div class="kc"><div class="l">仓位占比</div><div class="v">{{ fmt(posPct, 1) }}%</div></div>
              <div class="kc"><div class="l">实际最大亏损</div><div class="v down">-¥{{ fmt(maxLoss) }}</div></div>
              <div class="kc"><div class="l">止损幅度</div><div class="v down">-{{ fmt(stopPct, 2) }}%</div></div>
            </div>

            <div v-if="gainAmount != null" class="tp-block">
              <div class="tp-h">若到目标价 ¥{{ fmt(targetPrice, 2) }}</div>
              <div class="kpi2">
                <div class="kc"><div class="l">预计盈利</div><div class="v up">+¥{{ fmt(gainAmount) }}</div></div>
                <div class="kc"><div class="l">盈利幅度</div><div class="v up">+{{ fmt(gainPct, 2) }}%</div></div>
                <div class="kc"><div class="l">盈亏比 R:R</div><div class="v" :class="(rr ?? 0) >= 2 ? 'up' : ''">{{ fmt(rr, 2) }} : 1</div></div>
                <div class="kc"><div class="l">评价</div><div class="v" :style="{ fontSize: '14px' }">{{ (rr ?? 0) >= 2 ? '盈亏比理想' : (rr ?? 0) >= 1 ? '一般' : '盈亏比偏低' }}</div></div>
              </div>
            </div>
            <p class="calc-foot">口径：建议股数 = 可承受亏损额 ÷ 每股风险，向下取整到 100 股。仓位管理仅供参考，实盘请结合个股流动性与个人风险偏好。</p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.calc-view { max-width: 900px; margin: 0 auto; }
.calc-head { margin-bottom: 14px; }
.calc-head h2 { margin: 0; font-size: 18px; }
.calc-head .sub { font-size: 12px; color: var(--fg-subtle); }
.calc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; }

.pnl { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 6px; overflow: hidden; }
.pnl-head { height: 32px; background: var(--bg-head); border-bottom: 1px solid var(--border-default); display: flex; align-items: center; gap: 8px; padding: 0 12px; }
.pnl-head .tt { font-size: 12px; font-weight: 600; letter-spacing: .02em; }
.pnl-head .mlbl { font-size: 10px; color: var(--fg-subtle); text-transform: uppercase; letter-spacing: .06em; }
.pnl-body { padding: 14px; }

.fld { display: flex; flex-direction: column; gap: 5px; margin-bottom: 14px; }
.fld:last-child { margin-bottom: 0; }
.fld label { font-size: 12px; color: var(--fg-muted); font-weight: 500; }
.fld-hint { font-size: 11px; color: var(--fg-subtle); }
.fld-hint.err { color: var(--danger-fg); }
.fld-hint b { font-family: var(--font-mono); }

.calc-empty { color: var(--fg-subtle); font-size: 13px; text-align: center; padding: 30px 16px; line-height: 1.6; }
.hero-num { text-align: center; padding: 8px 0 14px; border-bottom: 1px solid var(--border-muted); margin-bottom: 14px; }
.hn-lbl { font-size: 11px; color: var(--fg-subtle); text-transform: uppercase; letter-spacing: .06em; }
.hn-val { font-family: var(--font-mono); font-size: 40px; font-weight: 700; line-height: 1.1; letter-spacing: -.02em; color: var(--accent-fg); }
.hn-val small { font-size: 16px; color: var(--fg-muted); font-weight: 500; }
.hn-sub { font-size: 12px; color: var(--fg-subtle); font-family: var(--font-mono); }

.kpi2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--border-muted); border: 1px solid var(--border-muted); border-radius: 4px; overflow: hidden; }
.kc { background: var(--bg-surface); padding: 9px 11px; }
.kc .l { font-size: 10px; color: var(--fg-subtle); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 3px; }
.kc .v { font-family: var(--font-mono); font-size: 17px; font-weight: 700; }
.up { color: var(--up-fg); } .down { color: var(--down-fg); }

.tp-block { margin-top: 14px; }
.tp-h { font-size: 12px; font-weight: 600; color: var(--fg-muted); margin-bottom: 8px; }
.calc-foot { margin-top: 14px; font-size: 11px; color: var(--fg-subtle); line-height: 1.6; }

@media (max-width: 768px) {
  .calc-grid { grid-template-columns: 1fr; }
}
</style>
