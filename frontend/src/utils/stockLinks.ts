// 个股外部行情页链接(同花顺 / 东方财富) — 点开可看该股分时+日K的网页版。
// 同花顺 stockpage 无需市场前缀、全 A 通用, 手机端自动跳 m.10jqka;
// 东财需 sh/sz/bj 前缀, 按代码段判定。

/** 沪深北市场前缀。注意 92x=北交所须在 9x=沪 之前判(否则被沪B 9 抢先误判)。未知返 '' */
export function marketPrefix(code: string): 'sh' | 'sz' | 'bj' | '' {
  const c = String(code || '')
  if (/^(4|8|92)/.test(c)) return 'bj'        // 北交所: 43/83/87/92 段
  if (/^(6|9)/.test(c)) return 'sh'           // 沪主板 6 / 科创 688 / 沪B 900
  if (/^(0|2|3)/.test(c)) return 'sz'         // 深主板 0 / 中小 002 / 创业 3 / 深B 200
  return ''
}

/** 同花顺网页版个股页(分时+日K), 全 A 通用 */
export function thsStockUrl(code: string): string {
  return `https://stockpage.10jqka.com.cn/${code}/`
}

/** 东方财富网页版个股页(分时+日K) */
export function emStockUrl(code: string): string {
  const p = marketPrefix(code)
  if (p === 'bj') return `https://quote.eastmoney.com/bj/${code}.html`
  if (p) return `https://quote.eastmoney.com/${p}${code}.html`
  return `https://quote.eastmoney.com/${code}.html`
}

/** 新标签打开外链(带 noopener 防反向控制) */
export function openExternal(url: string): void {
  window.open(url, '_blank', 'noopener')
}
