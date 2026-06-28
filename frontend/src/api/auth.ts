import client from './client'
import type { OperationLog } from '../types'

export async function login(username: string, password: string) {
  const { data } = await client.post('/api/auth/login', { username, password })
  return data as { token: string; user: { id: number; username: string; role: string } }
}

export async function getMe() {
  const { data } = await client.get('/api/auth/me')
  return data as { id: number; username: string; role: string }
}

export async function listUsers() {
  const { data } = await client.get('/api/users')
  return data as { id: number; username: string; role: string; mobile?: string; lark_webhook?: string; lark_enabled?: number; created_at: string }[]
}

export async function updateUser(userId: number, updates: { username?: string; role?: string; mobile?: string; lark_webhook?: string; lark_enabled?: number }) {
  const { data } = await client.put(`/api/users/${userId}`, updates)
  return data as { ok: boolean }
}

export async function createUser(username: string, password: string, role: string = 'user') {
  const { data } = await client.post('/api/users', { username, password, role })
  return data as { ok: boolean; id: number; username: string }
}

export async function deleteUser(userId: number) {
  const { data } = await client.delete(`/api/users/${userId}`)
  return data as { ok: boolean }
}

export async function resetPassword(userId: number, password: string) {
  const { data } = await client.post(`/api/users/${userId}/reset-password`, { password })
  return data as { ok: boolean }
}

export async function fetchLogs(params: {
  page?: number; page_size?: number;
  action?: string | null; keyword?: string | null;
  date_from?: string | null; date_to?: string | null;
} = {}) {
  const query: Record<string, unknown> = { page: params.page ?? 1, page_size: params.page_size ?? 50 }
  if (params.action) query.action = params.action
  if (params.keyword) query.keyword = params.keyword
  if (params.date_from) query.date_from = params.date_from
  if (params.date_to) query.date_to = params.date_to
  const { data } = await client.get('/api/logs', { params: query })
  return data as { total: number; page: number; page_size: number; logs: OperationLog[] }
}

export async function fetchLogActions() {
  const { data } = await client.get('/api/logs/actions')
  return data as { actions: string[] }
}
