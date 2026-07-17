<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { NButton, NIcon, NSkeleton } from 'naive-ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { formatYi } from '../utils/formatAmount'
import { SearchOutline, SparklesOutline, RefreshOutline, FlameOutline } from '@vicons/ionicons5'
import {
  fetchPopularity, fetchPopularityDates, analyzeStockAi,
  type PopularityStock, type HotSector,
} from '../api/popularity'

const message = useGlobalMessage()
const stocks = ref<PopularityStock[]>([])
const hotIndustries = ref<HotSector[]>([])
const hotConcepts = ref<HotSector[]>([])
const loading = ref(false)
const dates = ref<string[]>([])
const activeDate = ref('')
const updatedAt = ref('')

function pctClass(pct: number | undefined) {
  if (pct == null) return ''
  return pct >= 0 ? 'up' : 'down'
}

function pctText(pct: number | undefined) {
  if (pct == null) return '-'
  return pct >= 0 ? `+${pct.toFixed(2)}%` : `${pct.toFixed(2)}%`
}

function formatAmount(val: number) {
  if (!val) return '-'
  return formatYi(val)   // 成交额统一亿(2位)
}

function rankChangeText(val: number) {
  if (val > 0) return `↑${val}`
  if (val < 0) return `↓${Math.abs(val)}`
  return '-'
}

function rankChangeClass(val: number) {
  if (val > 0) return 'up'
  if (val < 0) return 'down'
  return ''
}

function formatAiAnalysis(text: string) {
  if (!text) return ''
  // v1.7.x: 后端实际产出 【催化剂】【持续性】【资金】【建议】4 段, 旧逻辑只匹配【操作建议】所以从未生效
  // 通用做法: 非首个的所有【xxx】前加 <br>, 让每段单独一行
  const parts = text.split('【')
  return parts[0] + parts.slice(1).map(p => '<br>【' + p).join('')
}

function formatRefreshTime(raw: string) {
  // 'YYYY-MM-DD HH:MM:SS' → 同日只显示 'HH:MM', 跨日显示 'MM-DD HH:MM'
  if (!raw || raw.length < 16) return raw || ''
  const today = new Date().toISOString().slice(0, 10)
  const ymd = raw.slice(0, 10)
  return ymd === today ? raw.slice(11, 16) : raw.slice(5, 16)
}

function formatDateLabel(dateStr: string) {
  const d = new Date(dateStr)
  const weekdays = ['日', '一', '二', '三', '四', '五', '六']
  const m = d.getMonth() + 1
  const day = d.getDate()
  const wd = weekdays[d.getDay()]
  const today = new Date().toISOString().slice(0, 10)
  if (dateStr === today) return `今天 ${m}/${day}`
  return `周${wd} ${m}/${day}`
}

// ── 趋势箭头(5日) ──
function trendArrow(pct: number | undefined) {
  if (pct == null) return '→'
  if (pct > 1.5) return '↗'
  if (pct < -1.5) return '↘'
  return '→'
}
function trendClass(pct: number | undefined) {
  if (pct == null) return 'flat'
  if (pct > 1.5) return 'up'
  if (pct < -1.5) return 'down'
  return 'flat'
}

// ──────────────────────────────────────────────────────────
// 题材主线分组 (纯前端重组现有数据)
// ──────────────────────────────────────────────────────────
interface ConceptGroup {
  key: string            // 题材名(锚点 key); '__other__' = 其他组
  name: string
  isOther: boolean
  rankIdx: number        // 在 hot_concepts 中的排名(其他组放最后)
  count: number          // 实际归入本组的家数
  pctToday: number       // 题材当日均涨(取 HotSector.pct_today; 其他组用组内均值)
  pct5day: number
  leaderName: string     // 龙头(组内人气 rank 最靠前)
  members: PopularityStock[]
}

// 把每只股归到它"最强"的热门题材(hot_concepts 中排名最靠前且出现在该股 concepts 里)
const conceptGroups = computed<ConceptGroup[]>(() => {
  const concepts = hotConcepts.value
  // 名次映射: 题材名 → 在 hot_concepts 的 index
  const order = new Map<string, number>()
  concepts.forEach((c, i) => order.set(c.name, i))

  // 每个题材一个收集桶
  const buckets = new Map<string, PopularityStock[]>()
  concepts.forEach(c => buckets.set(c.name, []))
  const other: PopularityStock[] = []

  for (const s of stocks.value) {
    const myConcepts = s.concepts || []
    let bestName = ''
    let bestIdx = Infinity
    for (const cn of myConcepts) {
      // 名字相等优先; 退而求其次用包含匹配(题材名是股票概念串的子串, 或反之)
      let idx = order.has(cn) ? order.get(cn)! : Infinity
      if (idx === Infinity) {
        for (const [hc, hi] of order) {
          if (cn === hc || cn.includes(hc) || hc.includes(cn)) {
            if (hi < idx) idx = hi
          }
        }
      }
      if (idx < bestIdx) {
        bestIdx = idx
        bestName = concepts[idx]?.name || ''
      }
    }
    if (bestName && buckets.has(bestName)) buckets.get(bestName)!.push(s)
    else other.push(s)
  }

  const groups: ConceptGroup[] = []
  concepts.forEach((c, i) => {
    const members = buckets.get(c.name) || []
    if (!members.length) return
    members.sort((a, b) => (b.amount || 0) - (a.amount || 0))
    // 龙头: 组内人气 rank 最靠前(数值最小)
    const leader = members.reduce((p, q) => (q.rank < p.rank ? q : p), members[0])
    groups.push({
      key: c.name,
      name: c.name,
      isOther: false,
      rankIdx: i,
      count: members.length,
      pctToday: c.pct_today,
      pct5day: c.pct_5day,
      leaderName: leader?.name || leader?.code || '',
      members,
    })
  })

  if (other.length) {
    other.sort((a, b) => (b.amount || 0) - (a.amount || 0))
    const avg = other.reduce((s, x) => s + (x.pct_change || 0), 0) / other.length
    const leader = other.reduce((p, q) => (q.rank < p.rank ? q : p), other[0])
    groups.push({
      key: '__other__',
      name: '其他',
      isOther: true,
      rankIdx: 9999,
      count: other.length,
      pctToday: avg,
      pct5day: 0,
      leaderName: leader?.name || leader?.code || '',
      members: other,
    })
  }

  return groups
})

// 主线强度总览: 只取真正有归入成员的题材(剔除空组), 按家数排
const overviewBars = computed(() => {
  return conceptGroups.value
    .filter(g => !g.isOther)
    .map(g => ({ key: g.key, name: g.name, count: g.count, pctToday: g.pctToday }))
})

// 归一化基准
const maxConceptCount = computed(() =>
  Math.max(1, ...overviewBars.value.map(b => b.count)),
)
// 全列表最大成交额(资金条跨题材可比)
const maxAmount = computed(() =>
  Math.max(1, ...stocks.value.map(s => s.amount || 0)),
)

// 条长(家数归一) → 百分比
function barWidth(count: number) {
  return `${Math.max(8, (count / maxConceptCount.value) * 100)}%`
}
// 资金条宽(成交额归一, 全列表最大)
function moneyWidth(amount: number) {
  return `${Math.max(3, ((amount || 0) / maxAmount.value) * 100)}%`
}
// 题材热力: 当日均涨 → 红色深浅 (0~+8% → 0.12~0.92 不透明度), 下跌走绿
function heatStyle(pct: number) {
  if (pct >= 0) {
    const a = Math.min(0.92, 0.14 + (pct / 8) * 0.78)
    return { background: `linear-gradient(90deg, rgba(207,34,46,${a.toFixed(3)}), rgba(207,34,46,${(a * 0.6).toFixed(3)}))` }
  }
  const a = Math.min(0.85, 0.14 + (Math.abs(pct) / 8) * 0.7)
  return { background: `linear-gradient(90deg, rgba(26,127,55,${a.toFixed(3)}), rgba(26,127,55,${(a * 0.6).toFixed(3)}))` }
}
// 资金条配色: 跟涨跌(红涨绿跌), 强度恒定
function moneyStyle(pct: number | undefined) {
  if (pct != null && pct < 0) {
    return { background: 'linear-gradient(90deg, rgba(26,127,55,0.85), rgba(26,127,55,0.45))' }
  }
  return { background: 'linear-gradient(90deg, rgba(207,34,46,0.85), rgba(207,34,46,0.42))' }
}

async function loadData(refresh = false) {
  loading.value = true
  try {
    const date = activeDate.value || undefined
    const result = await fetchPopularity(refresh, date)
    stocks.value = result.stocks
    hotIndustries.value = result.hot_industries || []
    hotConcepts.value = result.hot_concepts || []
    updatedAt.value = result.updated_at || ''
    if (refresh) {
      const newDates = await fetchPopularityDates()
      if (newDates.length) {
        dates.value = newDates
        if (!activeDate.value) activeDate.value = newDates[0]
      }
    }
  } catch {
    message.error('加载人气数据失败')
  } finally {
    loading.value = false
  }
}

async function switchDate(date: string) {
  if (date === activeDate.value) return
  activeDate.value = date
  await loadData()
}

// 题材分组折叠态 (默认全展开)
const collapsedGroups = ref<Record<string, boolean>>({})
function toggleGroup(key: string) {
  collapsedGroups.value[key] = !collapsedGroups.value[key]
}

// 个股明细展开态 (Record<code, bool>)
const openStocks = ref<Record<string, boolean>>({})
function toggleStock(code: string) {
  openStocks.value[code] = !openStocks.value[code]
}

// 总览热力条 → 滚动到对应题材分组 + 确保展开
async function jumpToGroup(key: string) {
  collapsedGroups.value[key] = false
  await nextTick()
  const el = document.getElementById('grp-' + key)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    el.classList.add('grp-flash')
    setTimeout(() => el.classList.remove('grp-flash'), 1200)
  }
}

const aiLoading = ref<Record<string, boolean>>({})
async function runAiAnalyze(stock: PopularityStock) {
  if (aiLoading.value[stock.code]) return
  aiLoading.value[stock.code] = true
  try {
    const res = await analyzeStockAi(stock.code, activeDate.value || undefined)
    if (res.ai_analysis) {
      stock.ai_analysis = res.ai_analysis
      stock.ai_analysis_at = res.ai_analysis_at
      message.success(`${stock.name || stock.code} AI 解读已生成`)
    } else {
      message.error('AI 返回为空')
    }
  } catch {
    message.error('AI 解读失败')
  } finally {
    aiLoading.value[stock.code] = false
  }
}

onMounted(async () => {
  try {
    const ds = await fetchPopularityDates()
    dates.value = ds
    if (ds.length) activeDate.value = ds[0]
  } catch { /* ignore */ }
  await loadData()
})
</script>

<template>
  <div class="pop-view">
    <div class="page-header">
      <div class="page-header-left">
        <span class="page-title">热门板块 / 个股梳理</span>
        <span v-if="updatedAt" class="updated-time">数据更新于 {{ updatedAt.slice(11, 19) }}</span>
      </div>
      <div class="page-header-right">
        <div v-if="dates.length" class="date-tabs">
          <div
            v-for="d in dates"
            :key="d"
            class="date-tab"
            :class="{ active: d === activeDate }"
            role="button"
            tabindex="0"
            @click="switchDate(d)"
            @keydown.enter="switchDate(d)"
          >
            {{ formatDateLabel(d) }}
          </div>
        </div>
        <NButton type="primary" size="small" :loading="loading" @click="loadData(true)">
          <template #icon><NIcon><SearchOutline /></NIcon></template>
          刷新
        </NButton>
      </div>
    </div>

    <!-- ① 主线强度总览 热力条 -->
    <div v-if="overviewBars.length" class="overview-card">
      <div class="overview-head">
        <NIcon :component="FlameOutline" :size="15" class="overview-flame" />
        <span class="overview-title">主线强度总览</span>
        <span class="overview-sub">条长=上榜家数 · 颜色深浅=当日均涨 · 点击直达分组</span>
      </div>
      <div class="heat-rail">
        <button
          v-for="b in overviewBars"
          :key="b.key"
          class="heat-bar-row"
          type="button"
          :aria-label="b.name + ' 跳转'"
          @click="jumpToGroup(b.key)"
        >
          <span class="heat-name">{{ b.name }}</span>
          <span class="heat-track">
            <span class="heat-fill" :style="[{ width: barWidth(b.count) }, heatStyle(b.pctToday)]"></span>
          </span>
          <span class="heat-count">{{ b.count }}只</span>
          <span class="heat-pct" :class="pctClass(b.pctToday)">{{ pctText(b.pctToday) }}</span>
        </button>
      </div>
    </div>

    <NSkeleton v-if="loading && stocks.length === 0" :repeat="5" text style="margin-bottom: 16px" />

    <!-- ② 题材分组区 -->
    <Transition v-else name="content-fade" appear>
      <div class="groups-wrap">
        <section
          v-for="g in conceptGroups"
          :id="'grp-' + g.key"
          :key="g.key"
          class="grp"
          :class="{ other: g.isOther }"
        >
          <header
            class="grp-head"
            role="button"
            tabindex="0"
            :aria-expanded="!collapsedGroups[g.key]"
            @click="toggleGroup(g.key)"
            @keydown.enter="toggleGroup(g.key)"
          >
            <span class="grp-caret" :class="{ collapsed: collapsedGroups[g.key] }">▾</span>
            <span class="grp-name">{{ g.name }}</span>
            <span class="grp-count">{{ g.count }}只</span>
            <span class="grp-today" :class="pctClass(g.pctToday)">今{{ pctText(g.pctToday) }}</span>
            <span v-if="!g.isOther" class="grp-trend" :class="trendClass(g.pct5day)">
              5日{{ trendArrow(g.pct5day) }} {{ pctText(g.pct5day) }}
            </span>
            <span v-if="g.leaderName" class="grp-leader">龙头 <b>★{{ g.leaderName }}</b></span>
          </header>

          <Transition name="grp-body">
            <div v-show="!collapsedGroups[g.key]" class="grp-body">
              <div
                v-for="s in g.members"
                :key="s.code"
                class="srow"
                :class="{ leader: s.name === g.leaderName, open: openStocks[s.code] }"
              >
                <!-- 紧凑强弱行 -->
                <div
                  class="srow-main"
                  role="button"
                  tabindex="0"
                  :aria-expanded="!!openStocks[s.code]"
                  @click="toggleStock(s.code)"
                  @keydown.enter="toggleStock(s.code)"
                >
                  <span class="s-name">
                    <span v-if="s.name === g.leaderName" class="s-star">★</span>{{ s.name || '-' }}
                  </span>
                  <span class="s-code">{{ s.code }}</span>
                  <span class="s-pct" :class="pctClass(s.pct_change)">{{ pctText(s.pct_change) }}</span>

                  <span class="s-money">
                    <span class="money-track">
                      <span class="money-fill" :style="[{ width: moneyWidth(s.amount) }, moneyStyle(s.pct_change)]"></span>
                    </span>
                    <span class="money-val">{{ formatAmount(s.amount) }}</span>
                  </span>

                  <span v-if="s.turnover" class="s-turn">换手 {{ s.turnover.toFixed(1) }}%</span>
                  <span v-if="s.industry" class="s-ind">{{ s.industry }}</span>
                  <span
                    v-if="s.rank_change"
                    class="s-rc"
                    :class="rankChangeClass(s.rank_change)"
                  >{{ rankChangeText(s.rank_change) }}</span>
                  <span class="s-exp" :class="{ open: openStocks[s.code] }">▾</span>
                </div>

                <!-- ③ 个股展开明细 -->
                <Transition name="detail">
                  <div v-show="openStocks[s.code]" class="srow-detail">
                    <div v-if="s.hot_reason?.length" class="d-block">
                      <div class="d-label">异动原因</div>
                      <div class="d-reason">
                        <div v-for="(r, i) in s.hot_reason" :key="i" class="reason-line">
                          {{ s.hot_reason.length > 1 ? '· ' : '' }}{{ r }}
                        </div>
                      </div>
                    </div>

                    <div class="d-block d-ai">
                      <div class="d-ai-head">
                        <span class="d-label ai-label">AI 解读</span>
                        <NButton
                          size="tiny"
                          text
                          type="primary"
                          :loading="aiLoading[s.code]"
                          @click="runAiAnalyze(s)"
                        >
                          <template #icon>
                            <NIcon :component="s.ai_analysis ? RefreshOutline : SparklesOutline" />
                          </template>
                          {{ s.ai_analysis ? '重新生成' : 'AI 解读' }}
                        </NButton>
                      </div>
                      <div v-if="s.ai_analysis" class="ai-block">
                        <div v-if="s.ai_analysis_at" class="ai-refresh-tag">
                          🕐 {{ formatRefreshTime(s.ai_analysis_at) }} 生成
                        </div>
                        <div class="ai-content" v-html="formatAiAnalysis(s.ai_analysis)"></div>
                      </div>
                      <div v-else class="ai-empty">点上面按钮生成针对该股的催化剂/持续性/资金/操作建议（9:00~22:00 每小时自动全量刷新）</div>
                    </div>
                  </div>
                </Transition>
              </div>
            </div>
          </Transition>
        </section>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.pop-view {
  --pop-line: rgba(31, 35, 40, 0.08);
}

/* 页面顶部 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 10px;
}
.page-header-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.updated-time {
  font-size: 12px;
  color: var(--text2);
  opacity: 0.75;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}
.page-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text1);
  letter-spacing: 0.2px;
}
.page-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 日期标签 */
.date-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px 4px;
  background: var(--surface);
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
.date-tab {
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text2);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  touch-action: manipulation;
  font-variant-numeric: tabular-nums;
}
.date-tab:hover { background: rgba(0, 0, 0, 0.04); }
.date-tab.active {
  background: var(--primary);
  color: var(--bg-surface);
  font-weight: 600;
}

/* ── ① 主线强度总览 ── */
.overview-card {
  position: relative;
  background:
    radial-gradient(120% 140% at 0% 0%, rgba(207, 34, 46, 0.05), transparent 55%),
    var(--surface);
  border: 1px solid var(--pop-line);
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  padding: 16px 18px 14px;
  margin-bottom: 18px;
}
.overview-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.overview-flame { color: var(--red); }
.overview-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text1);
  letter-spacing: 0.3px;
}
.overview-sub {
  font-size: 11px;
  color: var(--text2);
  opacity: 0.7;
}
.heat-rail {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.heat-bar-row {
  display: grid;
  grid-template-columns: 84px 1fr 44px 60px;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 4px 6px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
  transition: background 0.18s;
  touch-action: manipulation;
}
.heat-bar-row:hover { background: rgba(9, 105, 218, 0.05); }
.heat-name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.heat-track {
  position: relative;
  height: 16px;
  background: rgba(31, 35, 40, 0.05);
  border-radius: 4px;
  overflow: hidden;
}
.heat-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 4px;
  transition: width 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}
.heat-count {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text2);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.heat-pct {
  font-size: 12px;
  font-weight: 700;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.heat-pct.up { color: var(--red); }
.heat-pct.down { color: var(--green); }

/* ── ② 题材分组 ── */
.groups-wrap {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.grp {
  background: var(--surface);
  border: 1px solid var(--pop-line);
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  overflow: hidden;
  scroll-margin-top: 12px;
  transition: box-shadow 0.25s, border-color 0.25s;
}
.grp.grp-flash {
  border-color: rgba(207, 34, 46, 0.5);
  box-shadow: 0 0 0 3px rgba(207, 34, 46, 0.12);
}
.grp.other { opacity: 0.96; }

.grp-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 16px;
  cursor: pointer;
  flex-wrap: wrap;
  user-select: none;
  border-bottom: 1px solid transparent;
  background: linear-gradient(90deg, rgba(207, 34, 46, 0.035), transparent 40%);
  transition: background 0.2s;
}
.grp.other .grp-head { background: rgba(31, 35, 40, 0.02); }
.grp-head:hover { background: linear-gradient(90deg, rgba(207, 34, 46, 0.06), transparent 45%); }
.grp-caret {
  font-size: 12px;
  color: var(--text2);
  transition: transform 0.22s;
}
.grp-caret.collapsed { transform: rotate(-90deg); }
.grp-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--text1);
  letter-spacing: 0.2px;
}
.grp-count {
  font-size: 12px;
  font-weight: 700;
  color: var(--bg-surface);
  background: var(--red);
  padding: 1px 7px;
  border-radius: 10px;
  font-variant-numeric: tabular-nums;
}
.grp.other .grp-count { background: var(--text2); }
.grp-today {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.grp-today.up { color: var(--red); }
.grp-today.down { color: var(--green); }
.grp-trend {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  padding: 1px 8px;
  border-radius: 4px;
  background: rgba(31, 35, 40, 0.04);
  color: var(--text2);
}
.grp-trend.up { color: var(--red); background: var(--red-bg); }
.grp-trend.down { color: var(--green); background: var(--green-bg); }
.grp-leader {
  margin-left: auto;
  font-size: 12px;
  color: var(--text2);
}
.grp-leader b { color: var(--red); font-weight: 700; }

/* 题材 body 折叠过渡 */
.grp-body-enter-active,
.grp-body-leave-active { transition: opacity 0.2s ease; }
.grp-body-enter-from,
.grp-body-leave-to { opacity: 0; }

/* ── 紧凑强弱行 ── */
.srow { border-top: 1px solid var(--pop-line); }
.srow:first-child { border-top: none; }
.srow.leader { background: linear-gradient(90deg, rgba(207, 34, 46, 0.045), transparent 50%); }
.srow.open { background: rgba(9, 105, 218, 0.025); }

.srow-main {
  display: grid;
  grid-template-columns:
    minmax(96px, 1.3fr)   /* 名称 */
    64px                  /* 代码 */
    66px                  /* 涨跌 */
    minmax(110px, 1.6fr)  /* 资金条 */
    72px                  /* 换手 */
    minmax(56px, 0.8fr)   /* 行业 */
    32px                  /* rank_change */
    18px;                 /* 展开箭头 */
  align-items: center;
  gap: 10px;
  padding: 9px 16px;
  cursor: pointer;
  touch-action: manipulation;
  transition: background 0.15s;
}
.srow-main:hover { background: rgba(9, 105, 218, 0.04); }

.s-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: flex;
  align-items: center;
  gap: 2px;
}
.s-star { color: var(--red); font-size: 12px; }
.s-code {
  font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: 11.5px;
  color: var(--text2);
  opacity: 0.85;
  font-variant-numeric: tabular-nums;
}
.s-pct {
  font-size: 14px;
  font-weight: 700;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.s-pct.up { color: var(--red); }
.s-pct.down { color: var(--green); }

.s-money {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.money-track {
  position: relative;
  flex: 1;
  height: 9px;
  min-width: 40px;
  background: rgba(31, 35, 40, 0.06);
  border-radius: 5px;
  overflow: hidden;
}
.money-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 5px;
  transition: width 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}
.money-val {
  font-size: 12px;
  font-weight: 600;
  color: var(--text1);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.s-turn,
.s-ind {
  font-size: 11.5px;
  color: var(--text2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
}
.s-ind {
  justify-self: start;
  background: rgba(31, 35, 40, 0.045);
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 11px;
  max-width: 100%;
}
.s-rc {
  font-size: 11.5px;
  font-weight: 600;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.s-rc.up { color: var(--red); }
.s-rc.down { color: var(--green); }
.s-exp {
  font-size: 11px;
  color: var(--text2);
  opacity: 0.5;
  text-align: center;
  transition: transform 0.22s;
}
.s-exp.open { transform: rotate(180deg); opacity: 0.9; }

/* ── ③ 个股展开明细 ── */
.detail-enter-active,
.detail-leave-active { transition: opacity 0.22s ease; }
.detail-enter-from,
.detail-leave-to { opacity: 0; }

.srow-detail {
  padding: 4px 16px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.d-block {
  background: rgba(31, 35, 40, 0.02);
  border-left: 2px solid rgba(31, 35, 40, 0.12);
  border-radius: 0 6px 6px 0;
  padding: 8px 12px;
}
.d-block.d-ai { border-left-color: var(--primary); }
.d-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--text2);
  margin-bottom: 5px;
  letter-spacing: 0.3px;
}
.ai-label { color: var(--primary); }
.d-reason {
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text2);
}
.reason-line { word-break: break-word; }

.d-ai-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 5px;
  gap: 8px;
}
.ai-block {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.ai-refresh-tag {
  font-size: 11px;
  color: var(--text2);
  opacity: 0.85;
  font-family: ui-monospace, monospace;
  letter-spacing: 0.3px;
  padding: 2px 8px;
  background: rgba(9, 105, 218, 0.06);
  border-left: 2px solid var(--primary);
  border-radius: 2px;
  align-self: flex-start;
}
.ai-content {
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text1);
}
.ai-empty {
  font-size: 12px;
  color: var(--text2);
  opacity: 0.7;
  font-style: italic;
  line-height: 1.5;
}

/* 首屏渐显 */
.content-fade-enter-active { transition: opacity 0.4s ease; }
.content-fade-enter-from { opacity: 0; }

/* ── 移动端适配 (<=768px) ── */
@media (max-width: 768px) {
  .page-header-right {
    width: 100%;
    justify-content: space-between;
  }
  .date-tabs {
    flex: 1;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  .date-tabs::-webkit-scrollbar { display: none; }

  .overview-card { padding: 12px; }
  .overview-sub { display: none; }
  /* 总览热力条: 收紧列宽, 满宽显示 */
  .heat-bar-row {
    grid-template-columns: 70px 1fr 38px 54px;
    gap: 7px;
    padding: 5px 4px;
  }
  .heat-name { font-size: 12px; }

  /* 分组头: 龙头另起, 不挤 */
  .grp-head { gap: 8px 10px; padding: 10px 12px; }
  .grp-leader {
    margin-left: 0;
    flex-basis: 100%;
    order: 9;
  }

  /* 紧凑行: 网格改三行
     行1 名称 | 涨跌 | 箭头 ; 行2 资金条满宽 ; 行3 换手 行业 名次 */
  .srow-main {
    grid-template-columns: 1fr auto 16px;
    grid-template-areas:
      'name pct exp'
      'money money money'
      'turn ind rc';
    gap: 6px 8px;
    padding: 10px 12px;
  }
  .s-name { grid-area: name; min-width: 0; }
  /* 窄屏隐藏独立代码列, 信息密度优先 */
  .s-code { display: none; }
  .s-pct { grid-area: pct; align-self: center; }
  .s-exp { grid-area: exp; }
  .s-money { grid-area: money; }
  .money-track { height: 10px; }
  .s-turn { grid-area: turn; justify-self: start; align-self: center; }
  .s-ind { grid-area: ind; justify-self: start; align-self: center; }
  .s-rc { grid-area: rc; justify-self: end; align-self: center; text-align: right; }

  .srow-detail { padding: 4px 12px 12px; }
}
</style>
