import client from './client'
import type { ScheduledTask } from '../types'

export async function fetchScheduledTasks(): Promise<ScheduledTask[]> {
  const { data } = await client.get('/api/scheduled-tasks')
  return data
}

export async function updateScheduledTask(jobId: string, payload: Partial<ScheduledTask>) {
  const { data } = await client.put(`/api/scheduled-tasks/${jobId}`, payload)
  return data as { ok: boolean }
}

export async function toggleScheduledTask(jobId: string, enabled: boolean) {
  const { data } = await client.post(`/api/scheduled-tasks/${jobId}/toggle`, { enabled })
  return data as { ok: boolean }
}

export async function triggerScheduledTask(jobId: string) {
  const { data } = await client.post(`/api/scheduled-tasks/${jobId}/trigger`)
  return data as { ok: boolean }
}
