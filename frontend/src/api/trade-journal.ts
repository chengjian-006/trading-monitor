import client from './client'

export interface JournalEntry {
  id: number
  code: string
  name: string
  side: string            // buy / sell / hold / note
  trade_date: string | null
  price: number | null
  qty: number | null
  reason: string | null
  emotion: string
  review: string | null
  created_at: string
  updated_at: string
}

export type JournalInput = Omit<JournalEntry, 'id' | 'created_at' | 'updated_at'>

export const fetchJournal = () =>
  client.get<JournalEntry[]>('/api/trade-journal').then(r => r.data)

export const createJournal = (body: Partial<JournalInput>) =>
  client.post<{ ok: boolean; id: number }>('/api/trade-journal', body).then(r => r.data)

export const updateJournal = (id: number, body: Partial<JournalInput>) =>
  client.put<{ ok: boolean }>(`/api/trade-journal/${id}`, body).then(r => r.data)

export const deleteJournal = (id: number) =>
  client.delete<{ ok: boolean }>(`/api/trade-journal/${id}`).then(r => r.data)
