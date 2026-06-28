// 中文名 → 拼音首字母(小写)。供股票池"拼音缩写"模糊检索, 运行时零依赖。
// 数据表见 pinyin-table.ts(pypinyin 离线生成)。非汉字原样转小写。
import { PY_BASE, PY_TABLE } from './pinyin-table'

const PY_END = PY_BASE + PY_TABLE.length - 1

export function pinyinInitials(name: string): string {
  if (!name) return ''
  let out = ''
  for (const ch of name) {
    const cp = ch.codePointAt(0) ?? 0
    if (cp >= PY_BASE && cp <= PY_END) {
      const c = PY_TABLE[cp - PY_BASE]
      out += c === '_' ? '' : c
    } else {
      out += ch.toLowerCase()
    }
  }
  return out
}
