import client from './client'

export interface ApiHealthCheck {
  status: 'ok' | 'fail' | 'unknown'
  latency_ms: number
  error: string
  checked_at?: string
  ok?: number      // 窗口内成功次数 (后端 api_health._build_state 实际返回)
  total?: number   // 窗口内总调用次数
}

export interface ApiHealthSource {
  label: string
  summary: 'ok' | 'degraded' | 'fail' | 'unknown'
  checks: Record<string, ApiHealthCheck>
}

export interface ApiHealthFunction {
  id: string
  label: string
  status: 'ok' | 'fail' | 'unknown'
  reason: string
}

export interface ApiHealthSummary {
  total: number
  ok: number
  fail: number
  unknown: number
}

export interface FailingTask {
  job_id: string
  name: string
  consecutive_failures: number
  last_error_msg: string
  last_run_at: string
}

export interface ApiHealthState {
  checked_at: string | null
  sources: Record<string, ApiHealthSource>
  usage_labels: Record<string, string>
  functions?: ApiHealthFunction[]
  summary?: ApiHealthSummary
  failing_tasks?: FailingTask[]
}

export async function fetchApiHealth(): Promise<ApiHealthState> {
  const { data } = await client.get('/api/health/external')
  return data as ApiHealthState
}

export async function recheckApiHealth(): Promise<ApiHealthState> {
  const { data } = await client.post('/api/health/external/recheck')
  return data as ApiHealthState
}
