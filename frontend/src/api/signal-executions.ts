import client from './client'

export interface SignalExecution {
  id: number
  user_id: number
  signal_pk: number
  code: string
  action: 'executed' | 'skipped'
  actual_price: number | null
  actual_qty: number | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface UpsertExecutionPayload {
  signal_pk: number
  code: string
  action: 'executed' | 'skipped'
  actual_price?: number | null
  actual_qty?: number | null
  notes?: string | null
}

export async function upsertSignalExecution(payload: UpsertExecutionPayload): Promise<{ id: number; ok: boolean }> {
  const { data } = await client.post('/api/signal-executions', payload)
  return data
}

export async function deleteSignalExecution(signalPk: number): Promise<void> {
  await client.delete(`/api/signal-executions/${signalPk}`)
}

export async function fetchSignalExecutions(signalPks?: number[]): Promise<SignalExecution[]> {
  const params: Record<string, string> = {}
  if (signalPks && signalPks.length) {
    params.signal_pks = signalPks.join(',')
  }
  const { data } = await client.get('/api/signal-executions', { params })
  return data
}
