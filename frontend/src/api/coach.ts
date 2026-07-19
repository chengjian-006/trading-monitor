import client from './client'

export interface CoachGroupStat {
  n: number
  win_rate: number
  avg_pnl_pct: number
}

export interface CoachModelStat {
  model_name: string
  n: number
  win_rate: number
  avg_pnl_pct: number | null
  market_win_rate_3m: number | null
  exec_gap: number | null
}

export interface CoachPnlDist {
  best_pct: number
  worst_pct: number
  avg_pct: number | null
}

export interface CoachCycle {
  hold_days_avg: number | null
  winner_hold_avg: number | null
  loser_hold_avg: number | null
  pnl_dist: CoachPnlDist
}

export interface CoachStopDiscipline {
  stop_exit_rounds: number
  stop_exit_ratio: number
}

export interface CoachHabits {
  winner_hold_avg: number | null
  loser_hold_avg: number | null
  loser_holds_longer: boolean | null
  scaled_out_ratio: number
  stop_discipline: CoachStopDiscipline
}

export interface CoachFacts {
  window: { start: string; end: string }
  n_closed: number
  n_scored: number
  listen_vs_self: { listen: CoachGroupStat; self: CoachGroupStat }
  by_model: CoachModelStat[]
  cycle: CoachCycle
  habits: CoachHabits
}

export interface CoachReport {
  facts: CoachFacts
  narrative: string | null
  as_of: string
  cached: boolean
}

export async function getCoachReport(start: string, end: string): Promise<CoachReport> {
  const { data } = await client.get('/api/coach/report', { params: { start, end } })
  return data
}
