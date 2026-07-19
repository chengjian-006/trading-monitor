import client from './client'

export interface ReviewSignalItem {
  signal_name: string
  date: string
  direction: string
}

export interface ReviewSignalHistory {
  recent: ReviewSignalItem[]
  n: number
}

export interface ReviewModelWinrate {
  model_name: string
  win_rate_3m: number | null
  n_3m: number | null
}

export interface ReviewRiskFlagsData {
  has_data: true
  score: number | null
  flags: string[]
}
export interface ReviewRiskFlagsEmpty {
  has_data: false
}
export type ReviewRiskFlags = ReviewRiskFlagsData | ReviewRiskFlagsEmpty

export interface ReviewSector {
  board_strength: string | null
  sector_rank: string | null
  hot_themes: string[] | null
}

export interface ReviewHoldingData {
  is_holding: true
  cost: number | null
  float_pct: number | null
  entry_model: string | null
}
export interface ReviewHoldingEmpty {
  is_holding: false
}
export type ReviewHolding = ReviewHoldingData | ReviewHoldingEmpty

export interface ReviewNearBuyData {
  approaching: true
  model: string | null
  gap_pct: number | null
}
export interface ReviewNearBuyEmpty {
  approaching: false
}
export type ReviewNearBuy = ReviewNearBuyData | ReviewNearBuyEmpty

export interface StockReviewFacts {
  code: string
  name: string
  signal_history: ReviewSignalHistory
  model_winrate: ReviewModelWinrate[]
  risk_flags: ReviewRiskFlags
  sector: ReviewSector
  holding: ReviewHolding
  near_buy: ReviewNearBuy
}

export interface StockReview {
  facts: StockReviewFacts
  narrative: string | null
  as_of: string
  cached: boolean
}

export async function getStockReview(code: string): Promise<StockReview> {
  const { data } = await client.get(`/api/stock/${code}/review`)
  return data
}
