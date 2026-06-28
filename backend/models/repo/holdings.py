"""持仓 FIFO 计算 - cfzy_biz_trades 表派生.

按 FIFO 算每只仍持仓票的加权平均成本 + 最早未平仓买入日.
给 SELL_TAKE_PROFIT (+7%减仓) / SELL_TIME_STOP (持仓≥N日) / SELL_TRAIL_STOP (最高价回撤) 使用.
"""
from collections import defaultdict
from datetime import date, timedelta

from backend.models.repo._db import _fetchall


async def get_holdings_cost(user_id: int) -> dict[str, float]:
    """返回 {code: avg_cost}, 仅包含 remaining_qty > 0 的票."""
    info = await _get_holdings_fifo_info(user_id)
    return {code: v["avg_cost"] for code, v in info.items()}


async def get_holdings_entry_date(user_id: int) -> dict[str, str]:
    """返回 {code: 'YYYY-MM-DD'}, 最早未平仓买入日."""
    info = await _get_holdings_fifo_info(user_id)
    return {code: v["earliest_buy_date"] for code, v in info.items()}


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
    """一次 FIFO 计算返回 (cost_map, entry_date_map, entry_model_map) 三件套。

    scanner 每 30s 一轮要全部三个, 原来分调三个函数 = 同一轮内 3 次全量拉交割单 + 3 次 FIFO 重算;
    这里算一次复用。结果与分别调用三个函数完全一致。
    """
    info = await _get_holdings_fifo_info(user_id)
    cost_map = {code: v["avg_cost"] for code, v in info.items()}
    date_map = {code: v["earliest_buy_date"] for code, v in info.items()}
    model_map = await _match_entry_models(user_id, info, match_window_days)
    return cost_map, date_map, model_map


async def get_holdings_entry_model(user_id: int, match_window_days: int = 7) -> dict[str, str]:
    """返回 {code: entry_signal_id} — 把每只持仓的最早未平仓买入日, 匹配到 cfzy_biz_signals 里
    买入日前后 ±match_window_days 天内、离买入日最近的那条买点信号(同距离优先买入日及之前)。

    供出场差异化: 命中 BUY_WEAK_EXTREME → 左侧出场; 其余买点 / 未匹配到 → 不在返回 dict, 走右侧快出。
    交割单从券商导入不带"看了哪个信号", 且信号历史可能晚于买入日几天才触发(地量在持有初期才出现),
    故按 code + 买入日前后就近匹配, 是近似归因。
    """
    info = await _get_holdings_fifo_info(user_id)
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


async def _get_holdings_fifo_info(user_id: int) -> dict[str, dict]:
    """内部: FIFO 计算每只仍持仓票的成本 + 最早未平仓买入日.

    返回 {code: {"avg_cost": float, "earliest_buy_date": str}}.
    """
    rows = await _fetchall(
        "SELECT code, direction, quantity, price, trade_date, trade_time "
        "FROM cfzy_biz_trades WHERE user_id = %s "
        "ORDER BY code, trade_date, trade_time",
        (user_id,),
    )
    if not rows:
        return {}

    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_code[r["code"]].append(r)

    result: dict[str, dict] = {}
    for code, trades in by_code.items():
        # 每项: [price, remaining_qty, trade_date]
        buy_queue: list[list] = []
        for t in trades:
            qty = int(t["quantity"])
            price = float(t["price"])
            if t["direction"] == "buy":
                buy_queue.append([price, qty, t["trade_date"]])
            else:
                sell_qty = qty
                while sell_qty > 0 and buy_queue:
                    lot = buy_queue[0]
                    match = min(sell_qty, lot[1])
                    lot[1] -= match
                    sell_qty -= match
                    if lot[1] <= 0:
                        buy_queue.pop(0)
        total_qty = sum(lot[1] for lot in buy_queue)
        if total_qty > 0:
            total_amount = sum(lot[0] * lot[1] for lot in buy_queue)
            earliest_date = buy_queue[0][2]
            if hasattr(earliest_date, "strftime"):
                earliest_date_str = earliest_date.strftime("%Y-%m-%d")
            else:
                earliest_date_str = str(earliest_date)[:10]
            result[code] = {
                "avg_cost": round(total_amount / total_qty, 3),
                "earliest_buy_date": earliest_date_str,
            }
    return result
