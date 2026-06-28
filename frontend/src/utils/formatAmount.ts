// 成交额统一格式化 —— 全站成交额默认单位「亿元」, 保留2位小数(见 coding-standards「成交额默认单位亿元」)。
// 超过 1 万亿进位显示「万亿(2位)」。空值/非数返回占位符(默认 '-')。

/** 入参单位「元」(后端原始成交额) → 亿(2位) / 万亿(2位)。 */
export function formatYi(yuan: number | null | undefined, dash = '-'): string {
  if (yuan == null || !isFinite(Number(yuan))) return dash
  const yi = Number(yuan) / 1e8
  if (Math.abs(yi) >= 10000) return (yi / 10000).toFixed(2) + '万亿'
  return yi.toFixed(2) + '亿'
}

/** 入参已是「亿」(后端 _yi 字段) → 亿(2位) / 万亿(2位)。 */
export function formatYiFromYi(yi: number | null | undefined, dash = '-'): string {
  if (yi == null || !isFinite(Number(yi))) return dash
  const v = Number(yi)
  if (Math.abs(v) >= 10000) return (v / 10000).toFixed(2) + '万亿'
  return v.toFixed(2) + '亿'
}

/** 带正负号的成交额差额, 入参单位「亿」。 */
export function formatYiDelta(yi: number | null | undefined, dash = '—'): string {
  if (yi == null || !isFinite(Number(yi))) return dash
  const v = Number(yi)
  const sign = v >= 0 ? '+' : '-'
  const abs = Math.abs(v)
  return sign + (abs >= 10000 ? (abs / 10000).toFixed(2) + '万亿' : abs.toFixed(2) + '亿')
}
