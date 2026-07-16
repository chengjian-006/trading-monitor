import client from './client'
import type { AppConfig, ThsGroup, ThsCompareResult, SignalConfig } from '../types'

export async function fetchConfig(): Promise<AppConfig> {
  const { data } = await client.get('/api/config')
  return data
}

export async function saveConfig(cfg: Partial<AppConfig>) {
  const { data } = await client.post('/api/config', cfg)
  return data
}

export async function testPushplus() {
  const { data } = await client.post('/api/config/test-pushplus')
  return data as { ok: boolean; msg: string }
}

export async function testLark(webhook: string) {
  const { data } = await client.post('/api/config/test-lark', { webhook })
  return data as { ok: boolean; msg: string }
}

export async function testSignalCard() {
  const { data } = await client.post('/api/config/test-signal-card')
  return data as { ok: boolean; msg: string }
}

export async function testSurgeCard() {
  const { data } = await client.post('/api/config/test-surge-card')
  return data as { ok: boolean; msg: string }
}

export async function fetchUserProfile() {
  const { data } = await client.get('/api/users/profile')
  return data as { lark_webhook: string; lark_enabled: number }
}

export async function saveUserProfile(profile: {
  lark_webhook?: string; lark_enabled?: number
}) {
  const { data } = await client.put('/api/users/profile', profile)
  return data
}

export async function testUserLarkPush() {
  const { data } = await client.post('/api/users/test-lark-push')
  return data as { ok: boolean; msg: string }
}

export async function fetchThsGroups() {
  const { data } = await client.get('/api/ths/groups')
  return data as { ok: boolean; msg?: string; path?: string; ths_path?: string; groups: ThsGroup[] }
}

export async function fetchThsCompare() {
  const { data } = await client.get('/api/ths/compare')
  return data as ThsCompareResult
}

export async function compareThsUpload(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await client.post('/api/ths/compare-upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
  return data as ThsCompareResult
}

export type ImportProgressCallback = (event: {
  type: 'progress' | 'status' | 'done'
  current?: number
  total?: number
  code?: string
  name?: string
  action?: string
  msg?: string
  imported?: number
  skipped?: number
}) => void

export async function importThsGroup(group_id: string, onProgress?: ImportProgressCallback) {
  const token = localStorage.getItem('token')
  const res = await fetch('/api/ths/import', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({ group_id }),
  })

  if (!onProgress) {
    const data = await res.json()
    return data as { ok: boolean; msg: string; imported?: number; skipped?: number }
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result = { ok: true, msg: '', imported: 0, skipped: 0 }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const event = JSON.parse(line.slice(6))
        onProgress(event)
        if (event.type === 'done') {
          result = { ok: true, msg: event.msg, imported: event.imported, skipped: event.skipped }
        }
      }
    }
  }
  return result
}

export async function saveThsPath(ths_path: string) {
  const { data } = await client.post('/api/ths/path', { ths_path })
  return data as { ok: boolean }
}

export interface PushPref {
  id: number
  kind: string
  kind_label: string
  target: string
  until_date: string
}

export async function fetchPushPrefs() {
  const { data } = await client.get('/api/quick/prefs')
  return data as { prefs: PushPref[] }
}

export async function revokePushPref(id: number) {
  const { data } = await client.post(`/api/quick/prefs/${id}/revoke`)
  return data as { ok: boolean }
}

export async function fetchSignalConfig(): Promise<SignalConfig> {
  const { data } = await client.get('/api/signal-config')
  return data
}

export async function saveSignalConfig(cfg: SignalConfig) {
  const { data } = await client.post('/api/signal-config', cfg)
  return data as { ok: boolean }
}

export async function resetSignalConfig() {
  const { data } = await client.post('/api/signal-config/reset')
  return data as { ok: boolean; config: SignalConfig }
}
