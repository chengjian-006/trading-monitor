<script setup lang="ts">
import { onMounted, ref, computed, h } from 'vue'
import { NDataTable, NPagination, NSkeleton, NTag, NSelect, NButton, NIcon, NModal, NCard, NInput, NDatePicker } from 'naive-ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { RefreshOutline, SearchOutline, DocumentTextOutline } from '@vicons/ionicons5'
import { fetchLogs, fetchLogActions } from '../api/auth'
import FilterPanel from '../components/common/FilterPanel.vue'
import type { OperationLog } from '../types'

const message = useGlobalMessage()
const logs = ref<OperationLog[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const loading = ref(false)
// 默认: 全部类型 + 近7天 —— 旧默认(登录+仅今天)常匹配不到, 一进页面就是空表看着像坏了(v1.7.658修)
const filterAction = ref<string | null>(null)
const filterKeyword = ref('')
const filterDateRange = ref<[number, number] | null>(null)

function recentRange(days = 7): [number, number] {
  const now = new Date()
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() + 86400000 - 1
  const start = end + 1 - days * 86400000
  return [start, end]
}
filterDateRange.value = recentRange(7)

function formatDate(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const ACTION_LABELS: Record<string, { label: string; type: 'info' | 'success' | 'warning' | 'error' | 'default' }> = {
  login: { label: '系统登录', type: 'info' },
  logout: { label: '系统登出', type: 'default' },
  update_config: { label: '修改配置', type: 'warning' },
  create_user: { label: '创建用户', type: 'success' },
  delete_user: { label: '删除用户', type: 'error' },
  delete_stock: { label: '删除股票', type: 'error' },
  enable_task: { label: '启用任务', type: 'success' },
  disable_task: { label: '停用任务', type: 'warning' },
  update_task: { label: '修改任务', type: 'warning' },
  trigger_task: { label: '手动触发', type: 'info' },
}

const showDetail = ref(false)
const detailLog = ref<OperationLog | null>(null)

interface DiffItem {
  key: string
  oldVal: string
  newVal: string
  changed: boolean
}

function computeDiff(old_value: Record<string, unknown> | null, new_value: Record<string, unknown> | null): DiffItem[] {
  const oldObj = old_value || {}
  const newObj = new_value || {}
  const allKeys = new Set([...Object.keys(oldObj), ...Object.keys(newObj)])
  const items: DiffItem[] = []
  for (const key of allKeys) {
    const ov = key in oldObj ? String(oldObj[key] ?? '') : ''
    const nv = key in newObj ? String(newObj[key] ?? '') : ''
    items.push({ key, oldVal: ov, newVal: nv, changed: ov !== nv })
  }
  items.sort((a, b) => (a.changed === b.changed ? 0 : a.changed ? -1 : 1))
  return items
}

const detailDiff = computed(() => {
  if (!detailLog.value) return []
  return computeDiff(detailLog.value.old_value, detailLog.value.new_value)
})

function openDetail(row: OperationLog) {
  detailLog.value = row
  showDetail.value = true
}

const columns = [
  {
    title: '时间', key: 'created_at', width: 160,
    render: (row: OperationLog) => row.created_at?.replace('T', ' ') ?? '',
  },
  { title: '用户', key: 'username', width: 80 },
  {
    title: '操作',
    key: 'action',
    width: 100,
    render: (row: OperationLog) => {
      const info = ACTION_LABELS[row.action] || { label: row.action, type: 'default' as const }
      return h(NTag, { size: 'small', type: info.type, bordered: false }, () => info.label)
    },
  },
  { title: '对象', key: 'target', width: 100 },
  {
    title: '操作详情',
    key: 'detail',
    width: 100,
    render: (row: OperationLog) => {
      if (!row.old_value && !row.new_value) return '-'
      return h('div', {
        style: 'display: inline-block; cursor: pointer',
        onClick: (e: Event) => { e.stopPropagation(); openDetail(row) },
      }, [
        h(NButton, { size: 'small', type: 'primary' }, {
          icon: () => h(NIcon, null, () => h(DocumentTextOutline)),
          default: () => '详情',
        }),
      ])
    },
  },
]

async function loadLogs() {
  loading.value = true
  try {
    const params: Record<string, unknown> = { page: page.value, page_size: pageSize.value }
    if (filterAction.value) params.action = filterAction.value
    if (filterKeyword.value) params.keyword = filterKeyword.value
    if (filterDateRange.value) {
      params.date_from = formatDate(filterDateRange.value[0])
      params.date_to = formatDate(filterDateRange.value[1])
    }
    const result = await fetchLogs(params as any)
    logs.value = result.logs
    total.value = result.total
  } catch {
    message.error('加载日志失败')
  } finally {
    loading.value = false
  }
}

const actionOptions = ref(
  Object.entries(ACTION_LABELS).map(([key, info]) => ({ label: info.label, value: key })),
)

async function loadActions() {
  try {
    const { actions } = await fetchLogActions()
    const opts: { label: string; value: string }[] = []
    for (const action of actions) {
      const info = ACTION_LABELS[action]
      opts.push({ label: info ? info.label : action, value: action })
    }
    actionOptions.value = opts
  } catch { /* keep default */ }
}

function handlePageChange(p: number) {
  page.value = p
  loadLogs()
}

function handleReset() {
  filterAction.value = null
  filterKeyword.value = ''
  filterDateRange.value = recentRange(7)
  page.value = 1
  loadLogs()
}

onMounted(() => {
  loadActions()
  loadLogs()
})
</script>

<template>
  <div>
    <FilterPanel>
    <div class="filter-bar">
      <div class="filter-fields">
        <div class="filter-item">
          <label for="log-keyword">全局搜索</label>
          <NInput
            v-model:value="filterKeyword"
            size="small"
            clearable
            placeholder="用户/对象/操作"
            :input-props="{ id: 'log-keyword', name: 'keyword', type: 'search' }"
            @keyup.enter="loadLogs"
          />
        </div>
        <div class="filter-item" style="min-width: 220px">
          <label>时间段</label>
          <NDatePicker
            v-model:value="filterDateRange"
            type="daterange"
            size="small"
            clearable
            format="yyyy-MM-dd"
            placement="bottom"
            to="body"
          />
        </div>
        <div class="filter-item">
          <label>操作类型</label>
          <NSelect
            v-model:value="filterAction"
            :options="actionOptions"
            size="small"
            clearable
            placeholder="全部"
          />
        </div>
      </div>
      <div class="filter-actions">
        <NButton size="small" type="primary" @click="handleReset">
          <template #icon><NIcon><RefreshOutline /></NIcon></template>
          重置
        </NButton>
        <NButton size="small" type="primary" @click="loadLogs" :loading="loading">
          <template #icon><NIcon><SearchOutline /></NIcon></template>
          查询
        </NButton>
      </div>
    </div>
    </FilterPanel>

    <NSkeleton v-if="loading && logs.length === 0" :repeat="5" text />

    <Transition v-else name="content-fade" appear>
      <div>
        <div class="table-summary">共 {{ total }} 条记录</div>
        <NDataTable
          :columns="columns"
          :data="logs"
          :bordered="false"
          size="small"
          :resizable-columns="true"
          :row-key="(row: OperationLog) => row.id"
          :scroll-x="540"
          :loading="loading"
          max-height="calc(100vh - 220px)"
        />
        <div v-if="total > pageSize" class="pagination-row">
          <NPagination
            :page="page"
            :page-size="pageSize"
            :item-count="total"
            @update:page="handlePageChange"
          />
        </div>
        <div v-if="logs.length === 0 && !loading" class="empty">暂无操作记录</div>
      </div>
    </Transition>

    <NModal v-model:show="showDetail" preset="card" title="操作详情" style="width: 560px; max-width: 90vw" @close="showDetail = false">
      <template v-if="detailLog">
        <div class="detail-meta">
          <span><b>时间：</b>{{ detailLog.created_at?.replace('T', ' ') }}</span>
          <span><b>用户：</b>{{ detailLog.username }}</span>
          <span><b>操作：</b>{{ ACTION_LABELS[detailLog.action]?.label || detailLog.action }}</span>
          <span><b>对象：</b>{{ detailLog.target }}</span>
        </div>
        <table class="diff-table">
          <thead>
            <tr>
              <th>字段</th>
              <th>变更前</th>
              <th>变更后</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in detailDiff" :key="item.key" :class="{ 'diff-changed': item.changed }">
              <td class="diff-key">{{ item.key }}</td>
              <td :class="{ 'diff-old': item.changed }">{{ item.oldVal || '-' }}</td>
              <td :class="{ 'diff-new': item.changed }">{{ item.newVal || '-' }}</td>
            </tr>
            <tr v-if="detailDiff.length === 0">
              <td colspan="3" style="text-align: center; color: var(--text2)">无变更数据</td>
            </tr>
          </tbody>
        </table>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.filter-bar {
  background: var(--surface);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  padding: 16px 20px;
  margin-bottom: 16px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px 24px;
  align-items: end;
  position: sticky;
  top: 0;
  z-index: 50;
}
.filter-fields {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.filter-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 120px;
}
.filter-item label {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.6);
  white-space: nowrap;
}
.filter-actions {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  justify-content: flex-end;
}
.table-summary {
  text-align: right;
  font-size: 12px;
  color: var(--text2);
  margin-bottom: 8px;
}
.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.empty {
  text-align: center;
  padding: 40px;
  color: var(--text2);
}
.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 16px;
  font-size: 13px;
  color: var(--text2);
}
.detail-meta b {
  color: rgba(0, 0, 0, 0.85);
}
.diff-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.diff-table th {
  background: var(--bg-sunken);
  padding: 8px 12px;
  text-align: left;
  font-weight: 600;
  border-bottom: 2px solid var(--border);
}
.diff-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  word-break: break-all;
}
.diff-key {
  font-weight: 600;
  color: rgba(0, 0, 0, 0.65);
  white-space: nowrap;
}
.diff-changed {
  background: color-mix(in srgb, var(--warn-fg) 10%, transparent);
}
.diff-old {
  color: var(--danger-fg);
  background: var(--danger-bg-muted);
  text-decoration: line-through;
  font-weight: 600;
}
.diff-new {
  color: var(--success-fg);
  background: var(--success-bg-muted);
  font-weight: 600;
}

/* ── 移动端适配 (≤768px) ── */
@media (max-width: 768px) {
  /* 过滤栏改单列堆叠, 取消 sticky 防遮挡内容 */
  .filter-bar {
    grid-template-columns: 1fr;
    gap: 10px;
    padding: 12px;
    position: static;
    top: auto;
  }
  .filter-fields {
    flex-direction: column;
    gap: 10px;
  }
  /* 各控件全宽; 覆盖时间段项的 inline min-width:220px 防横向溢出 */
  .filter-item {
    width: 100%;
    flex: 1 1 100%;
    min-width: 0 !important;
  }
  .filter-item :deep(.n-input),
  .filter-item :deep(.n-select),
  .filter-item :deep(.n-date-picker) {
    width: 100%;
  }
  /* 触摸目标 ≥40px: 输入类控件 */
  .filter-item :deep(.n-input),
  .filter-item :deep(.n-base-selection),
  .filter-item :deep(.n-date-picker .n-input) {
    min-height: 40px;
  }
  /* 重置/查询按钮排到下面并等分, 触摸目标 ≥40px */
  .filter-actions {
    justify-content: stretch;
    gap: 10px;
  }
  .filter-actions :deep(.n-button) {
    flex: 1;
    min-height: 40px;
  }
  /* 汇总条左对齐更省空间 */
  .table-summary {
    text-align: left;
  }
  /* 详情弹窗内 meta 单列排列, 表格允许自身横向滚动不撑破页面 */
  .detail-meta {
    gap: 8px 16px;
  }
  .diff-table {
    display: block;
    overflow-x: auto;
  }
}
</style>
