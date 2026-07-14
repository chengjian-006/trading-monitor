"""持仓成本计算 - cfzy_biz_trades 表派生.

按"摊薄成本"(券商参考成本价口径)算每只仍持仓票的成本 + 当前持仓段建仓日.
给 SELL_TAKE_PROFIT (+7%减仓) / SELL_TIME_STOP (持仓≥N日) / SELL_TRAIL_STOP (最高价回撤) /
盈利保护 / 浮盈显示 使用.

v1.7.535 起由 FIFO(剩余物理批次成本) 改为 摊薄成本: 卖出时把卖出金额从剩余成本里扣减
(已落袋盈亏摊到剩余股), 持仓清零即重置。与券商"参考成本价"一致(差额=手续费, 本表 price 不含费)。
区别: 高抛低吸/减仓落袋利润会压低剩余股成本, 成本可低于任一买入价、甚至为负(已实现盈利超剩余投入);
FIFO 旧法剩下的是更晚买入的高价仓, 与用户/券商认的"利润垫"背离, 致盈利保护误报(天华新能 300390)。
"""
from collections import defaultdict
from datetime import date, timedelta

from backend.models.repo._db import _fetchall


def compute_diluted_holdings(trades: list[dict]) -> dict[str, dict]:
    """纯函数: 按"摊薄成本"算每只在持票的成本 + 当前持仓段建仓日.

    Args:
        trades: 成交行列表(每行 code/direction/quantity/price/trade_date), 须已按
                (code, trade_date, trade_time) 升序。
    Returns:
        {code: {"avg_cost": float, "earliest_buy_date": "YYYY-MM-DD"}}, 仅含当前净持>0 的票。
        avg_cost 可为负(已实现盈利超过剩余投入); earliest_buy_date = 当前持仓段第一笔买入日。

    算法(摊薄/券商参考成本价): 买入累加成本; 卖出把卖出金额从剩余总成本里扣减; 净持清零即重置,
    下一笔买入另起一段。
    """
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in trades:
        by_code[r["code"]].append(r)

    result: dict[str, dict] = {}
    for code, ts in by_code.items():
        qty = 0
        total_cost = 0.0
        leg_start = None     # 当前持仓段第一笔买入日
        for t in ts:
            tq = int(t["quantity"])
            price = float(t["price"])
            if t["direction"] == "buy":
                if qty == 0:            # 从空仓起一段新建仓
                    total_cost = 0.0
                    leg_start = t["trade_date"]
                total_cost += price * tq
                qty += tq
            else:                       # 卖出: 卖出金额抵减剩余成本(摊薄)
                total_cost -= price * tq
                qty -= tq
                if qty <= 0:            # 清仓归零, 重置(已落袋盈亏不带入下一段)
                    qty = 0
                    total_cost = 0.0
                    leg_start = None
        if qty > 0:
            if hasattr(leg_start, "strftime"):
                leg_str = leg_start.strftime("%Y-%m-%d")
            else:
                leg_str = str(leg_start)[:10]
            result[code] = {
                "avg_cost": round(total_cost / qty, 3),   # 可为负(超额落袋)
                "earliest_buy_date": leg_str,
                "qty": qty,                               # 当前净持股数(止损升级算累计多亏用)
            }
    return result


async def get_holdings_cost(user_id: int) -> dict[str, float]:
    """返回 {code: avg_cost(摊薄)}, 仅包含 remaining_qty > 0 的票."""
    info = await _get_holdings_cost_info(user_id)
    return {code: v["avg_cost"] for code, v in info.items()}


async def get_holdings_entry_date(user_id: int) -> dict[str, str]:
    """返回 {code: 'YYYY-MM-DD'}, 当前持仓段建仓日."""
    info = await _get_holdings_cost_info(user_id)
    return {code: v["earliest_buy_date"] for code, v in info.items()}


async def get_holdings_qty(user_id: int) -> dict[str, int]:
    """返回 {code: 当前净持股数}, 仅 remaining_qty > 0 的票(止损升级算累计多亏用)."""
    info = await _get_holdings_cost_info(user_id)
    return {code: v["qty"] for code, v in info.items()}


def _as_date(v) -> date | None:
    """把 DATE() 返回值(date / datetime / str)统一成 date, 失败返 None。"""
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


async def get_holdings_full_info(user_id: int, match_window_days: int = 7,
                                 ) -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
    """一次摊薄成本计算返回 (cost_map, entry_date_map, entry_model_map) 三件套。

    scanner 每 30s 一轮要全部三个, 原来分调三个函数 = 同一轮内 3 次全量拉交割单 + 3 次重算;
    这里算一次复用。结果与分别调用三个函数完全一致。
    """
    info = await _get_holdings_cost_info(user_id)
    cost_map = {code: v["avg_cost"] for code, v in info.items()}
    date_map = {code: v["earliest_buy_date"] for code, v in info.items()}
    model_map = await _match_entry_models(user_id, info, match_window_days)
    return cost_map, date_map, model_map


async def get_holdings_took_half(user_id: int, date_map: dict[str, str]) -> set[str]:
    """本轮建仓以来已经推过「+7%止盈卖半」的持仓代码集合 (v1.7.614)。

    喂 signal_engine.detect_signals(took_half=...) 用: 卖半后每股成本不变, 没有这道闸
    SELL_TAKE_PROFIT 会天天重复触发, 把赢家一路碾成碎仓 —— 而回测口径是「只卖半一次,
    剩半交给破MA5/止损」。以「该股本轮建仓日(date_map)之后是否发过 SELL_TAKE_PROFIT」为准:
    清仓再买入 → entry_date 前移 → 自动重新开闸, 无需额外状态表。
    """
    if not date_map:
        return set()
    earliest = min(date_map.values())
    rows = await _fetchall(
        "SELECT code, triggered_at FROM cfzy_biz_signals "
        "WHERE user_id=%s AND signal_id='SELL_TAKE_PROFIT' AND triggered_at >= %s",
        (user_id, f"{str(earliest)[:10]} 00:00:00"))
    out: set[str] = set()
    for r in rows:
        code = str(r["code"])
        entry = date_map.get(code)
        if entry and str(r["triggered_at"])[:10] >= str(entry)[:10]:
            out.add(code)
    return out


async def get_holdings_entry_model(user_id: int, match_window_days: int = 7) -> dict[str, str]:
    """返回 {code: entry_signal_id} — 把每只持仓的最早未平仓买入日, 匹配到 cfzy_biz_signals 里
    买入日前后 ±match_window_days 天内、离买入日最近的那条买点信号(同距离优先买入日及之前)。

    供出场差异化: 命中 BUY_WEAK_EXTREME → 左侧出场; 其余买点 / 未匹配到 → 不在返回 dict, 走右侧快出。
    交割单从券商导入不带"看了哪个信号", 且信号历史可能晚于买入日几天才触发(地量在持有初期才出现),
    故按 code + 买入日前后就近匹配, 是近似归因。
    """
    info = await _get_holdings_cost_info(user_id)
    return await _match_entry_models(user_id, info, match_window_days)


async def _match_entry_models(user_id: int, info: dict[str, dict],
                              match_window_days: int = 7) -> dict[str, str]:
    if not info:
        return {}
    codes = list(info.keys())
    placeholders = ",".join(["%s"] * len(codes))
    # 按 signal_id 前缀匹配买点, 不依赖 signal_group: 历史信号该列普遍为空, 用 group 会漏掉全部。
    rows = await _fetchall(
        f"SELECT code, signal_id, DATE(triggered_at) AS d FROM cfzy_biz_signals "
        f"WHERE user_id = %s AND signal_id LIKE 'BUY\\_%%' AND code IN ({placeholders}) "
        f"ORDER BY code, triggered_at ASC",
        (user_id, *codes),
    )
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_code[r["code"]].append(r)

    result: dict[str, str] = {}
    for code, meta in info.items():
        buy_d = _as_date(meta["earliest_buy_date"])
        if buy_d is None:
            continue
        lo = buy_d - timedelta(days=match_window_days)
        hi = buy_d + timedelta(days=match_window_days)
        best = None  # (排序键, signal_id); 键=(距买入日天数, 0=买入日及之前/1=之后)
        for r in by_code.get(code, []):
            sd = _as_date(r["d"])
            if sd is None or not (lo <= sd <= hi):
                continue
            key = (abs((sd - buy_d).days), 0 if sd <= buy_d else 1)
            if best is None or key < best[0]:
                best = (key, r["signal_id"])
        if best:
            result[code] = best[1]
    return result


async def _get_holdings_cost_info(user_id: int) -> dict[str, dict]:
    """内部: 摊薄成本计算每只仍持仓票的成本 + 当前持仓段建仓日.

    返回 {code: {"avg_cost": float, "earliest_buy_date": str}}. 取数后委托纯函数
    compute_diluted_holdings(便于单测)。
    """
    rows = await _fetchall(
        "SELECT code, direction, quantity, price, trade_date, trade_time "
        "FROM cfzy_biz_trades WHERE user_id = %s "
        "ORDER BY code, trade_date, trade_time",
        (user_id,),
    )
    if not rows:
        return {}
    return compute_diluted_holdings(rows)
