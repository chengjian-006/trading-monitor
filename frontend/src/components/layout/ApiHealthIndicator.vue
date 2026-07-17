<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { NPopover, NButton } from 'naive-ui'
import { fetchApiHealth, recheckApiHealth, type ApiHealthState } from '../../api/api-health'

const state = ref<ApiHealthState | null>(null)
const loading = ref(false)
let timer: number | null = null

const sources = computed(() => {
  if (!state.value) return []
  return Object.entries(state.value.sources).map(([id, src]) => ({ id, ...src }))
})

const functions = computed(() => state.value?.functions || [])
const summary = computed(() => state.value?.summary)
const failingTasks = computed(() => state.value?.failing_tasks || [])

// v1.7.72: 顶栏总状态: 任一功能 fail → 红, 任一 fail 但有 ok → 黄, 全 ok/unknown → 绿
const overallStatus = computed(() => {
  const s = summary.value
  if (!s || s.total === 0) return 'unknown'
  if (s.fail > 0) return s.fail === s.total ? 'fail' : 'degraded'
  return 'ok'
})
const overallLabel = computed(() => {
  const s = summary.value
  if (!s) return '加载中'
  if (s.fail > 0) return `${s.fail} 个功能不可用`
  if (s.unknown === s.total) return '5min 无业务调用'
  return `${s.ok}/${s.total} 功能正常`
})

function colorOf(summary: string) {
  if (summary === 'ok') return '#22c55e'
  if (summary === 'degraded') return '#f59e0b'
  if (summary === 'fail') return '#ef4444'
  return '#9ca3af'
}

function labelOf(summary: string) {
  if (summary === 'ok') return '正常'
  if (summary === 'degraded') return '部分异常'
  if (summary === 'fail') return '全部失败'
  return '未知'
}

const SHORT_LABEL: Record<string, string> = {
  eastmoney: '东财',
  sina: '新浪',
  akshare: 'akshare',
}
function shortLabel(id: string) {
  return SHORT_LABEL[id] || id
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function load() {
  // 没 token 直接跳过 — 避免在登出/token失效时仍然每 60s 撞一次 401
  if (!localStorage.getItem('token')) {
    stopPolling()
    return
  }
  try {
    state.value = await fetchApiHealth()
  } catch (e: any) {
    // 401 已经被 interceptor 处理 (清 token + 跳登录), 这里再停轮询以兜底
    if (e?.response?.status === 401) stopPolling()
  }
}

async function recheck() {
  if (loading.value) return
  loading.value = true
  try {
    state.value = await recheckApiHealth()
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  // 前端每 60s 拉取一次最新状态（后端 5min 探活，前端轮询频率高一点保证显示新鲜）
  // v1.7.571: 切走标签页时跳过(保留 stopPolling 在 401 时停轮询的能力, 故内联判断而非换 useVisiblePolling)
  timer = window.setInterval(() => { if (!document.hidden) load() }, 60000)
})

onUnmounted(() => {
  stopPolling()
})

// v1.7.81: 数据源 × 接口明细 默认折叠, 让顶部业务功能视图成为视觉焦点
const detailExpanded = ref(false)
</script>

<template>
  <div v-if="state" class="api-health">
    <NPopover trigger="hover" placement="bottom-end" :duration="100" :width="440">
      <template #trigger>
        <div class="dots" role="button" tabindex="0" :title="overallLabel" :aria-label="'外部接口健康监控: ' + overallLabel">
          <span class="dot" :style="{ background: colorOf(overallStatus) }" aria-hidden="true" />
          <span class="src-tag-label">{{ overallLabel }}</span>
        </div>
      </template>
      <div class="popover">
        <div class="popover-header">
          <span class="popover-title">外部接口健康监控</span>
          <NButton size="tiny" :loading="loading" @click="recheck">立即重测</NButton>
        </div>
        <div class="popover-checked">
          最近业务调用: {{ state.checked_at || '5 分钟内无调用' }}（窗口 5 分钟实时统计）
        </div>

        <div v-if="functions.length" class="functions-block">
          <div class="functions-head">
            <span>业务功能可用性</span>
            <span class="functions-summary" :style="{ color: colorOf(overallStatus) }">{{ overallLabel }}</span>
          </div>
          <div class="functions-grid">
            <div
              v-for="fn in functions"
              :key="fn.id"
              class="fn-item"
              :class="`fn-${fn.status}`"
              :title="fn.reason"
            >
              <span class="fn-dot" :style="{ background: colorOf(fn.status) }" />
              <span class="fn-label">{{ fn.label }}</span>
              <span class="fn-status">{{ fn.status === 'ok' ? '可用' : fn.status === 'fail' ? '✗ 不可用' : '—' }}</span>
              <div class="fn-reason">{{ fn.reason }}</div>
            </div>
          </div>
        </div>

        <div v-if="failingTasks.length" class="failing-tasks-block">
          <div class="functions-head">
            <span>⚠ 调度任务异常</span>
            <span class="functions-summary" style="color:#ef4444">{{ failingTasks.length }} 个连续失败</span>
          </div>
          <div class="failing-tasks-list">
            <div v-for="t in failingTasks" :key="t.job_id" class="failing-task-item"
                 :class="{ 'is-alerted': t.consecutive_failures >= 3 }" :title="t.last_error_msg">
              <span class="ft-name">{{ t.name }}</span>
              <span class="ft-count">连续失败 {{ t.consecutive_failures }} 次</span>
              <span v-if="t.last_error_msg" class="ft-error">
                {{ t.last_error_msg.length > 50 ? t.last_error_msg.slice(0, 50) + '…' : t.last_error_msg }}
              </span>
            </div>
          </div>
        </div>

        <div class="src-section-title" role="button" tabindex="0"
             :aria-expanded="detailExpanded"
             @click="detailExpanded = !detailExpanded" @keydown.enter="detailExpanded = !detailExpanded">
          <span :class="['detail-arrow', { expanded: detailExpanded }]" aria-hidden="true">▶</span>
          <span>数据源 × 接口明细</span>
          <span class="detail-hint">{{ detailExpanded ? '点击折叠' : '点击展开' }}</span>
        </div>
        <div v-show="detailExpanded" v-for="src in sources" :key="src.id" class="src-block">
          <div class="src-head">
            <span class="dot" :style="{ background: colorOf(src.summary) }" />
            <span class="src-name">{{ src.label }}</span>
            <span class="src-summary" :style="{ color: colorOf(src.summary) }">{{ labelOf(src.summary) }}</span>
          </div>
          <div class="checks">
            <div v-for="(c, usage) in src.checks" :key="usage" class="check">
              <div class="check-row">
                <span
                  class="check-dot"
                  :style="{ background: c.status === 'ok' ? '#22c55e' : c.status === 'unknown' ? '#9ca3af' : '#ef4444' }"
                />
                <span class="check-label">{{ state.usage_labels[usage] || usage }}</span>
                <span v-if="c.status === 'unknown'" class="check-unknown">5min无调用</span>
                <span v-else-if="c.status === 'ok'" class="check-latency">
                  {{ c.ok }}/{{ c.total }} · {{ c.latency_ms }}ms
                </span>
                <span v-else class="check-fail-tag">
                  失败 {{ (c.total ?? 0) - (c.ok ?? 0) }}/{{ c.total ?? 0 }} · {{ c.latency_ms }}ms
                </span>
              </div>
              <div v-if="c.status !== 'ok' && c.error" class="check-error-detail" :title="c.error">
                {{ c.error }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </NPopover>
  </div>
</template>

<style scoped>
.api-health {
  display: flex;
  align-items: center;
}
.dots {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--bg-default);
  border: 1px solid var(--border-muted);
  font-size: 12px;
  color: var(--fg-muted);
  cursor: pointer;
  touch-action: manipulation;
}
.src-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.7);
  white-space: nowrap;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.src-tag-label {
  letter-spacing: 0.5px;
}

.popover {
  font-size: 12px;
}
.functions-block {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 8px;
}
.functions-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 12px;
  color: #374151;
  margin-bottom: 4px;
}
.functions-summary {
  font-size: 11px;
  font-weight: 500;
}
.functions-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px 10px;
}
.fn-item {
  display: grid;
  grid-template-columns: 8px 1fr auto;
  grid-template-rows: auto auto;
  column-gap: 4px;
  align-items: center;
  font-size: 11px;
  padding: 2px 0;
}
.fn-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  grid-row: 1;
}
.fn-label {
  color: #1f2937;
  grid-row: 1;
}
.fn-status {
  font-size: 10px;
  grid-row: 1;
}
.fn-ok .fn-status { color: #16a34a; }
.fn-fail .fn-status { color: #ef4444; font-weight: 600; }
.fn-unknown .fn-status { color: #9ca3af; }
.fn-reason {
  grid-column: 2 / span 2;
  grid-row: 2;
  font-size: 10px;
  color: #6b7280;
  line-height: 1.3;
}
.fn-fail .fn-reason { color: #991b1b; }
.src-section-title {
  font-size: 11px;
  color: #6b7280;
  font-weight: 600;
  margin: 4px 0 2px;
  letter-spacing: 0.3px;
  cursor: pointer;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 0;
  touch-action: manipulation;
}
.src-section-title:hover {
  color: #374151;
}
.failing-tasks-block {
  margin-top: 10px;
  padding: 8px;
  border-radius: 4px;
  background: rgba(239, 68, 68, 0.04);
  border-left: 3px solid #ef4444;
}
.failing-tasks-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
}
.failing-task-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 4px 6px;
  border-radius: 3px;
  background: white;
}
.failing-task-item.is-alerted {
  background: rgba(239, 68, 68, 0.06);
  border-left: 2px solid #dc2626;
}
.ft-name {
  font-weight: 600;
  font-size: 12px;
  color: #374151;
}
.ft-count {
  font-size: 11px;
  color: #b91c1c;
}
.ft-error {
  font-size: 10px;
  color: #9ca3af;
  font-family: monospace;
  word-break: break-all;
}
.detail-arrow {
  display: inline-block;
  width: 10px;
  font-size: 9px;
  color: #9ca3af;
  transition: transform 0.15s;
}
.detail-arrow.expanded {
  transform: rotate(90deg);
}
.detail-hint {
  margin-left: auto;
  font-size: 10px;
  color: #9ca3af;
  font-weight: 400;
}
.popover-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.popover-title {
  font-weight: 600;
  font-size: 13px;
}
.popover-checked {
  color: #888;
  font-size: 11px;
  margin-bottom: 8px;
}
.src-block {
  border-top: 1px solid #eee;
  padding: 6px 0;
}
.src-block:first-of-type {
  border-top: none;
}
.src-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.src-name {
  font-weight: 600;
}
.src-summary {
  font-size: 11px;
  margin-left: auto;
}
.checks {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-left: 14px;
}
.check {
  font-size: 11px;
}
.check-row {
  display: flex;
  align-items: center;
  gap: 5px;
}
.check-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.check-label {
  color: #333;
}
.check-latency {
  color: #999;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.check-fail-tag {
  color: #ef4444;
  margin-left: auto;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.check-unknown {
  color: #9ca3af;
  margin-left: auto;
  font-size: 10px;
}
.check-error-detail {
  margin-top: 2px;
  margin-left: 0;
  padding: 3px 6px;
  background: #fef2f2;
  border-left: 2px solid #ef4444;
  color: #991b1b;
  font-size: 10px;
  line-height: 1.4;
  word-break: break-all;
  overflow-wrap: anywhere;
  border-radius: 2px;
  cursor: help;
  box-sizing: border-box;
  max-width: 100%;
}
</style>
