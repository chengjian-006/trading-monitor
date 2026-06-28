<script setup lang="ts">
import { onMounted, ref, toRaw, computed } from 'vue'
import {
  NSwitch, NInputNumber, NButton, NSkeleton, NIcon, NTabs, NTabPane, NTag, NCard, NPopselect,
} from 'naive-ui'
import CursorTooltip from '../components/common/CursorTooltip.vue'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { SaveOutline, RefreshOutline, InformationCircleOutline, AddOutline, CloseOutline } from '@vicons/ionicons5'
import { fetchSignalConfig, saveSignalConfig, resetSignalConfig } from '../api/config'
import type { SignalConfig } from '../types'

const message = useGlobalMessage()
const loading = ref(false)
const saving = ref(false)
const config = ref<SignalConfig>({})
const savedSnapshot = ref<SignalConfig>({})
const savingCardId = ref('')

interface ParamDef {
  key: string
  label: string
  min?: number
  max?: number
  step?: number
  recommend?: string
}

type SignalSource = 'user' | 'recommended'

interface SignalDef {
  id: string
  name: string
  source: SignalSource
  tags?: string[]
  description?: string
  params: ParamDef[]
}

interface GroupDef {
  title: string
  signals: SignalDef[]
}

// v1.7.38: 拆出独立 "底层概念" Tab — MAIN_RALLY / SCORE_THEME 被多个信号引用,改它会影响全局
const conceptGroups: GroupDef[] = [
  {
    title: '主升浪定义（被所有短线买卖信号引用，改它会影响全局）',
    signals: [
      {
        id: 'MAIN_RALLY', name: '主升浪定义', source: 'user', tags: ['底层概念'],
        description: '主升浪 = 起点放量上穿MA10 + 30日窗口内涨幅≥15% + 当前从峰值回撤≤8% + 窗口内未出现连续两日收盘破MA10\n\n起点：close 从 MA10 下方上穿 MA10 当日的 close (且当日量 ≥ 5日均量 × 上穿放量倍数)\n两个状态：\n  · ever_qualified  30日窗口内曾经形成过主升浪 (上穿+放量+峰值涨幅≥15%)\n  · in_rally        当前仍处于主升浪 (ever_qualified + 当前回撤≤8% + 未出现连续两日破MA10)\n结束信号：窗口内某T日close<MA10 且 T+1日close<MA10 (对应 SELL_BREAK_MA10 的"真跌破")',
        params: [
          { key: 'lookback_n', label: '追踪窗口(交易日)', min: 10, max: 60, step: 1, recommend: '30' },
          { key: 'breakout_vol_mult', label: '上穿放量倍数', min: 1.0, max: 3.0, step: 0.1, recommend: '1.2' },
          { key: 'min_gain_pct', label: '涨幅门槛(%)', min: 5, max: 50, step: 1, recommend: '15' },
          { key: 'max_drawdown_pct', label: '回撤上限(%)', min: 3, max: 20, step: 1, recommend: '8' },
        ],
      },
    ],
  },
  {
    title: '主流题材定义（被真假强势评分 C 维度引用，改它会影响强弱评分）',
    signals: [
      {
        id: 'SCORE_THEME', name: '主流题材：板块强势+龙头大涨+市场焦点', source: 'user', tags: ['底层概念'],
        description: '主流题材 = 客观有好故事 + 主观资金认可 (用资金行为反推故事)\n\n阶段 1 极简版三条规则:\n  ① 板块今日涨幅 ≥ 3% (板块整体强势)\n  ② 板块龙头(top1)涨幅 ≥ 7% (有标杆/赚钱效应)\n  ③ 板块涨幅榜排名 ≤ 前 5 (是市场焦点)\n\n语义：第10课原话"行业龙头要大涨、情绪票要翻倍"——三条全过才算主流\n后续阶段(2~5)可加入:涨停数、持续性、资金流、主观面',
        params: [
          { key: 'min_sector_pct', label: '板块涨幅门槛(%)', min: 1.0, max: 8.0, step: 0.5, recommend: '3.0' },
          { key: 'min_leader_pct', label: '龙头涨幅门槛(%)', min: 3.0, max: 10.0, step: 0.5, recommend: '7.0' },
          { key: 'max_rank', label: '涨幅榜排名上限', min: 3, max: 20, step: 1, recommend: '5' },
        ],
      },
    ],
  },
]

const preWarningGroups: GroupDef[] = [
  {
    title: '前置预警（在买点前一步的预警信号）',
    signals: [
      {
        id: 'BUY_WEAK_EXTREME', name: '弱势极限（左侧）：地量+趋势未破+贴近MA10或MA20', source: 'user', tags: ['前置预警'],
        description: '【主升浪回踩】前面有过涨幅≥15%的主升浪, 且峰值距今≤15个交易日\n【绝对地量】今日成交量 ≤ 近10日最低成交量 × 1.1\n【相对缩量】今日成交量 ≤ 近10日均量 × 0.80  (v1.7.164: 0.75→0.80 轻度放宽)\n【长期未破】收盘价 > MA60\n【中期未破】收盘价 > MA20\n【贴近锚点】close 距 MA10 ∈ [-2%, +2%]  OR  close 距 MA20 ∈ [-2%, +2%]  ← v1.7.79: MA10/MA20 合并, 任一命中即可\n【前置确认】v1.7.155: 默认要求前 1 个交易日(T-1)也满足以上六条 — 排除孤日昙花式缩量 (v1.7.150 上线时默认 N=2 太严, 回测 30 天仅 1 笔, 降到 N=1 后 30 天 6 笔), 0 = 关闭\n【盘中早盘门槛】v1.7.86: 默认 10:00 之前不触发 — 9:30-10:00 虚拟全天量噪声过大\n【盘中虚拟】10:00 后用虚拟全天预估成交量\n推送时附带命中锚点(MA10/MA20)及距最近一根均线的位置信息',
        params: [
          { key: 'intraday_earliest_minute', label: '盘中最早触发(分钟)', min: 570, max: 720, step: 5, recommend: '600' },
          { key: 'vol_floor_window', label: '地量回溯窗口(日)', min: 5, max: 30, step: 1, recommend: '10' },
          { key: 'vol_floor_tolerance', label: '地量容差(×最低量)', min: 1.0, max: 1.5, step: 0.05, recommend: '1.1' },
          { key: 'vol_shrink_avg10_ratio', label: '10日均量上限(×)', min: 0.4, max: 1.0, step: 0.05, recommend: '0.80' },
          { key: 'ma10_above_max_pct', label: 'MA10上方最大偏离(%)', min: 0.0, max: 3.0, step: 0.5, recommend: '2.0' },
          { key: 'ma10_below_max_pct', label: 'MA10下方最大偏离(%)', min: 1.0, max: 5.0, step: 0.5, recommend: '2.0' },
          { key: 'ma20_above_max_pct', label: 'MA20上方最大偏离(%)', min: 0.0, max: 3.0, step: 0.5, recommend: '2.0' },
          { key: 'ma20_below_max_pct', label: 'MA20下方最大偏离(%)', min: 1.0, max: 5.0, step: 0.5, recommend: '2.0' },
          { key: 'prior_weak_days_required', label: '前置确认(连续N日同满足, 0=关)', min: 0, max: 5, step: 1, recommend: '1' },
        ],
      },
      {
        id: 'SCORE_STRENGTH', name: '真假强势评分 v2：抗跌票是领涨还是补跌', source: 'user', tags: ['前置预警', '评分'],
        description: '抗跌 ≠ 真强势,可能只是"无人接盘但也无人砸盘"(假强势)\n\n9 维度评分(满分约 155):\n通用 A~F:\n  A. 量价配合 (±20): 量缩 50-80% 收阳=+20,地量 <30%=-10\n  B. 均线结构 (±20): 多头排列=+20,close 破 MA5=-10\n  C. 主流题材 (+15): 处于主流题材(引用 SCORE_THEME)\n  D. 板块相对 (±15): 板块跌它涨=+15,板块强它弱=-15\n  E. 资金面 (±15): 大单买入≥2笔=+15,大单净流出>1000万=-15\n  F. 时间持续 (+10): 抗跌 ≥5日=+10\n弱市 G/H/I (v2 新增):\n  G. 逆势创新高 (+25): 大盘创10日新低 同时 个股创10日新高\n  H. 独立强度 (+20/10/5): 5日累计跑赢大盘 ≥5%/≥3%/≥0%\n  I. 板块内排名 (+15/8): 板块涨幅前3=+15,前10=+8\n\n阈值: ≥ 65 = 真强势(可候选), 40~65 = 观望, < 40 = 警惕补跌',
        params: [
          { key: 'min_persist_days', label: '抗跌门槛(跑赢大盘天数)', min: 1, max: 5, step: 1, recommend: '3' },
          { key: 'healthy_vol_min', label: '健康量缩下限(×5日均量)', min: 0.3, max: 0.8, step: 0.05, recommend: '0.5' },
          { key: 'healthy_vol_max', label: '健康量缩上限(×5日均量)', min: 0.6, max: 1.2, step: 0.05, recommend: '0.8' },
          { key: 'extreme_low_vol', label: '地量警戒(×5日均量)', min: 0.1, max: 0.5, step: 0.05, recommend: '0.3' },
          { key: 'big_buy_min_count', label: '大单买入加分门槛(笔)', min: 1, max: 5, step: 1, recommend: '2' },
          { key: 'real_strong_threshold', label: '真强势分数线', min: 50, max: 85, step: 5, recommend: '65' },
          { key: 'observe_threshold', label: '观望分数线', min: 20, max: 60, step: 5, recommend: '40' },
          { key: 'counter_trend_proximity', label: 'G·逆势容差(逼近极值)', min: 0.001, max: 0.02, step: 0.001, recommend: '0.005' },
          { key: 'relative_strong_pct', label: 'H·5日跑赢大盘强阈值(%)', min: 2, max: 10, step: 0.5, recommend: '5.0' },
          { key: 'relative_medium_pct', label: 'H·5日跑赢大盘中阈值(%)', min: 1, max: 6, step: 0.5, recommend: '3.0' },
          { key: 'sector_rank_top_strong', label: 'I·板块前 N 名最强', min: 1, max: 5, step: 1, recommend: '3' },
          { key: 'sector_rank_top_medium', label: 'I·板块前 N 名中等', min: 5, max: 20, step: 1, recommend: '10' },
        ],
      },
      {
        id: 'SECTOR_CAPITAL_INFLOW', name: '资金回流·板块预警：板块强势+龙头真涨停+板块内强势密度', source: 'user', tags: ['前置预警', '板块级'],
        description: '【板块强势】板块今日涨幅 ≥ 1%\n【龙头真涨停】板块龙头(top1) 真正涨停 (v1.7.23 按市场分类: 主板≥9.85% / 科创创业≥19.85% / 北交≥29.85%)\n【板块内强势密度】板块内前10个股平均涨幅 ≥ 4%\n\n三条件 AND 触发, 每30秒扫所有板块, 同一板块当日只推一次\n推送内容: 板块名 + 龙头 + 涨幅 + 你股票池中属于该板块的关注个股清单',
        params: [
          { key: 'min_sector_pct', label: '板块涨幅下限(%)', min: 0.5, max: 5.0, step: 0.5, recommend: '1.0' },
          { key: 'sector_top_n_stocks', label: '板块内取前 N 个股', min: 5, max: 20, step: 1, recommend: '10' },
          { key: 'min_sector_top_avg_pct', label: '板块内前N股平均涨幅下限(%)', min: 2.0, max: 8.0, step: 0.5, recommend: '4.0' },
        ],
      },
    ],
  },
]

const shortTermGroups: GroupDef[] = [
  {
    title: '买点',
    signals: [
      {
        id: 'BUY_STRONG_START', name: '强势起点（右侧·内置）', source: 'user', tags: ['强档'],
        description: '【左侧前置】最近 5 个交易日内有过 EOD 弱势极限(BUY_WEAK_EXTREME) 命中, 取最近一次作为 baseline\n【今日放量】今日盘中预估全天量 ≥ baseline 日量 × 3 (默认 3 倍)\n【成交额门槛】今日预估全天成交额 ≥ 20 亿\n【涨幅门槛】今日涨幅 ≥ 2%\n【站上均线】close > MA10 OR close > MA20\n【盘中早盘门槛】v1.7.89: 默认 10:00 之前不触发\n物理意义: 左侧 S0 缩量地量蓄势完毕, 今日放量拉升 = 右侧买点; 与 S0 天然互斥不会同日同票同时命中',
        params: [
          { key: 'intraday_earliest_minute', label: '盘中最早触发(分钟)', min: 570, max: 720, step: 5, recommend: '600' },
          { key: 'lookback_days', label: '弱势极限回溯天数', min: 1, max: 15, step: 1, recommend: '5' },
          { key: 'vol_multiplier', label: '今日量/baseline 倍数', min: 1.5, max: 6.0, step: 0.5, recommend: '3.0' },
          { key: 'min_full_day_amount', label: '全天预估成交额下限(元)', min: 500000000, max: 5000000000, step: 100000000, recommend: '2000000000' },
          { key: 'min_pct_change', label: '当日涨幅下限(%)', min: 1.0, max: 6.0, step: 0.5, recommend: '2.0' },
        ],
      },
      {
        id: 'BUY_RALLY_MA20', name: '回踩20MA缩量后突破昨高（右侧·内置）', source: 'user', tags: ['强档'],
        description: '与弱势极限互补, 专抓"强势缩量回踩中期线、次日放量突破"——弱势极限抓不到的急跌/高量回踩(如多氟多)。\n【昨日 setup】\n ① 主升浪: 前有 ≥15% 主升浪, 峰值距今(到昨日) ≤ 30 交易日\n ② 回踩20日线: 昨日 close 距 MA20 在 ±3% 内\n ③ 回踩日缩量: 昨日量 < 近10日均量 × 0.8 (卖盘衰竭, 质量关键)\n ④ 流动性: 近10日均成交额 > 20 亿\n【今日 trigger】\n ⑤ 盘中最高 > 昨日最高 ×(1+2.5%) → 买点 (2.5% 过滤假突破)\n回测(自选股近1年, T+5): 触发48 胜率46% 胜负比1.9:1 平均+6.9%。\n配套交易计划: +15% 减仓50% / -7% 全仓止损。',
        params: [
          { key: 'intraday_earliest_minute', label: '盘中最早触发(分钟)', min: 570, max: 720, step: 5, recommend: '600' },
          { key: 'rally_peak_within_bars', label: '主升浪峰值距今上限(交易日)', min: 10, max: 60, step: 5, recommend: '30' },
          { key: 'ma20_touch_pct', label: '回踩容差(±%)', min: 1.0, max: 5.0, step: 0.5, recommend: '3.0' },
          { key: 'shrink_ratio', label: '回踩日缩量上限(×近10日均量)', min: 0.5, max: 1.2, step: 0.05, recommend: '0.80' },
          { key: 'min_full_day_amount', label: '今日全天成交额下限(元)', min: 500000000, max: 10000000000, step: 500000000, recommend: '1000000000' },
          { key: 'breakout_pct', label: '突破昨高门槛(%)', min: 0.0, max: 5.0, step: 0.5, recommend: '2.5' },
        ],
      },
      {
        id: 'BUY_RALLY_MA10', name: '回踩10MA缩量后突破昨高（右侧·内置）', source: 'user', tags: ['强档'],
        description: '回踩20MA缩量后突破昨高 的近亲——回踩锚点改用 MA10、容差收紧到 ±1%(要求贴近MA10), 卖出剩半跟踪 MA10×0.98。\n【昨日 setup】\n ① 主升浪: 前有 ≥15% 主升浪, 峰值距今 ≤ 30 交易日\n ② 回踩10日线: 昨日 close 距 MA10 在 ±1% 内\n ③ 回踩日缩量: 昨日量 < 近10日均量 × 0.8\n ④ 流动性: 今日全天成交额 ≥ 10 亿\n【今日 trigger】\n ⑤ 盘中最高 > 昨日最高 ×(1+2.5%) → 买点\n全市场半年回测: 612笔 胜率57% 盈利因子1.92(样本内1.96/样本外1.88); 配强势股占比≥45%过滤→2.37。\n配套交易计划: +7%卖半 / 剩半收盘破MA10×0.98清 / -6%收盘止损 / 满10交易日时停。',
        params: [
          { key: 'intraday_earliest_minute', label: '盘中最早触发(分钟)', min: 570, max: 720, step: 5, recommend: '600' },
          { key: 'rally_peak_within_bars', label: '主升浪峰值距今上限(交易日)', min: 10, max: 60, step: 5, recommend: '30' },
          { key: 'ma20_touch_pct', label: '回踩容差(±%)', min: 0.5, max: 3.0, step: 0.5, recommend: '1.0' },
          { key: 'shrink_ratio', label: '回踩日缩量上限(×近10日均量)', min: 0.5, max: 1.2, step: 0.05, recommend: '0.80' },
          { key: 'min_full_day_amount', label: '今日全天成交额下限(元)', min: 500000000, max: 10000000000, step: 500000000, recommend: '1000000000' },
          { key: 'breakout_pct', label: '突破昨高门槛(%)', min: 0.0, max: 5.0, step: 0.5, recommend: '2.5' },
        ],
      },
    ],
  },
  {
    title: '卖点 / 减仓',
    signals: [
      {
        id: 'SELL_BREAK_MA5', name: '短线卖 跌破MA5：持仓股盘中跌破 MA5 ≥2%', source: 'user', tags: ['持仓卖出'],
        description: '【适用对象】仅持仓股(entry_cost > 0)\n【触发时间】盘中任何时刻\n【触发条件】close ≤ MA5 × (1 − break_pct/100), 默认 -2%\n物理意义: 5 日线支撑失守, 短线趋势走坏',
        params: [
          { key: 'break_pct', label: '跌破幅度(%)', min: 0.5, max: 5, step: 0.5, recommend: '2.0' },
        ],
      },
      {
        id: 'SELL_BREAK_MA10', name: '短线卖 跌破MA10：持仓股盘中跌破 MA10 ≥2%', source: 'user', tags: ['持仓卖出'],
        description: '【适用对象】仅持仓股\n【触发时间】盘中任何时刻\n【触发条件】close ≤ MA10 × (1 − break_pct/100), 默认 -2%\n物理意义: 10 日线支撑失守',
        params: [
          { key: 'break_pct', label: '跌破幅度(%)', min: 0.5, max: 5, step: 0.5, recommend: '2.0' },
        ],
      },
      {
        id: 'SELL_BREAK_MA20', name: '短线卖 跌破MA20：持仓股盘中跌破 MA20 ≥2%', source: 'user', tags: ['持仓卖出'],
        description: '【适用对象】仅持仓股\n【触发时间】盘中任何时刻\n【触发条件】close ≤ MA20 × (1 − break_pct/100), 默认 -2%\n物理意义: 20 日线是中期趋势线, 跌破 ≥2% 强烈警示离场',
        params: [
          { key: 'break_pct', label: '跌破幅度(%)', min: 0.5, max: 5, step: 0.5, recommend: '2.0' },
        ],
      },
      {
        id: 'SELL_TAKE_PROFIT', name: '止盈减仓 +7%：浮盈达标减仓锁利', source: 'user', tags: ['持仓减仓'],
        description: '持仓票 当前价 ≥ 入仓成本 × (1 + target_pct)\n\n物理意义：浮盈达标后部分锁利，避免"赚到没卖最后倒亏"\n仅对持仓票触发；同时触发 PLOSS 时优先 PLOSS',
        params: [
          { key: 'target_pct', label: '浮盈目标(%)', min: 3, max: 20, step: 1, recommend: '7' },
        ],
      },
      {
        id: 'SELL_TRAIL_STOP', name: '追踪止盈：持仓最高价回撤触发', source: 'user', tags: ['持仓减仓', '主动止盈'],
        description: '物理意义：利润已经跑出来一段，别让它全部回吐\n\n① 持仓最高价(entry_date 起所有 high 的最大值)\n② 浮盈达到 min_gain_pct 才启用追踪(避免开仓后立刻被甩出)\n③ 当 close ≤ 最高价 × (1 - drawdown_pct%) 时触发减仓\n\n推荐组合: min_gain_pct=5, drawdown_pct=7 — 浮盈过 5% 后, 回撤 7% 锁利',
        params: [
          { key: 'min_gain_pct', label: '启用浮盈门槛(%)', min: 0, max: 20, step: 1, recommend: '5' },
          { key: 'drawdown_pct', label: '回撤阈值(%)', min: 3, max: 15, step: 1, recommend: '7' },
        ],
      },
      {
        id: 'SELL_RR_TARGET', name: '盈亏比止盈：达 R 倍止损锁半仓', source: 'user', tags: ['持仓减仓', '主动止盈'],
        description: '物理意义：已实现 N 倍风险单位, 用收益保护已赚到的钱\n\n触发: close ≥ 成本 × (1 + stop_loss_pct × target_r%)\n\n· stop_loss_pct=5, target_r=2 → +10% 锁利 (默认)\n· stop_loss_pct=5, target_r=3 → +15% 锁利\n\n与 SELL_TAKE_PROFIT 的区别: SR1 是固定 +7%, 这个是相对你设定的止损线, 更科学',
        params: [
          { key: 'stop_loss_pct', label: '止损线 R(%)', min: 3, max: 10, step: 0.5, recommend: '5.0' },
          { key: 'target_r', label: '目标 R 倍数', min: 1.5, max: 4, step: 0.5, recommend: '2.0' },
        ],
      },
      {
        id: 'SELL_TIME_STOP', name: '时间止损：N 日不动出局换股', source: 'user', tags: ['持仓减仓', '主动止盈'],
        description: '物理意义：不亏不赚的票挤占资金, 让位给真正能动的票\n\n触发: 持仓 ≥ min_days 个交易日 AND |当前涨跌| < flat_threshold_pct\n\n推荐: 5 日 / ±3% — 一周内还在原地踏步, 换股',
        params: [
          { key: 'min_days', label: '最小持仓日数', min: 3, max: 20, step: 1, recommend: '5' },
          { key: 'flat_threshold_pct', label: '踏步阈值(%)', min: 1, max: 8, step: 0.5, recommend: '3.0' },
        ],
      },
    ],
  },
  {
    title: '持仓风控（浮亏分级预警，渐进强化避免错过止损）',
    signals: [
      {
        id: 'SELL_LOSS_5', name: '浮亏止损 -5%：强档预警(止损线)', source: 'user', tags: ['持仓风控', '强档预警'],
        description: '文档约定的 -5% 止损线\n\n第10课原话:"错了小亏对了大赚,亏 5 个点止损"\n触发后严格交易系统应立即执行止损,继续等待可能"打回原形"\n\n💡 推送模式开关(emit_all):\n  · 默认(关) = 只推当前达到的最深档(避免刷屏)\n  · 开 = 每档都推(渐进强化,可能一次 4 条信号)',
        params: [
          { key: 'threshold_pct', label: '浮亏阈值(%)', min: 3, max: 8, step: 0.5, recommend: '5.0' },
          { key: 'emit_all', label: '每档全推(0/1)', min: 0, max: 1, step: 1, recommend: '0' },
        ],
      },
      {
        id: 'SELL_LOSS_8', name: '浮亏止损 -8%：二次预警', source: 'user', tags: ['持仓风控'],
        description: '已超 -5% 止损线 3 个百分点 — 执行力问题,不止损=违反交易系统',
        params: [
          { key: 'threshold_pct', label: '浮亏阈值(%)', min: 6, max: 12, step: 0.5, recommend: '8.0' },
        ],
      },
      {
        id: 'SELL_LOSS_10', name: '浮亏止损 -10%：严重超止损', source: 'user', tags: ['持仓风控'],
        description: '已严重超 -5% 止损线 — 继续持有 = 持续违反交易系统\n建议考虑反弹卖出点(回到 MA10/MA20 即卖)',
        params: [
          { key: 'threshold_pct', label: '浮亏阈值(%)', min: 8, max: 15, step: 0.5, recommend: '10.0' },
        ],
      },
    ],
  },
]

// 中线信号体系(M1/M2/MS1/MS2) 已下线 v1.7.90
const midTermGroups: GroupDef[] = []

const marketGroups: GroupDef[] = [
  {
    title: '跳水预警',
    signals: [
      {
        id: 'PLUNGE_INDEX', name: '指数急跌：N分钟内沪指跌幅超阈值', source: 'recommended',
        description: '① 监控上证指数在指定时间窗口内的分钟级跌幅\n② 当窗口内累计跌幅超过设定阈值时触发\n③ 适用于盘中快速杀跌的早期预警',
        params: [
          { key: 'time_window_min', label: '时间窗口(分)', min: 3, max: 30, step: 1, recommend: '10' },
          { key: 'drop_threshold_pct', label: '跌幅阈值(%)', min: 0.3, max: 3.0, step: 0.1, recommend: '1.0' },
        ],
      },
      {
        id: 'PLUNGE_BREADTH', name: '涨跌家数恶化：下跌/上涨比超阈值', source: 'recommended',
        description: '① 监控全市场上涨/下跌家数比值\n② 当下跌家数远超上涨家数时触发\n③ 反映市场整体情绪快速转弱',
        params: [
          { key: 'down_up_ratio', label: '下跌/上涨比', min: 1.5, max: 8.0, step: 0.5, recommend: '3.0' },
          { key: 'drop_gt3_pct', label: '跌>3%占比(%)', min: 10, max: 50, step: 5, recommend: '25' },
        ],
      },
      {
        id: 'PLUNGE_SPEED', name: '跌停加速：短时间内跌停家数激增', source: 'recommended',
        description: '① 监控短时间窗口内新增跌停股票数量\n② 跌停数量突然增多表明恐慌情绪蔓延\n③ 常见于主力出逃或利空冲击的中后期',
        params: [
          { key: 'time_window_min', label: '时间窗口(分)', min: 3, max: 15, step: 1, recommend: '5' },
          { key: 'new_limit_down', label: '新增跌停数', min: 3, max: 20, step: 1, recommend: '8' },
        ],
      },
    ],
  },
]

async function loadConfig() {
  loading.value = true
  try {
    config.value = await fetchSignalConfig()
    savedSnapshot.value = JSON.parse(JSON.stringify(toRaw(config.value)))
  } catch {
    message.error('加载策略配置失败')
  } finally {
    loading.value = false
  }
}

async function handleSaveCard(signalId: string, signalName: string) {
  savingCardId.value = signalId
  try {
    await saveSignalConfig(JSON.parse(JSON.stringify(toRaw(config.value))))
    savedSnapshot.value = JSON.parse(JSON.stringify(toRaw(config.value)))
    const shortName = signalName.split('：')[0] || signalName
    message.success(`${shortName} 已保存`)
  } catch {
    message.error('保存失败')
  } finally {
    savingCardId.value = ''
  }
}

function isCardDirty(signalId: string): boolean {
  return JSON.stringify(config.value[signalId] || {}) !== JSON.stringify(savedSnapshot.value[signalId] || {})
}


function getVal(signalId: string, key: string): number {
  return (config.value[signalId]?.[key] as number) ?? 0
}

function setVal(signalId: string, key: string, val: number | null) {
  if (!config.value[signalId]) config.value[signalId] = { enabled: true }
  config.value[signalId][key] = val ?? 0
}

function getEnabled(signalId: string): boolean {
  return config.value[signalId]?.enabled !== false
}

function setEnabled(signalId: string, val: boolean) {
  if (!config.value[signalId]) config.value[signalId] = { enabled: val }
  else config.value[signalId].enabled = val
}

const EXTRA_FILTER_DEFS: ParamDef[] = [
  { key: 'filter_rsi_min', label: 'RSI下限', min: 0, max: 100, step: 1 },
  { key: 'filter_rsi_max', label: 'RSI上限', min: 0, max: 100, step: 1 },
  { key: 'filter_vol_ratio_min', label: '量比下限', min: 0, max: 10, step: 0.1 },
  { key: 'filter_vol_ratio_max', label: '量比上限', min: 0, max: 10, step: 0.1 },
  { key: 'filter_pct_change_min', label: '涨幅下限(%)', min: -10, max: 10, step: 0.1 },
  { key: 'filter_pct_change_max', label: '涨幅上限(%)', min: -10, max: 10, step: 0.1 },
]

function getActiveFilters(signalId: string): ParamDef[] {
  const sc = config.value[signalId]
  if (!sc) return []
  return EXTRA_FILTER_DEFS.filter(f => f.key in sc)
}

function getAvailableFilterOptions(signalId: string) {
  const sc = config.value[signalId] || {}
  return EXTRA_FILTER_DEFS
    .filter(f => !(f.key in sc))
    .map(f => ({ label: f.label, value: f.key }))
}

function addFilter(signalId: string, filterKey: string) {
  if (!config.value[signalId]) config.value[signalId] = { enabled: true }
  const def = EXTRA_FILTER_DEFS.find(f => f.key === filterKey)
  if (def) config.value[signalId][filterKey] = def.min ?? 0
}

function removeFilter(signalId: string, filterKey: string) {
  if (config.value[signalId]) {
    delete config.value[signalId][filterKey]
  }
}

onMounted(() => loadConfig())
</script>

<template>
  <div>
    <NSkeleton v-if="loading" :repeat="6" text />

    <Transition v-else name="content-fade" appear>
    <div>
      <NTabs type="card" animated class="strategy-tabs">
        <NTabPane v-for="(tab, ti) in [
          { name: 'concept', label: '底层概念', anchor: '主升浪定义（被全部信号引用）', anchorLabel: '类型', groups: conceptGroups },
          { name: 'pre', label: '前置预警', anchor: '弱势极限 / 主流题材 / 资金回流', anchorLabel: '类型', groups: preWarningGroups },
          { name: 'short', label: '短线·十五法', anchor: '5日 / 10日均线', anchorLabel: '锚点', groups: shortTermGroups },
          { name: 'market', label: '大盘', anchor: '指数跳水', anchorLabel: '监控对象', groups: marketGroups },
        ]" :key="ti" :name="tab.name" :tab="tab.label">
          <div class="anchor-chip">
            <span class="anchor-label">{{ tab.anchorLabel }}</span>
            <span class="anchor-value">{{ tab.anchor }}</span>
          </div>

          <div v-for="group in tab.groups" :key="group.title" class="signal-group">
            <div class="group-title">{{ group.title }}</div>
            <div class="signal-list">
              <div
                v-for="sig in group.signals"
                :key="sig.id"
                :class="['sig-card', { disabled: !getEnabled(sig.id), 'sig-user': sig.source === 'user' }]"
              >
                <div class="sig-header">
                  <NSwitch
                    :value="getEnabled(sig.id)"
                    @update:value="(v: boolean) => setEnabled(sig.id, v)"
                    size="small"
                  />
                  <span class="sig-name">{{ sig.name }}</span>
                  <CursorTooltip v-if="sig.description" :width="320" dark>
                    <template #trigger>
                      <NIcon class="info-icon" :size="14"><InformationCircleOutline /></NIcon>
                    </template>
                    <div class="desc-tooltip">
                      <div v-for="(line, li) in sig.description.split('\n')" :key="li" class="desc-line">{{ line }}</div>
                    </div>
                  </CursorTooltip>
                  <NTag :type="sig.source === 'user' ? 'info' : 'warning'" size="tiny" :bordered="false">
                    {{ sig.source === 'user' ? '交易系统' : '经验推荐' }}
                  </NTag>
                  <NTag v-for="tag in (sig.tags || [])" :key="tag" type="error" size="tiny" :bordered="false">
                    {{ tag }}
                  </NTag>
                  <div class="sig-actions">
                    <NPopselect
                      v-if="tab.name !== 'market' && getEnabled(sig.id) && getAvailableFilterOptions(sig.id).length"
                      :options="getAvailableFilterOptions(sig.id)"
                      trigger="click"
                      size="small"
                      @update:value="(v: string) => addFilter(sig.id, v)"
                    >
                      <NButton size="tiny" dashed type="primary">
                        <template #icon><NIcon :size="12"><AddOutline /></NIcon></template>
                        增加预警条件
                      </NButton>
                    </NPopselect>
                    <NButton
                      size="tiny"
                      :type="isCardDirty(sig.id) ? 'primary' : 'default'"
                      :disabled="!isCardDirty(sig.id)"
                      @click="handleSaveCard(sig.id, sig.name)"
                      :loading="savingCardId === sig.id"
                    >
                      <template #icon><NIcon :size="12"><SaveOutline /></NIcon></template>
                      保存
                    </NButton>
                  </div>
                </div>

                <div v-if="sig.params.length && getEnabled(sig.id)" class="sig-params">
                  <div v-for="p in sig.params" :key="p.key" class="p-item">
                    <label :for="`sig-${sig.id}-${p.key}`">{{ p.label }}</label>
                    <NInputNumber
                      :value="getVal(sig.id, p.key)"
                      @update:value="(v) => setVal(sig.id, p.key, v)"
                      :step="p.step"
                      size="tiny"
                      class="p-input"
                      :input-props="{ id: `sig-${sig.id}-${p.key}`, name: p.key, 'aria-label': p.label }"
                    />
                    <span v-if="p.recommend" class="p-rec">{{ p.recommend }}</span>
                  </div>
                </div>

                <div v-if="getEnabled(sig.id) && getActiveFilters(sig.id).length" class="sig-filters">
                  <div v-for="f in getActiveFilters(sig.id)" :key="f.key" class="f-item">
                    <NTag size="tiny" :bordered="false" closable @close="removeFilter(sig.id, f.key)" type="success">
                      {{ f.label }}
                    </NTag>
                    <NInputNumber
                      :value="getVal(sig.id, f.key)"
                      @update:value="(v) => setVal(sig.id, f.key, v)"
                      :min="f.min" :max="f.max" :step="f.step"
                      size="tiny"
                      class="f-input"
                      :input-props="{ name: f.key, 'aria-label': f.label }"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </NTabPane>
      </NTabs>

      <!-- v1.7.42: 全局 "保存配置/恢复默认" 已移除, 用卡级保存即可 -->
    </div>
    </Transition>
  </div>
</template>

<style scoped>
/* ── Tabs ── */
.strategy-tabs :deep(.n-tabs-nav) {
  gap: 8px;
  border-bottom: none;
  padding-bottom: 8px;
}
.strategy-tabs :deep(.n-tabs-tab) {
  padding: 6px 22px;
  border-radius: 6px;
  border: 1.5px solid var(--border);
  background: #f5f5f5;
  font-size: 13px;
  font-weight: 600;
  color: var(--text2);
  transition: all 0.2s;
}
.strategy-tabs :deep(.n-tabs-tab:hover) {
  border-color: var(--primary);
  color: var(--primary);
}
.strategy-tabs :deep(.n-tabs-tab.n-tabs-tab--active) {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
  box-shadow: 0 2px 8px rgba(46, 128, 255, 0.25);
}
.strategy-tabs :deep(.n-tabs-tab-pad),
.strategy-tabs :deep(.n-tabs-scroll-padding) {
  display: none;
}

/* ── Anchor ── */
.anchor-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  margin-bottom: 10px;
  background: rgba(46, 128, 255, 0.06);
  border: 1px solid rgba(46, 128, 255, 0.15);
  border-radius: 20px;
  font-size: 12px;
}
.anchor-label {
  font-weight: 700;
  color: var(--primary);
  background: rgba(46, 128, 255, 0.12);
  padding: 1px 6px;
  border-radius: 10px;
  font-size: 11px;
}
.anchor-value {
  font-weight: 600;
  color: var(--text1);
}

/* ── Group ── */
.signal-group {
  margin-bottom: 12px;
}
.group-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text2);
  margin-bottom: 6px;
  padding-left: 2px;
}
.signal-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* ── Signal Card ── */
.sig-card {
  padding: 8px 12px;
  border-radius: 6px;
  border-left: 3px solid rgba(255, 152, 0, 0.5);
  background: rgba(255, 152, 0, 0.04);
  transition: all 0.15s;
}
.sig-card.sig-user {
  border-left-color: rgba(46, 128, 255, 0.5);
  background: rgba(46, 128, 255, 0.03);
}
.sig-card.disabled {
  opacity: 0.55;
}
.sig-card:hover {
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

/* ── Header ── */
.sig-header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
}
.sig-name {
  font-weight: 600;
  font-size: 13px;
  color: var(--text1);
}
.info-icon {
  color: var(--primary);
  cursor: help;
  opacity: 0.6;
  transition: opacity 0.15s;
}
.info-icon:hover {
  opacity: 1;
}

/* ── Params ── */
.sig-params {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 6px;
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.02);
  border-radius: 4px;
}
.p-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.p-item label {
  font-size: 11px;
  color: var(--text3);
  white-space: nowrap;
}
.p-input {
  width: 80px;
}
.p-input :deep(.n-input-number) {
  font-size: 12px;
}
.p-rec {
  font-size: 10px;
  color: #e6a23c;
  white-space: nowrap;
}

/* ── Filters ── */
.sig-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  padding: 4px 8px;
  border-top: 1px dashed rgba(0, 0, 0, 0.06);
}
.f-item {
  display: flex;
  align-items: center;
  gap: 3px;
}
.f-input {
  width: 72px;
}
.sig-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

/* ── Tooltip ── */
.desc-tooltip {
  font-size: 12px;
  line-height: 1.6;
}
.desc-line {
  color: rgba(255, 255, 255, 0.9);
}

/* ── Actions ── */
.action-bar {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 8px;
}
</style>
