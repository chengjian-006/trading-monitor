// 买点模型「临时参数」key → 中文显示名。
// 用于模型回测页等需要展示后端 DEFAULT_SIGNAL_CONFIG 原始参数 key 的地方,
// 避免把 intraday_earliest_minute / vol_mult_avg10 这类英文 key 直接甩给用户。
//
// 单一真相源约定(见 .claude/skills/model-roster-sync):
//   后端 DEFAULT_SIGNAL_CONFIG 新增/改名任何数值或开关参数时,这里必须同步加一条中文,
//   否则模型回测页会退化成直接显示英文 key(paramLabel 的兜底)。
// 用词与 SignalConfigView.vue 的 label 保持一致,两页不得各说一套。

export const PARAM_LABELS: Record<string, string> = {
  // —— 通用(多模型共用)——
  intraday_earliest_minute: '盘中最早触发(分钟, 0=不限)',
  min_full_day_amount: '全天预估成交额下限(亿元)',
  min_amount_now: '实时累计成交额下限(亿元)',
  breakout_pct: '突破昨高门槛(%)',
  shrink_ratio: '回踩日缩量上限(×近10日均量)',
  amount_avg_window: '放量对比均量窗口(日)',
  vol_mult_avg10: '放量确认(×近N日均量)',

  // —— 回踩MA10 / 回踩MA20 ——
  require_prior_rally: '需前置主升浪',
  rally_peak_within_bars: '主升浪峰值距今上限(交易日)',
  ma20_touch_pct: '回踩MA容差(±%)',

  // —— 缩量突破 ——
  vol_mult_prev: '放量确认(×昨日量)',
  REQ_PREV_SHADOW: '需昨日带下影线',
  PREV_SHADOW_MIN: '昨日下影最小占比',
  zt_setup_skip: '排除昨日涨停封板(假缩量)',
  zt_setup_pct_min: '认定昨日封板的涨幅阈值(%)',
  chase_limit_skip: '排除现价逼近涨停的追高',
  chase_limit_buffer_pct: '现价距涨停板视为接近的阈值(%)',

  // —— 平台突破 ——
  L: '平台横盘窗口(根K)',
  A: '平台振幅上限(收盘)',
  N_PRIOR: '中继前置回看(日)',
  R: '前置主升最小涨幅',
  REQ_PRIOR: '需中继前置主升',
  REQ_RISE: '需平台缓升',
  RISE_MIN: '平台缓升下限',
  RISE_MAX: '平台缓升上限',
  BUF: '突破上沿缓冲',
  V: '放量倍数(×平台均量)',
  REQ_VOL: '需放量确认',
  REQ_HOLD: '需平台期不深破MA20',

  // —— 强势起点 ——
  lookback_days: '弱势极限回溯天数',
  vol_multiplier: '今日量/基准量倍数',
  vol_avg_window: '均量回溯窗口(日)',
  min_vol_vs_avgN: '放量确认(×近N日均量)',
  max_gain_from_base_pct: '距基准涨幅上限(%)',
  min_pct_change: '当日涨幅下限(%)',

  // —— 弱势极限 ——
  vol_floor_window: '地量回溯窗口(日)',
  vol_floor_tolerance: '地量容差(×最低量)',
  vol_shrink_avg10_ratio: '10日均量上限(×)',
  ma10_above_max_pct: 'MA10上方最大偏离(%)',
  ma10_below_max_pct: 'MA10下方最大偏离(%)',
  ma20_above_max_pct: 'MA20上方最大偏离(%)',
  ma20_below_max_pct: 'MA20下方最大偏离(%)',
  prior_weak_days_required: '前置确认(连续N日同满足, 0=关)',
}

/** 取参数中文名;未登记的 key 兜底原样返回(并应补进 PARAM_LABELS)。 */
export function paramLabel(key: string): string {
  return PARAM_LABELS[key] ?? key
}
