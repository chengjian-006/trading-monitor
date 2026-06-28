// 池子页常驻用的纯格式化 helper, 不依赖 xlsx 等重库, 单独成文件避免被一起打进首屏 chunk。

// 双榜共振强弱: 人气排名 与 成交额排名 同时进前100 时, 按较差名次定档
export function resonanceLevel(popR: number | null | undefined, amtR: number | null | undefined): string {
  if (popR == null || amtR == null || popR > 100 || amtR > 100) return ''
  const worst = Math.max(popR, amtR)
  if (worst <= 20) return '超强'
  if (worst <= 50) return '强'
  return '一般'
}
