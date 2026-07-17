<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  NButton, NSwitch, NIcon, NTag, NSpace, NModal,
  NForm, NFormItem, NInput, NInputNumber, NSelect, NSkeleton,
} from 'naive-ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { PlayOutline, CreateOutline, RefreshOutline } from '@vicons/ionicons5'
import type { ScheduledTask } from '../types'
import {
  fetchScheduledTasks, updateScheduledTask, toggleScheduledTask, triggerScheduledTask,
} from '../api/scheduled-tasks'

const message = useGlobalMessage()
const loading = ref(true)
const tasks = ref<ScheduledTask[]>([])
const triggeringId = ref<string | null>(null)

const showEdit = ref(false)
const editForm = ref({
  job_id: '',
  name: '',
  description: '',
  schedule_type: 'cron' as 'interval' | 'cron',
  hour: 0,
  minute: 0,
  seconds: 30,
})
const saveLoading = ref(false)

interface TaskGroup {
  title: string
  icon: string
  color: string
  tasks: ScheduledTask[]
}

const taskGroups = computed<TaskGroup[]>(() => {
  const reportTasks: ScheduledTask[] = []
  const dataTasks: ScheduledTask[] = []
  const scanTasks: ScheduledTask[] = []

  for (const t of tasks.value) {
    if (t.handler === 'run_market_report') {
      reportTasks.push(t)
    } else if (t.handler === 'scan_stock_pool') {
      scanTasks.push(t)
    } else {
      dataTasks.push(t)
    }
  }

  const groups: TaskGroup[] = []
  if (scanTasks.length) groups.push({ title: '信号扫描', icon: '🎯', color: '#e74c3c', tasks: scanTasks })
  if (dataTasks.length) groups.push({ title: '数据刷新', icon: '📊', color: '#0969DA', tasks: dataTasks })
  if (reportTasks.length) groups.push({ title: 'AI报告', icon: '📝', color: '#18a058', tasks: reportTasks })
  return groups
})

async function loadTasks() {
  loading.value = true
  try {
    tasks.value = await fetchScheduledTasks()
  } catch {
    message.error('加载定时任务失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadTasks)

function formatSchedule(t: ScheduledTask): string {
  if (t.schedule_type === 'interval') {
    const s = t.schedule_config.seconds ?? 0
    if (s >= 60) return `每 ${Math.round(s / 60)} 分钟`
    return `每 ${s} 秒`
  }
  const h = String(t.schedule_config.hour ?? 0).padStart(2, '0')
  const m = String(t.schedule_config.minute ?? 0).padStart(2, '0')
  return `每天 ${h}:${m}`
}

function formatTime(ts: string | null): string {
  if (!ts) return '-'
  const d = new Date(ts)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

async function handleToggle(task: ScheduledTask, enabled: boolean) {
  try {
    await toggleScheduledTask(task.job_id, enabled)
    task.enabled = enabled
    message.success(enabled ? `${task.name} 已启用` : `${task.name} 已停用`)
    await loadTasks()
  } catch {
    message.error('操作失败')
  }
}

async function handleTrigger(task: ScheduledTask) {
  triggeringId.value = task.job_id
  try {
    await triggerScheduledTask(task.job_id)
    message.success(`${task.name} 已触发`)
    await loadTasks()
  } catch {
    message.error('触发失败')
  } finally {
    triggeringId.value = null
  }
}

function openEdit(task: ScheduledTask) {
  editForm.value = {
    job_id: task.job_id,
    name: task.name,
    description: task.description,
    schedule_type: task.schedule_type,
    hour: task.schedule_config.hour ?? 0,
    minute: task.schedule_config.minute ?? 0,
    seconds: task.schedule_config.seconds ?? 30,
  }
  showEdit.value = true
}

async function handleSave() {
  const f = editForm.value
  const schedule_config: Record<string, number> = f.schedule_type === 'cron'
    ? { hour: f.hour, minute: f.minute }
    : { seconds: f.seconds }
  saveLoading.value = true
  try {
    await updateScheduledTask(f.job_id, {
      name: f.name,
      description: f.description,
      schedule_type: f.schedule_type,
      schedule_config,
    })
    message.success('保存成功')
    showEdit.value = false
    await loadTasks()
  } catch {
    message.error('保存失败')
  } finally {
    saveLoading.value = false
  }
}

const scheduleTypeOptions = [
  { label: '定时循环', value: 'interval' },
  { label: '定时触发', value: 'cron' },
]

const enabledCount = computed(() => tasks.value.filter(t => t.enabled).length)
</script>

<template>
  <div class="scheduled-task-page">
    <div class="page-header">
      <span class="page-title">定时任务</span>
      <NSpace size="small" align="center">
        <span class="running-badge">{{ enabledCount }}/{{ tasks.length }} 运行中</span>
        <NButton size="tiny" quaternary circle :loading="loading" @click="loadTasks">
          <template #icon><NIcon :size="14"><RefreshOutline /></NIcon></template>
        </NButton>
      </NSpace>
    </div>

    <NSkeleton v-if="loading && tasks.length === 0" :repeat="4" text style="margin-bottom: 16px" />
    <Transition v-else name="content-fade" appear>
      <div class="groups-container">
        <div v-for="group in taskGroups" :key="group.title" class="task-group">
          <div class="group-header" :style="{ '--group-color': group.color }">
            <span class="group-icon">{{ group.icon }}</span>
            <span class="group-title">{{ group.title }}</span>
            <span class="group-count">{{ group.tasks.length }}</span>
          </div>
          <div class="group-cards">
            <div
              v-for="task in group.tasks"
              :key="task.job_id"
              class="task-card"
              :class="{ disabled: !task.enabled }"
            >
              <div class="task-card-header">
                <div class="task-name">{{ task.name }}</div>
                <NSwitch
                  :value="task.enabled"
                  size="small"
                  @update:value="(val: boolean) => handleToggle(task, val)"
                />
              </div>

              <div class="task-desc">{{ task.description }}</div>

              <div class="task-meta">
                <NTag size="small" :type="task.schedule_type === 'cron' ? 'success' : 'info'" :bordered="false">
                  {{ formatSchedule(task) }}
                </NTag>
                <span v-if="task.running" class="task-status running">运行中</span>
                <span v-else class="task-status stopped">已停止</span>
              </div>

              <div class="task-run-info">
                <span class="run-label">上次</span>
                <span class="run-time">{{ formatTime(task.last_run_at) }}</span>
                <NTag
                  v-if="task.last_status"
                  size="small"
                  :type="task.last_status === 'success' ? 'success' : 'error'"
                  :bordered="false"
                  round
                >
                  {{ task.last_status === 'success' ? '成功' : '失败' }}
                </NTag>
                <NTag
                  v-if="(task.consecutive_failures ?? 0) > 0"
                  size="small"
                  :type="(task.consecutive_failures ?? 0) >= 3 ? 'error' : 'warning'"
                  :bordered="false"
                  round
                  :title="(task.consecutive_failures ?? 0) >= 3 ? '已触发推送告警(冷却中)' : '尚未达到 3 次告警阈值'"
                  :aria-label="(task.consecutive_failures ?? 0) >= 3 ? '已触发推送告警(冷却中)' : '尚未达到 3 次告警阈值'"
                >
                  连续失败 {{ task.consecutive_failures }} 次
                </NTag>
              </div>
              <div v-if="task.last_error_msg" class="task-error" :title="task.last_error_msg">
                ⚠ {{ task.last_error_msg.length > 60 ? task.last_error_msg.slice(0, 60) + '…' : task.last_error_msg }}
              </div>

              <div class="task-actions">
                <NButton
                  size="small"
                  type="primary"
                  secondary
                  :loading="triggeringId === task.job_id"
                  @click="handleTrigger(task)"
                >
                  <template #icon><NIcon><PlayOutline /></NIcon></template>
                  触发
                </NButton>
                <NButton size="small" secondary @click="openEdit(task)">
                  <template #icon><NIcon><CreateOutline /></NIcon></template>
                  编辑
                </NButton>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>

    <NModal v-model:show="showEdit" preset="card" title="编辑定时任务" style="width: 460px" :mask-closable="false">
      <NForm label-placement="left" label-width="80" :show-feedback="false">
        <NFormItem label="任务名称">
          <NInput v-model:value="editForm.name" />
        </NFormItem>
        <NFormItem label="任务描述">
          <NInput v-model:value="editForm.description" />
        </NFormItem>
        <NFormItem label="调度类型">
          <NSelect v-model:value="editForm.schedule_type" :options="scheduleTypeOptions" />
        </NFormItem>
        <NFormItem v-if="editForm.schedule_type === 'cron'" label="触发时间">
          <NSpace>
            <NInputNumber v-model:value="editForm.hour" :min="0" :max="23" style="width: 90px" />
            <span style="line-height: 34px">时</span>
            <NInputNumber v-model:value="editForm.minute" :min="0" :max="59" style="width: 90px" />
            <span style="line-height: 34px">分</span>
          </NSpace>
        </NFormItem>
        <NFormItem v-else label="间隔秒数">
          <NInputNumber v-model:value="editForm.seconds" :min="1" :max="86400" style="width: 160px" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEdit = false">取消</NButton>
          <NButton type="primary" :loading="saveLoading" @click="handleSave">保存</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.scheduled-task-page {
  max-width: 960px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text1);
}
.running-badge {
  font-size: 12px;
  color: var(--accent-fg);
  background: var(--accent-bg-muted);
  padding: 2px 10px;
  border-radius: 10px;
}
.groups-container {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 20px;
  align-items: start;
}
.task-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--group-color) 8%, transparent), color-mix(in srgb, var(--group-color) 3%, transparent));
  border-left: 3px solid var(--group-color);
  border-radius: 6px;
}
.group-icon {
  font-size: 18px;
  line-height: 1;
}
.group-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text1);
  flex: 1;
}
.group-count {
  font-size: 11px;
  font-weight: 600;
  color: var(--group-color);
  background: color-mix(in srgb, var(--group-color) 12%, transparent);
  padding: 2px 8px;
  border-radius: 10px;
  line-height: 1.4;
}
.group-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.task-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 14px 16px;
  transition: box-shadow 0.2s, opacity 0.2s;
}
.task-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}
.task-card.disabled {
  opacity: 0.5;
}
.task-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.task-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text1);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-desc {
  font-size: 11px;
  color: var(--text2);
  margin-bottom: 8px;
}
.task-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.task-status {
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 4px;
}
.task-status.running {
  color: var(--accent-fg);
  background: var(--accent-bg-muted);
}
.task-status.stopped {
  color: var(--fg-subtle);
  background: rgba(0, 0, 0, 0.04);
}
.task-run-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text2);
  margin-bottom: 10px;
}
.run-label {
  color: var(--text3);
}
.run-time {
  font-family: monospace;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}
.task-error {
  margin-top: 4px;
  font-size: 11px;
  color: var(--danger-fg);
  background: var(--danger-bg-muted);
  padding: 4px 8px;
  border-left: 2px solid var(--danger-fg);
  border-radius: 2px;
  word-break: break-all;
  cursor: help;
}
.task-actions {
  display: flex;
  gap: 8px;
}
</style>
