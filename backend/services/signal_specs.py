"""信号元数据集中表 (v1.7.x).

把"信号有什么属性"这一层从 scanner.py 抽出来:
  SIGNAL_GROUP_MAP   — 每个信号属于哪一类 (entry/exit/risk/regime/sector/quality)
  PRIORITY_OVERRIDES — 推送优先级 (强/中/弱) 覆盖 Signal.strength
  ALERT_TIMING_DEFAULT_POST_CLOSE — 默认走盘后汇总而非盘中实时推送的信号集合

scanner.py 写库 / 推送决策时从这里读, 不再在 scanner 本地维护映射。

新加信号时 必须 在此三处登记, 否则:
  - 未登记 SIGNAL_GROUP_MAP → DB signal_group 字段为空, 影响后续按组聚合
  - 未登记 PRIORITY_OVERRIDES → 走 Signal.strength 默认 (一般 1)
  - 默认 alert_timing=intraday
"""

# 信号分组. 与 cfzy_biz_signals.signal_group 列对应.
#   entry   - 买点信号 (左侧 / 右侧)
#   exit    - 主动卖/锁利 (均线破位 / 浮盈达标 / 追踪止盈 / 盈亏比 / 时间止损)
#   risk    - 持仓风控 (浮亏分级)
#   regime  - 大盘急跌告警 (盘面级)
#   sector  - 板块级 (资金回流等)
#   quality - 后台评分类 (强势评分, 主流题材) — 通常走盘后汇总
SIGNAL_GROUP_MAP: dict[str, str] = {
    # entry — 买点
    "BUY_WEAK_EXTREME": "entry",
    "BUY_STRONG_START":    "entry",
    "BUY_RALLY_MA20":      "entry",
    "BUY_RALLY_MA10":      "entry",
    "BUY_VOL_BREAKOUT":    "entry",
    "BUY_PLATFORM_BREAKOUT": "entry",
    "BUY_AUCTION_STRENGTH": "entry",
    # exit — 主动卖 / 锁利
    "SELL_BREAK_MA5":          "exit",
    "SELL_BREAK_MA10":          "exit",
    "SELL_BREAK_MA20":          "exit",
    "SELL_TAKE_PROFIT":        "exit",
    "SELL_TRAIL_STOP": "exit",
    "SELL_RR_TARGET":    "exit",
    "SELL_TIME_STOP":         "exit",
    "SELL_WEAK_STOP":         "exit",   # 弱势极限左侧出场 -12% 硬止损
    "SELL_WEAK_TIME":         "exit",   # 弱势极限左侧出场 持有满T+15 清仓
    "SELL_RALLY_MA20":        "exit",   # 回踩20MA缩量后突破昨高 卖出(止损/清剩半/时间止损)
    "SELL_RALLY_MA10":        "exit",   # 回踩10MA缩量后突破昨高 卖出(止损/清剩半/时间止损)
    "SELL_RALLY_MA20_HALF":   "exit",   # 回踩20MA缩量后突破昨高 +7%止盈减半
    "SELL_RALLY_MA10_HALF":   "exit",   # 回踩10MA缩量后突破昨高 +7%止盈减半
    # risk — 持仓风控浮亏分级
    "SELL_LOSS_5":  "risk",
    "SELL_LOSS_8":  "risk",
    "SELL_LOSS_10": "risk",
    # regime — 大盘急跌告警
    "PLUNGE_INDEX": "regime",
    "PLUNGE_BREADTH":    "regime",
    "PLUNGE_SPEED":      "regime",
    "PLUNGE_MARKET_EBB": "regime",   # 大盘退潮(涨停家数骤降≥40%) 减仓提示
    # sector — 板块级
    "SECTOR_CAPITAL_INFLOW": "sector",
    # quality — 后台评分
    "SCORE_STRENGTH": "quality",
    "SCORE_THEME": "quality",
}


def group_of(signal_id: str) -> str:
    """返回信号所属 group; 未登记返回空字符串 (DB NOT NULL DEFAULT '')。"""
    return SIGNAL_GROUP_MAP.get(signal_id, "")


# 卖出信号归类(报告"卖出"段三分组用): profit 主动止盈 / loss 被动止损 / discipline 纪律清仓
_SELL_PROFIT_IDS = {"SELL_TAKE_PROFIT", "SELL_RR_TARGET", "SELL_TRAIL_STOP",
                    "SELL_RALLY_MA20_HALF", "SELL_RALLY_MA10_HALF"}
_SELL_DISCIPLINE_IDS = {"SELL_WEAK_TIME", "SELL_TIME_STOP"}   # 时间到/持满清仓(SELL_TIME_STOP名含"止损"但是纪律)
_SELL_LOSS_IDS = {"SELL_LOSS_5", "SELL_LOSS_8", "SELL_LOSS_10", "SELL_WEAK_STOP",
                  "SELL_BREAK_MA5", "SELL_BREAK_MA10", "SELL_BREAK_MA20"}

SELL_CATEGORY_LABEL = {"profit": "主动止盈", "loss": "被动止损", "discipline": "纪律清仓"}
SELL_CATEGORY_EMOJI = {"profit": "🟢", "loss": "🔴", "discipline": "⏳"}


def sell_category(signal_id: str | None, signal_name: str = "") -> str:
    """卖出信号归类 → 'profit'(主动止盈) / 'loss'(被动止损) / 'discipline'(纪律清仓)。

    已登记 id 直判; 回踩MA派生卖点(SELL_RALLY_MA20/MA10, 名称随触发原因变)与未登记的按名称关键词。
    """
    sid = (signal_id or "").upper()
    if sid in _SELL_PROFIT_IDS:
        return "profit"
    if sid in _SELL_DISCIPLINE_IDS:
        return "discipline"
    if sid in _SELL_LOSS_IDS:
        return "loss"
    nm = signal_name or ""
    if any(k in nm for k in ("止盈", "锁利", "减半")):
        return "profit"
    if any(k in nm for k in ("持有满", "持满", "时间")):
        return "discipline"
    if any(k in nm for k in ("止损", "跌破", "破位", "警戒")):
        return "loss"
    return "loss"   # 兜底偏保守: 卖出默认当风控处理


def sell_group_category(signals: list[tuple]) -> str:
    """一只票多个卖出信号时的归类: 被动止损 > 纪律清仓 > 主动止盈 (先暴露风险)。
    signals: [(signal_id, signal_name), ...]。"""
    cats = {sell_category(sid, nm) for sid, nm in signals}
    if "loss" in cats:
        return "loss"
    if "discipline" in cats:
        return "discipline"
    return "profit"


# 推送优先级覆盖 (1=仅 DB / 2=DB+前端 / 3=DB+前端+企微).
# 不在表里的信号走 Signal.strength.
PRIORITY_OVERRIDES: dict[str, int] = {
    # 强档 (必须立即决策, 触发推企微)
    "BUY_STRONG_START": 3,
    "SELL_BREAK_MA5": 3, "SELL_BREAK_MA10": 3, "SELL_BREAK_MA20": 3,
    "SELL_LOSS_5": 3, "SELL_LOSS_8": 3, "SELL_LOSS_10": 3,
    "SELL_TAKE_PROFIT": 3,
    "PLUNGE_INDEX": 3, "PLUNGE_BREADTH": 3, "PLUNGE_SPEED": 3,
    "PLUNGE_MARKET_EBB": 3,
    # v1.7.x 主动止盈/止损 (默认关, 开启时按强档推送 — 用户主动开启就是想立即看到)
    "SELL_TRAIL_STOP": 3, "SELL_RR_TARGET": 3, "SELL_TIME_STOP": 3,
    # 弱势极限左侧出场 (清仓级, 强档推企微)
    "SELL_WEAK_STOP": 3, "SELL_WEAK_TIME": 3,
    # 回踩MA买点派生卖点 (rally_reminder 落库, 与其它 exit 卖点一致按强档; 出场参数走 rally_reminder.RALLY_MODELS, 不在 DEFAULT_SIGNAL_CONFIG)
    "SELL_RALLY_MA20": 3, "SELL_RALLY_MA10": 3,
    "SELL_RALLY_MA20_HALF": 3, "SELL_RALLY_MA10_HALF": 3,
    # 左侧弱势极限 (v1.7.147 升强档: 盘中命中即推 + 11:30/15:00 快照仍跑, 用户接受双推)
    "BUY_WEAK_EXTREME": 3,
    # 回踩20MA缩量后突破昨高 (盘中突破即买, 须及时, 故强档推企微; 中等质量, 嫌噪音可在配置页降档)
    "BUY_RALLY_MA20": 3,
    "BUY_RALLY_MA10": 3,
    # 缩量突破昨高 (v1.7.248): 盘中突破即买, 须及时, 强档推企微
    "BUY_VOL_BREAKOUT": 3,
    # 中继平台突破 (v1.7.323): 尾盘收盘确认突破即买, 强档推企微; 退潮/分化月由 regime 闸门自动降级
    "BUY_PLATFORM_BREAKOUT": 3,
    # 竞价高开弱转强 (v1.7.275): 9:26竞价即决策, 须及时, 强档推企微
    "BUY_AUCTION_STRENGTH": 3,
}


def priority_of(signal_id: str, fallback_strength: int = 2) -> int:
    return PRIORITY_OVERRIDES.get(signal_id, fallback_strength or 2)


# 默认走盘后 15:05 汇总而非盘中实时推送的信号集合.
# 用户配置 alert_timing=intraday 可逐个覆盖.
ALERT_TIMING_DEFAULT_POST_CLOSE: set[str] = {
    "SCORE_STRENGTH",
    "SCORE_THEME",
}


def default_alert_timing(signal_id: str) -> str:
    """返回 'intraday' 或 'post_close'."""
    return "post_close" if signal_id in ALERT_TIMING_DEFAULT_POST_CLOSE else "intraday"
