// A股板幅判定 — 按代码前缀(+ST名)给出涨跌停板幅, 供自选池统计栏等前端聚合用。
// 口径对齐后端 signal_engine 的板幅感知: 主板10% / 创业板(300·301)·科创(688·689)20% /
// 北交所(4x·8x·920)30% / 主板ST 5%。创业板·科创板的ST仍按20%(板规则优先)。

/** 是否 ST / *ST 股(名称含 ST) */
export function isST(name?: string | null): boolean {
  return !!name && /st/i.test(name)
}

/** 该股涨跌停板幅(%): 10 / 20 / 30 / 5(主板ST) */
export function limitPct(code: string, name?: string | null): number {
  const c = code || ''
  let base = 10
  if (/^(688|689)/.test(c)) base = 20        // 科创板
  else if (/^(30|31)/.test(c)) base = 20     // 创业板 300/301
  else if (/^(4|8|92)/.test(c)) base = 30    // 北交所 43x/83x/87x/88x/920
  // 主板ST 5%(创业·科创ST仍20%, 故仅 base===10 时降到5)
  if (base === 10 && isST(name)) return 5
  return base
}

// 距板 ≤ 此% 即视为封板(封板价四舍五入误差 + 尾盘微差容差)
const LIMIT_EPS = 0.3

/** 是否涨停: limit_up_days≥1(同花顺标签)优先, 否则按现涨幅贴板判定 */
export function isLimitUp(
  pct: number | null | undefined,
  code: string,
  name?: string | null,
  limitUpDays?: number | null,
): boolean {
  if ((limitUpDays ?? 0) >= 1) return true
  if (pct == null) return false
  return pct >= limitPct(code, name) - LIMIT_EPS
}

/** 是否跌停: 按现跌幅贴板判定 */
export function isLimitDown(
  pct: number | null | undefined,
  code: string,
  name?: string | null,
): boolean {
  if (pct == null) return false
  return pct <= -limitPct(code, name) + LIMIT_EPS
}
