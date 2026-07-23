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

/** 是否涨停: 只按【当日涨幅贴板】判定。
 *
 * v1.7.786 修口径: 原来 limit_up_days≥1(连板标签)优先返回 true, 但那个标签是
 * stock_tag_refresher 盘中用【已完成交易日】的日K算的连板数(见其 prefer_cache=True),
 * 表示"截至上一交易日仍在连板中", 与今天涨没涨完全无关 → 昨天涨停、今天下跌的票
 * 一整天都被算成"今日涨停"(实测瑞芯微 -3.91% 也被计入, 全池 11 只里 6 只是这么来的),
 * 而广度条「昨日」那行走后端 _compute_pool_breadth 是按当日收盘现算的, 两行口径还不一致。
 * 连板信息不丢: 行内仍有独立的「首板 / N连板」徽章(读 limit_up_days)。
 */
export function isLimitUp(
  pct: number | null | undefined,
  code: string,
  name?: string | null,
): boolean {
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
