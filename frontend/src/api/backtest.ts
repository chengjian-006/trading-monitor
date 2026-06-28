import client from './client'

// 回测类请求耗时长(全池逐日重模拟, lookback 大时可达数十秒), 覆盖默认 10s 超时
const BACKTEST_TIMEOUT = 180000

// ── 模型回测页: 配参 + 点击回测(日线/5分钟 × 自选股/全市场) ──
export type ModelParams = Record<string, number | boolean>

export interface BacktestModel { id: string; name: string; params: ModelParams }

export async function listBacktestModels(): Promise<BacktestModel[]> {
  const { data } = await client.get('/api/backtest/models')
  return data.models as BacktestModel[]
}

export interface ModelStat { n: number; win: number; avg: number; pf: number }

// 一次买入对应的一条卖出腿(卖半→清剩两腿 / 非卖半一腿)
export interface ModelTradeLeg {
  pos: number             // 该腿仓位% (50 卖半 / 100 整单)
  reason: string          // 该腿出场机制文案
  date: string            // 该腿成交日期 YYYY-MM-DD
  price: number           // 该腿成交价
  ret_pct?: number        // 该腿毛收益%(相对买入价; 新, 旧记录缺)
  hold?: number           // 该腿持有交易日数(新, 旧记录缺)
}

export interface ModelTrade {
  code: string
  name: string
  buy_date: string        // 买入日期
  model: string           // 触发模型
  detail: string          // 触发详情(检测器理由)
  buy_price: number       // 买入价
  exit_reason: string     // 出场机制(整单, 旧字段保留兼容)
  exit_date: string       // 出场日期
  exit_price: number      // 出场价
  hold_days: number       // 持股交易日数
  ret_pct: number         // 净收益%
  took_half: boolean      // 是否先止盈卖半
  legs?: ModelTradeLeg[]  // 出场分腿(新; 旧历史记录可能缺失, 渲染时回退 exit_reason/date/price)
  mfe_pct?: number        // 持有期最高浮盈%(新, 旧记录缺)
  mfe_day?: number        // 最高浮盈发生在第几个交易日(新)
  mae_pct?: number        // 持有期最大浮亏%(新, 旧记录缺)
  mae_day?: number        // 最大浮亏发生在第几个交易日(新)
}

export interface ModelRunResult {
  model_id: string
  model_name: string
  koujing: string
  overall: ModelStat
  monthly?: Record<string, ModelStat>
  scanned: number
  trades?: ModelTrade[]
  trades_total?: number
  trades_truncated?: boolean
}

export interface ModelRunResponse {
  ok: boolean
  msg?: string
  status?: 'done' | 'running'
  result?: ModelRunResult
  job_id?: string
  total?: number
  window?: { start: string; end: string }
}

export async function runModelBacktest(req: {
  model_id: string
  scope: 'pool' | 'all'
  koujing: 'daily' | '5m'
  lookback_days: number
  temp_config?: Record<string, ModelParams>
}): Promise<ModelRunResponse> {
  const { data } = await client.post('/api/backtest/model-run', req, { timeout: BACKTEST_TIMEOUT })
  return data
}

export interface JobProgress {
  done: number
  total: number
  phase?: string   // 当前阶段(准备数据 / 日线逐只回测 / 5分钟逐只回测 …)
  note?: string    // 当前正在处理的条目(如股票代码)
}

export interface ModelJobResponse {
  ok: boolean
  status: 'running' | 'done' | 'error'
  progress: JobProgress
  result: ModelRunResult | null
  error: string | null
}

export async function getModelJob(jobId: string): Promise<ModelJobResponse> {
  const { data } = await client.get(`/api/backtest/model-job/${jobId}`)
  return data
}

// ── 回测历史记录 ──
export interface BacktestRunSummary {
  id: number
  model_id: string
  model_name: string
  scope: string
  koujing: string
  lookback_days: number
  window_start: string
  window_end: string
  params: ModelParams
  overall: ModelStat
  scanned: number
  trades_total: number
  trades_truncated: boolean
  created_at: string
}

export interface BacktestRunDetail extends BacktestRunSummary {
  monthly: Record<string, ModelStat>
  trades: ModelTrade[]
}

export async function listModelRuns(limit = 100): Promise<BacktestRunSummary[]> {
  const { data } = await client.get('/api/backtest/model-runs', { params: { limit } })
  return data.runs as BacktestRunSummary[]
}

export async function getModelRun(runId: number): Promise<BacktestRunDetail> {
  const { data } = await client.get(`/api/backtest/model-runs/${runId}`)
  return data.run as BacktestRunDetail
}

export async function deleteModelRun(runId: number): Promise<boolean> {
  const { data } = await client.delete(`/api/backtest/model-runs/${runId}`)
  return data.ok as boolean
}
