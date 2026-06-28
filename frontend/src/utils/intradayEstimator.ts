// 盘中虚拟全天成交额/量预估 — 前端口移植 backend/services/intraday_estimator.py
// 时点系数表保持与后端一致, 任何调整需两边同步。

// 时点累计成交占比表(U型: 开盘前置/午间平/尾盘集合竞价回升)。与后端 intraday_estimator.py 必须同步。
// v1.7.518 实测标定: 全市场5min真实成交额近30交易日均值(详见后端同名表注释)。
// 旧手填经验值早盘偏低(10:00=0.24)致开盘外推放大约1.4倍, 已换实测值。
const TIME_COEF_TABLE: [number, number][] = [
  [9 * 60 + 30, 0.000],
  [9 * 60 + 45, 0.220],
  [10 * 60 +  0, 0.328],
  [10 * 60 + 15, 0.409],
  [10 * 60 + 30, 0.469],
  [10 * 60 + 45, 0.521],
  [11 * 60 +  0, 0.564],
  [11 * 60 + 15, 0.603],
  [11 * 60 + 30, 0.639],
  [13 * 60 +  0, 0.639],
  [13 * 60 + 15, 0.694],
  [13 * 60 + 30, 0.736],
  [13 * 60 + 45, 0.776],
  [14 * 60 +  0, 0.813],
  [14 * 60 + 15, 0.851],
  [14 * 60 + 30, 0.888],
  [14 * 60 + 45, 0.935],
  [14 * 60 + 57, 0.978],
  [15 * 60 +  0, 1.000],
]

function interpCoef(minutesInDay: number): number {
  if (minutesInDay <= TIME_COEF_TABLE[0][0]) return 0
  if (minutesInDay >= TIME_COEF_TABLE[TIME_COEF_TABLE.length - 1][0]) return 1
  let lo = 0
  let hi = TIME_COEF_TABLE.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (TIME_COEF_TABLE[mid][0] < minutesInDay) lo = mid + 1
    else hi = mid
  }
  const [k1, v1] = TIME_COEF_TABLE[lo - 1]
  const [k2, v2] = TIME_COEF_TABLE[lo]
  if (k2 === k1) return v1
  return v1 + (v2 - v1) * (minutesInDay - k1) / (k2 - k1)
}

/**
 * 根据当前累计成交额(元)与时点外推今日预估全天成交额(元)。
 *
 * - 工作日 09:30-15:00 盘中: 线性插值时点系数后反推
 * - 工作日 ≥15:00 盘后: 当前累计即全天值
 * - 工作日 <09:30 / 周末: 无法判断"今日", 返回 null
 * - 输入为 0/空: 返回 null
 */
export function estimateFullDayAmount(currentAmount: number | null | undefined, now: Date = new Date()): number | null {
  if (!currentAmount || currentAmount <= 0) return null
  const day = now.getDay()
  if (day === 0 || day === 6) return null
  const minutes = now.getHours() * 60 + now.getMinutes()
  if (minutes < 9 * 60 + 30) return null
  if (minutes >= 15 * 60) return currentAmount
  let mins = minutes
  if (mins > 11 * 60 + 30 && mins < 13 * 60) mins = 11 * 60 + 30
  const coef = interpCoef(mins)
  if (coef <= 0) return null
  if (coef >= 1) return currentAmount
  return currentAmount / coef
}
