// xlsx 是大库(~100KB gzip), 改成只在真正点导出时动态加载, 不进首屏池子/复盘 chunk。
// 纯格式化 helper resonanceLevel 已拆到 ./poolFormat, 池子常驻代码不再连 xlsx。
import type { ReviewSignalRow, ReviewSummaryRow } from '../api/signals'
import type { Stock, Signal } from '../types'
import { resonanceLevel } from './poolFormat'

const pct = (v: number | null) => (v == null ? '' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`)

// 股票池导出: 当前展示列表(由调用方按当前排序传入), 含人气/成交额排名/双榜共振/策略/当前信号
export async function exportPoolXlsx(
  stocks: Stock[],
  amountRankMap: Record<string, number>,
  signalsByCode: Map<string, Signal[]>,
) {
  const XLSX = await import('xlsx')
  const fmtSignals = (code: string) => {
    const list = signalsByCode.get(code)
    if (!list || !list.length) return ''
    return list.map(s => {
      const dir = s.direction === 'buy' || s.direction === 'add' ? '买'
        : s.direction === 'sell' || s.direction === 'reduce' ? '卖' : '提示'
      return `${dir}:${s.signal_name}`
    }).join('; ')
  }
  const aoa = stocks.map(s => {
    const amtR = amountRankMap[s.code]
    return {
      代码: s.code,
      名称: s.name,
      类型: s.trade_type === 'short' ? '短线' : '中线',
      状态: s.status === 'hold' ? '持仓' : '观察',
      关注: s.focused ? '是' : '',
      人气排名: s.popularity_rank == null ? '' : (s.popularity_rank > 100 ? '100名外' : s.popularity_rank),
      成交额排名: amtR != null && amtR <= 100 ? amtR : '100名外',
      双榜共振: resonanceLevel(s.popularity_rank, amtR),
      现价: s.price ?? '',
      '涨幅%': s.pct_change ?? '',
      '5日%': s.pct_5d ?? '',
      '涨速%': s.speed ?? '',
      '成交额(亿)': s.amount ? Math.round((s.amount / 1e8) * 100) / 100 : '',
      '换手率%': s.turnover ?? '',
      量比: s.volume_ratio ?? '',
      行业: s.industry ?? '',
      策略: s.strategy ?? '',
      当前信号: fmtSignals(s.code),
    }
  })
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(aoa), '股票池')
  const now = new Date()
  const p2 = (n: number) => String(n).padStart(2, '0')
  const ts = `${now.getFullYear()}${p2(now.getMonth() + 1)}${p2(now.getDate())}_${p2(now.getHours())}${p2(now.getMinutes())}`
  XLSX.writeFile(wb, `股票池_${ts}.xlsx`)
}

export async function exportReviewXlsx(
  rows: ReviewSignalRow[], summary: ReviewSummaryRow[], start: string, end: string,
) {
  const XLSX = await import('xlsx')
  const detailAoa = rows.map(r => ({
    代码: r.code, 名称: r.name, 信号类型: r.signal_name, 方向: r.direction,
    触发日: r.trigger_date, 触发价: r.trigger_price, 现价: r.cur_price,
    当前收益: pct(r.cur_ret_pct), 区间最大浮盈: pct(r.max_gain_pct), 区间最大浮亏: pct(r.max_dd_pct),
    'T+1': pct(r.t1_pct), 'T+3': pct(r.t3_pct), 'T+5': pct(r.t5_pct),
    评估: r.outcome ?? '待评估',
    计划止盈: r.tp_label, 止盈目标价: r.tp_price, 止盈触及: r.tp_label ? (r.tp_hit ? '是' : '否') : '',
    计划止损: r.sl_label, 止损价: r.sl_price, 止损触及: r.sl_label ? (r.sl_hit ? '是' : '否') : '',
    时停其他出场: r.other_exit, 形态详情: r.detail,
  }))
  const sumAoa = summary.map(g => ({
    信号类型: g.signal_id === '__ALL__' ? '全部' : g.signal_name, 笔数: g.count,
    胜率: pct(g.win_rate), 均当前收益: pct(g.avg_cur_ret), 中位: pct(g.median_cur_ret),
    均最大浮盈: pct(g.avg_max_gain), 均最大浮亏: pct(g.avg_max_dd),
    T5均: pct(g.avg_t5), success率: pct(g.success_rate),
  }))
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(detailAoa), '个股明细')
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(sumAoa), '按类型汇总')
  XLSX.writeFile(wb, `区间复盘_${start}_${end}.xlsx`)
}
