import apiClient from './client'

export interface SubstanceResult {
  code: string
  name: string
  substance_score: number       // 0~5
  substance_note: string
  substance_analysis: string    // Markdown 报告
  substance_updated_at: string | null
}

export interface AnalyzeResponse {
  ok: boolean
  report: string | null
  model: string
  error: string | null
}

/** 调 AI 生成真受益核查报告(自动入库) */
export async function analyzeSubstance(code: string, theme: string): Promise<AnalyzeResponse> {
  const { data } = await apiClient.post('/api/substance/analyze', { code, theme, persist: true })
  return data
}

/** 保存人工评分 + 备注 */
export async function saveSubstanceScore(code: string, score: number, note: string = '') {
  const { data } = await apiClient.post('/api/substance/score', { code, score, note })
  return data
}

/** 读取已有报告 */
export async function getSubstance(code: string): Promise<SubstanceResult> {
  const { data } = await apiClient.get(`/api/substance/${code}`)
  return data
}
