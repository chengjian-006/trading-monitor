<script setup lang="ts">
// v1.7.93: 删除"今日信号"Tab(信号在"今日预警"页 + 股票池行内展开看), 去掉 NTabs 容器
// 主看板只保留两块: 顶部实时 MarketOverviewBar + AI 市场分析卡片
import { onMounted, ref } from 'vue'
import { NSkeleton, NCard, NButton, NIcon, NTag, NCollapse, NCollapseItem } from 'naive-ui'
import { SparklesOutline, RefreshOutline, ChevronUpOutline, ChevronDownOutline, ThumbsUpOutline, ThumbsDownOutline, ThumbsUp, ThumbsDown } from '@vicons/ionicons5'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { useKeyedSubmitGuard } from '../composables/useSubmitGuard'
import MarketOverviewBar from '../components/common/MarketOverviewBar.vue'
import EmotionPanel from '../components/common/EmotionPanel.vue'
import MarketRiskBanner from '../components/common/MarketRiskBanner.vue'
import ThemeHeatPanel from '../components/common/ThemeHeatPanel.vue'
import SectorRotationPanel from '../components/common/SectorRotationPanel.vue'
import NearBuyPanel from '../components/common/NearBuyPanel.vue'
import WencaiOpinionPanel from '../components/common/WencaiOpinionPanel.vue'
import MarketIndexOverview from '../components/common/MarketIndexOverview.vue'
import { fetchTodayReports, fetchLatestReport, getSlotName, upsertReportFeedback, deleteReportFeedback, fetchReportFeedback, type MarketReport, type ReportFeedback } from '../api/market-report'

const reports = ref<MarketReport[]>([])
const reportLoading = ref(false)
const reportCollapsed = ref(false)
const showAiReport = false   // AI 市场分析板块先整体拿掉(需要恢复改回 true)
const message = useGlobalMessage()

// report_id -> 'up' | 'down' | null
const feedbackMap = ref<Record<number, 'up' | 'down'>>({})

async function loadReportFeedback(ids: number[]) {
  if (!ids.length) {
    feedbackMap.value = {}
    return
  }
  try {
    const list = await fetchReportFeedback(ids)
    const map: Record<number, 'up' | 'down'> = {}
    for (const f of list) map[f.report_id] = f.vote
    feedbackMap.value = map
  } catch {
    // silent
  }
}

async function loadReports() {
  reportLoading.value = true
  try {
    const todayList = await fetchTodayReports()
    if (todayList.length) {
      reports.value = todayList
    } else {
      const latest = await fetchLatestReport()
      reports.value = latest ? [latest] : []
    }
    await loadReportFeedback(reports.value.map(r => r.id).filter(Boolean))
  } catch {
    /* silent */
  } finally {
    reportLoading.value = false
  }
}

// 防重复提交: 👍/👎 连点会让 upsert 与 delete 并发竞态, 库里最终赞踩状态可能与界面相反。
// 按 report_id 守卫(同一份报告的两颗按钮共用一把锁), 不同报告互不影响
const { isBusy: voteBusy, guardKey: guardVote } = useKeyedSubmitGuard()

const toggleVote = guardVote((reportId: number, _vote: 'up' | 'down') => String(reportId), async (reportId: number, vote: 'up' | 'down') => {
  const current = feedbackMap.value[reportId]
  try {
    if (current === vote) {
      // 同方向再点 → 取消
      await deleteReportFeedback(reportId)
      const next = { ...feedbackMap.value }
      delete next[reportId]
      feedbackMap.value = next
      message.info('已取消标记')
    } else {
      await upsertReportFeedback(reportId, vote)
      feedbackMap.value = { ...feedbackMap.value, [reportId]: vote }
      message.success(vote === 'up' ? '已标记 👍' : '已标记 👎 (反馈将用于优化 AI prompt)')
    }
  } catch {
    message.error('标记失败')
  }
})

// 旧 AI 报告里"全球股市/A股大盘概况/市场温度"区块在前端剥掉(数据已由顶部 MarketOverviewBar 实时刷新)
const STRIP_SECTIONS = ['全球股市', 'A股大盘概况', '大盘概况', '市场温度']

function stripDataSections(html: string): string {
  let out = html
  for (const title of STRIP_SECTIONS) {
    const re = new RegExp(
      `<h3[^>]*>\\s*${title}[^<]*(?:<[^>]*>[^<]*<\\/[^>]*>[^<]*)*<\\/h3>[\\s\\S]*?(?=<h3|$)`,
      'g'
    )
    out = out.replace(re, '')
  }
  return out
}

function renderContent(text: string): string {
  if (!text) return ''
  if (text.includes('<table') || text.includes('<h3')) {
    // AI 报告结构化 HTML(含 table/h3): 目前该面板已隐藏(showAiReport=false); 若重新启用需接 DOMPurify 消毒
    return stripDataSections(text)
  }
  // markdown 分支: 先转义 & 和 <(阻断标签注入, 保留 > 让引用块 markdown 仍生效)
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/\n/g, '<br>')
    .replace(/<br><blockquote>/g, '<blockquote>')
    .replace(/<\/blockquote><br>/g, '</blockquote>')
}

onMounted(() => {
  if (showAiReport) loadReports()
})
</script>

<template>
  <div class="signal-view">
    <!-- 大盘指数概览: 指数 tab 切换 + 单张大分时图 + 右侧成交额栏 (参照东财/同花顺盘面风格), 置顶 -->
    <MarketIndexOverview />

    <!-- 实时市场概览 (全球/A股/温度), 30s 自动刷新 -->
    <MarketOverviewBar />

    <!-- 市场风险两级预警状态条 (GREEN/YELLOW/RED 三态): 环境级"该不该开新仓", 全宽母线 -->
    <MarketRiskBanner />

    <!-- 机构级驾驶舱栅格 (v1.7.656 重排): 不等宽双栏, 按内容密度分配 —
         左主栏(情绪主角 → 板块轮动细条 → 情绪温度表)纵向堆叠, 右栏临近买点长清单整列到底自滚动。
         窄屏(≤960px)回落单列堆叠。 -->
    <div class="cockpit-grid">
      <div class="cockpit-main">
        <!-- 短线情绪 (温度计/封板率/连板梯队): 开盘先看"今天敢不敢干", 主角占位 -->
        <EmotionPanel />
        <!-- 板块轮动·弱强转换: 内容常稀疏(盘中多为无信号), 压在情绪下方细条不独占一栏 -->
        <SectorRotationPanel />
      </div>
      <!-- 右栏: 临近买点(盯盘轨, 撑满并内部滚) + 问财观点(投顾档, 固定高度不拉伸) 纵向堆叠。 -->
      <div class="cockpit-side-wrap">
        <div class="cockpit-side">
          <NearBuyPanel />
          <WencaiOpinionPanel />
        </div>
      </div>
    </div>

    <!-- 市场情绪温度表: 日期×题材 涨停家数矩阵 + 强势主线操作, 占满整行(表宽, 单独一行才排得开) -->
    <ThemeHeatPanel class="full-row" />

    <!-- AI 市场分析 (先整体拿掉: showAiReport=false; 需要恢复改回 true) -->
    <NCard v-if="showAiReport" size="small" class="report-card" :bordered="true">
      <template #header>
        <div class="report-header">
          <NIcon :component="SparklesOutline" :size="16" class="report-icon" />
          <span>AI 市场分析</span>
          <span v-if="reports.length" class="report-count">{{ reports.length }} 份</span>
          <span class="header-spacer" />
          <NButton quaternary circle size="tiny" @click="loadReports" :loading="reportLoading" title="刷新" aria-label="刷新">
            <template #icon><NIcon :component="RefreshOutline" :size="14" /></template>
          </NButton>
          <NButton quaternary circle size="tiny" @click="reportCollapsed = !reportCollapsed" :title="reportCollapsed ? '展开' : '收起'" :aria-label="reportCollapsed ? '展开' : '收起'">
            <template #icon><NIcon :component="reportCollapsed ? ChevronDownOutline : ChevronUpOutline" :size="14" /></template>
          </NButton>
        </div>
      </template>

      <div v-show="!reportCollapsed">
        <NSkeleton v-if="reportLoading && !reports.length" :repeat="3" text />

        <template v-else-if="reports.length">
          <div class="report-latest">
            <div class="report-meta">
              <NTag size="small" type="info" :bordered="false">{{ getSlotName(reports[0].time_slot) }}</NTag>
              <span class="report-time">{{ reports[0].created_at }}</span>
              <span class="report-feedback">
                <NButton quaternary circle size="tiny"
                         :type="feedbackMap[reports[0].id] === 'up' ? 'success' : 'default'"
                         :title="feedbackMap[reports[0].id] === 'up' ? '已标记有用 (再点取消)' : '标记有用'"
                         :aria-label="feedbackMap[reports[0].id] === 'up' ? '已标记有用 (再点取消)' : '标记有用'"
                         :loading="voteBusy(String(reports[0].id))"
                         :disabled="voteBusy(String(reports[0].id))"
                         @click="toggleVote(reports[0].id, 'up')">
                  <template #icon>
                    <NIcon :component="feedbackMap[reports[0].id] === 'up' ? ThumbsUp : ThumbsUpOutline" :size="14" />
                  </template>
                </NButton>
                <NButton quaternary circle size="tiny"
                         :type="feedbackMap[reports[0].id] === 'down' ? 'error' : 'default'"
                         :title="feedbackMap[reports[0].id] === 'down' ? '已标记无用 (再点取消)' : '标记无用 — 将用于优化 AI prompt'"
                         :aria-label="feedbackMap[reports[0].id] === 'down' ? '已标记无用 (再点取消)' : '标记无用 — 将用于优化 AI prompt'"
                         :loading="voteBusy(String(reports[0].id))"
                         :disabled="voteBusy(String(reports[0].id))"
                         @click="toggleVote(reports[0].id, 'down')">
                  <template #icon>
                    <NIcon :component="feedbackMap[reports[0].id] === 'down' ? ThumbsDown : ThumbsDownOutline" :size="14" />
                  </template>
                </NButton>
              </span>
            </div>
            <div class="report-content" v-html="renderContent(reports[0].content)" />
          </div>

          <NCollapse v-if="reports.length > 1" class="report-history">
            <NCollapseItem :title="`查看更早的分析 (${reports.length - 1} 份)`" name="history">
              <div v-for="r in reports.slice(1)" :key="r.id" class="report-item">
                <div class="report-meta">
                  <NTag size="small" type="default" :bordered="false">{{ getSlotName(r.time_slot) }}</NTag>
                  <span class="report-time">{{ r.created_at }}</span>
                  <span class="report-feedback">
                    <NButton quaternary circle size="tiny"
                             :type="feedbackMap[r.id] === 'up' ? 'success' : 'default'"
                             aria-label="标记有用"
                             :loading="voteBusy(String(r.id))"
                             :disabled="voteBusy(String(r.id))"
                             @click="toggleVote(r.id, 'up')">
                      <template #icon>
                        <NIcon :component="feedbackMap[r.id] === 'up' ? ThumbsUp : ThumbsUpOutline" :size="14" />
                      </template>
                    </NButton>
                    <NButton quaternary circle size="tiny"
                             :type="feedbackMap[r.id] === 'down' ? 'error' : 'default'"
                             aria-label="标记无用"
                             :loading="voteBusy(String(r.id))"
                             :disabled="voteBusy(String(r.id))"
                             @click="toggleVote(r.id, 'down')">
                      <template #icon>
                        <NIcon :component="feedbackMap[r.id] === 'down' ? ThumbsDown : ThumbsDownOutline" :size="14" />
                      </template>
                    </NButton>
                  </span>
                </div>
                <div class="report-content" v-html="renderContent(r.content)" />
              </div>
            </NCollapseItem>
          </NCollapse>
        </template>

        <div v-else class="report-empty">
          <NIcon :component="SparklesOutline" :size="20" class="report-empty-icon" />
          <div>等待 AI 分析生成 ...</div>
          <div class="report-empty-hint">每个交易日 09:26 / 10:00 / 11:30 / 14:00 / 15:00 自动生成</div>
        </div>
      </div>
    </NCard>
  </div>
</template>


<style scoped>
.signal-view {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
/* 机构级驾驶舱栅格 (v1.7.656 重排): 左宽右窄不等宽双栏, 按内容密度分配空间 */
.cockpit-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.62fr) minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;   /* 右栏拉伸到与左栏等高, 便于问财观点撑满剩余高度 */
}
/* 左主栏: 情绪 → 板块轮动细条 → 情绪温度表 纵向堆叠 */
.cockpit-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;   /* 防内部宽表撑破栅格 */
}
/* 右栏容器: 绝对定位让右栏不参与栅格行高测量, 行高只由左主栏决定 —— 右栏再长也不会顶出左侧留白 */
.cockpit-side-wrap { position: relative; min-width: 0; }
/* 右栏: 临近买点(自然高、空间不够内部滚) + 问财观点(撑满剩余高度、内部自滚动) */
.cockpit-side {
  position: absolute;
  inset: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;   /* 兜底: 两个面板都压到最小高仍装不下时整栏可滚 */
}
.cockpit-side > :first-child { flex: 1 1 auto; min-height: 240px; }      /* 临近买点: 撑满剩余高度, 内部滚 */
.cockpit-side > :last-child { flex: 0 0 auto; height: 340px; }          /* 问财观点: 固定高度不拉伸, 内部滚 */
.cockpit-side :deep(.wo-panel) { height: 100%; }
/* 市场情绪温度表: 占满整行(脱离左右双栏), 宽表在此才排得开 */
.full-row { min-width: 0; }
/* 临近买点在右栏内也要能内部滚动, 否则挤不下时会溢出右栏 */
.cockpit-side :deep(.nearbuy-panel) { display: flex; flex-direction: column; min-height: 0; }
.cockpit-side :deep(.nearbuy-panel .list) { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
@media (max-width: 960px) {
  /* 窄屏回落单列: 顺序=情绪/轮动/临近买点/问财观点/温度表, 各面板自然高不强制撑满 */
  .cockpit-grid { grid-template-columns: 1fr; }
  .cockpit-side-wrap { position: static; }
  .cockpit-side { position: static; overflow: visible; }
  .cockpit-side > :first-child { flex: none; min-height: 0; }
  .cockpit-side > :last-child { flex: none; min-height: 0; height: auto; }
  .cockpit-side :deep(.wo-panel) { height: auto; }
  .cockpit-side :deep(.nearbuy-panel) { display: block; }
  .cockpit-side :deep(.nearbuy-panel .list) { overflow: visible; }
}
.report-card {
  border-radius: 6px;
}
.report-card :deep(.n-card-header) {
  padding: 8px 12px;
}
.report-card :deep(.n-card__content) {
  padding: 10px 12px;
}
.report-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
}
.report-icon {
  color: var(--primary);
}
.report-count {
  font-size: 11px;
  font-weight: 500;
  color: var(--text2);
  background: var(--bg-sunken);
  padding: 1px 8px;
  border-radius: 10px;
  font-variant-numeric: tabular-nums;
}
.header-spacer {
  flex: 1;
}
.report-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.report-time {
  font-size: 12px;
  color: var(--text2);
}
.report-feedback {
  margin-left: auto;
  display: inline-flex;
  gap: 2px;
}
.report-content {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text1);
}
.report-content :deep(strong) {
  color: var(--primary);
}
.report-content :deep(blockquote) {
  margin: 4px 0;
  padding: 2px 10px;
  border-left: 3px solid var(--primary);
  background: rgba(46, 128, 255, 0.04);
  color: var(--text2);
}
.report-content :deep(h3),
.report-content :deep(h4) {
  margin: 12px 0 6px;
  font-size: 13px;
  font-weight: 700;
}
.report-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin: 6px 0;
}
.report-content :deep(th),
.report-content :deep(td) {
  padding: 4px 6px;
  border-bottom: 1px solid var(--border-muted);
  text-align: left;
}
.report-content :deep(th) {
  background: var(--accent-bg-muted);
  font-weight: 600;
}
.report-content :deep(p) {
  margin: 4px 0;
}
.report-history {
  margin-top: 12px;
}
.report-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
.report-item:last-child {
  border-bottom: none;
}
.report-empty {
  text-align: center;
  padding: 40px 20px;
  color: var(--text2);
  font-size: 13px;
}
.report-empty-icon {
  color: var(--fg-subtle);
  margin-bottom: 8px;
}
.report-empty-hint {
  font-size: 11px;
  color: var(--fg-subtle);
  margin-top: 4px;
}

@media (max-width: 768px) {
  .signal-view { gap: 6px; }
  .report-card :deep(.n-card-header) { padding: 6px 10px; }
  .report-card :deep(.n-card__content) { padding: 8px 10px; }
  /* AI 报告正文(markdown v-html)若含宽表/长代码, 横向滚动而非撑破 */
  .report-content { font-size: 13px; line-height: 1.6; }
  .report-content :deep(table),
  .report-content :deep(pre) { display: block; overflow-x: auto; max-width: 100%; }
  .report-content :deep(img) { max-width: 100%; height: auto; }
}
</style>
