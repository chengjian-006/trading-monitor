"""交易回合 FIFO 切分纯函数 — cfzy_biz_trades 行 → 回合(头+腿)列表.

单只票按时间升序的成交流, 切成"开仓(持仓0→正)→清仓(持仓回0)"一个回合.
卖出按 FIFO 匹配最早买入批次, realized_pnl 只算已匹配卖出部分(open 回合的部分卖出也正确).
MFE/MAE/holding_days/max_drawdown 由 attach_excursions() 从日线回填(v1.7.685 补齐)。
"""


def build_rounds_from_trades(trades: list[dict]) -> list[dict]:
    """trades: 单只票按 (trade_date, trade_time) 升序的成交 dict 列表.

    每个 dict 需含: id, trade_date, trade_time, code, name, direction('buy'/'sell'),
    quantity, price, amount, fee_total. 返回回合 dict 列表(见模块文档).
    """
    rounds: list[dict] = []
    cur: dict | None = None          # 当前 open 回合
    buy_lots: list[list] = []        # FIFO 队列, 每项 [price, remaining_qty]
    position = 0                     # 当前持仓股数

    for t in trades:
        qty = int(t["quantity"])
        price = float(t["price"])
        fee = float(t.get("fee_total", 0) or 0)
        amount = float(t["amount"])
        direction = t["direction"]

        if cur is None:
            if direction != "buy":
                continue  # 无持仓时的卖出(脏数据)忽略
            cur = _new_round(t)

        leg = {
            "leg_type": direction, "trade_date": t["trade_date"],
            "trade_time": t.get("trade_time") or "", "price": price,
            "qty": qty, "amount": round(amount, 2), "fee": round(fee, 2),
            "trade_id": t.get("id"), "running_qty": 0,
        }
        cur["total_fee"] += fee

        if direction == "buy":
            buy_lots.append([price, qty])
            position += qty
            cur["total_buy_amount"] += amount
            cur["_buy_qty"] += qty
            cur["_buy_cost"] += amount
            cur["_buy_legs"] += 1
        else:  # sell
            sell_qty = qty
            matched_cost = 0.0
            while sell_qty > 0 and buy_lots:
                lot = buy_lots[0]
                m = min(sell_qty, lot[1])
                matched_cost += lot[0] * m
                lot[1] -= m
                sell_qty -= m
                if lot[1] <= 0:
                    buy_lots.pop(0)
            matched_qty = qty - sell_qty          # 实际有买入批次对应的卖出股数
            # 超卖(卖出>持仓, 多见于交割单从持仓中途开始): 只记可匹配部分, 不虚增收益/卖额
            matched_amount = round(amount * matched_qty / qty, 2) if qty else 0.0
            position -= matched_qty
            leg["qty"] = matched_qty
            leg["amount"] = matched_amount
            cur["total_sell_amount"] += matched_amount
            cur["_sell_qty"] += matched_qty
            cur["_sell_amount"] += matched_amount
            cur["_sell_legs"] += 1
            cur["realized_pnl"] += matched_amount - matched_cost  # 费在收尾统一扣

        leg["running_qty"] = position
        cur["peak_qty"] = max(cur["peak_qty"], position)
        cur["legs"].append(leg)

        if position == 0:
            _close_round(cur, t)
            rounds.append(cur)
            cur, buy_lots = None, []

    if cur is not None:
        _finalize_open(cur)
        rounds.append(cur)
    return rounds


def _new_round(t: dict) -> dict:
    return {
        "code": t["code"], "name": t.get("name") or "", "source": "real",
        "status": "open", "open_date": t["trade_date"],
        "open_time": t.get("trade_time") or "", "close_date": None,
        "close_time": None, "entry_price": 0.0, "exit_price": None,
        "peak_qty": 0, "is_scaled_in": False, "is_scaled_out": False,
        "total_buy_amount": 0.0, "total_sell_amount": 0.0, "total_fee": 0.0,
        "realized_pnl": 0.0, "realized_pnl_pct": None, "legs": [],
        "_buy_qty": 0, "_buy_cost": 0.0, "_buy_legs": 0,
        "_sell_qty": 0, "_sell_amount": 0.0, "_sell_legs": 0,
    }


def _common_finalize(r: dict):
    r["entry_price"] = round(r["_buy_cost"] / r["_buy_qty"], 3) if r["_buy_qty"] else 0.0
    r["exit_price"] = round(r["_sell_amount"] / r["_sell_qty"], 3) if r["_sell_qty"] else None
    r["is_scaled_in"] = r["_buy_legs"] > 1
    r["is_scaled_out"] = r["_sell_legs"] > 1
    r["realized_pnl"] = round(r["realized_pnl"] - r["total_fee"], 2)
    r["total_buy_amount"] = round(r["total_buy_amount"], 2)
    r["total_sell_amount"] = round(r["total_sell_amount"], 2)
    r["total_fee"] = round(r["total_fee"], 2)
    base = r["_buy_cost"]
    r["realized_pnl_pct"] = round(r["realized_pnl"] / base * 100, 4) if base else None
    for k in ("_buy_qty", "_buy_cost", "_buy_legs", "_sell_qty", "_sell_amount", "_sell_legs"):
        r.pop(k, None)


def _close_round(r: dict, last_trade: dict):
    r["status"] = "closed"
    r["close_date"] = last_trade["trade_date"]
    r["close_time"] = last_trade.get("trade_time") or ""
    _common_finalize(r)


def _finalize_open(r: dict):
    r["status"] = "open"
    _common_finalize(r)


from datetime import date, timedelta


def _as_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def attach_entry_signal(round_obj: dict, signals: list[dict], window_days: int = 7):
    """把回合 open_date 就近匹配到该票买点信号(±window_days, 同距离优先买入日及之前).

    signals: 同 code 的买点信号 dict 列表, 每项 {id, signal_id, signal_name, price, date}.
    命中则就地写 entry_signal_pk/entry_signal_id/entry_model_name/entry_deviation_pct.
    """
    buy_d = _as_date(round_obj.get("open_date"))
    if buy_d is None or not signals:
        return
    lo, hi = buy_d - timedelta(days=window_days), buy_d + timedelta(days=window_days)
    best = None  # (排序键, 信号)
    for s in signals:
        sd = _as_date(s.get("date"))
        if sd is None or not (lo <= sd <= hi):
            continue
        key = (abs((sd - buy_d).days), 0 if sd <= buy_d else 1)
        if best is None or key < best[0]:
            best = (key, s)
    if best is None:
        return
    s = best[1]
    round_obj["entry_signal_pk"] = s.get("id")
    round_obj["entry_signal_id"] = s.get("signal_id")
    round_obj["entry_model_name"] = s.get("signal_name")
    sp = s.get("price")
    ep = round_obj.get("entry_price")
    if sp and ep:
        round_obj["entry_deviation_pct"] = round((float(ep) - float(sp)) / float(sp) * 100, 4)


def attach_excursions(round_obj: dict, bars: list[dict]):
    """从日线回填 holding_days / mfe / mae / max_drawdown(就地写入 round_obj).

    bars: 该票日线 [{trade_date, high, low}], 需按日期升序, 可含窗口外的行(本函数自行裁剪)。

    ── 窗口约定(重要, 决定了这几个数字怎么解读) ──
      统计窗 = [入场日的**次日**, 出场日]; 持仓中的回合出场日取 bars 末日。
      · 入场日不计: A股 T+1 当日不可卖出, 那天的浮盈浮亏根本不可兑现; 且日线分不出你
        买入前/后的极值(早上跌 5% 但你下午才买, 算进 MAE 就是假的)。Tradervue 同样
        排除入场那根 bar。
      · 出场日计入: 当天你本可以卖在更好的价位 —— "卖点好不好"正是要衡量这个。
      窗口内无 K 线(极端情况, 如当日建仓当日清仓的脏数据)则退回用入场日当天, 不留空。

    ── 口径 ──
      mfe_pct = (窗口内最高价 - entry_price) / entry_price * 100   → 最大浮盈, 通常 >0
      mae_pct = (窗口内最低价 - entry_price) / entry_price * 100   → 最大浮亏, 通常 <0
      max_drawdown_pct = 窗口内自"当时最高点"回落的最大幅度(负数) → 浮盈回吐了多少
      holding_days = 入场日~出场日之间的交易日数(含两端)
    entry_price 是本回合**加权平均买入价**(见 _common_finalize), 分批建仓下这是正确基准。
    """
    entry = float(round_obj.get("entry_price") or 0)
    open_d = _as_date(round_obj.get("open_date"))
    if entry <= 0 or open_d is None or not bars:
        return
    close_d = _as_date(round_obj.get("close_date"))

    rows = []
    for b in bars:
        d = _as_date(b.get("trade_date"))
        if d is None or d < open_d:
            continue
        if close_d is not None and d > close_d:
            continue
        try:
            hi, lo = float(b["high"]), float(b["low"])
        except (KeyError, TypeError, ValueError):
            continue
        if hi <= 0 or lo <= 0:
            continue
        rows.append((d, hi, lo))
    if not rows:
        return
    round_obj["holding_days"] = len(rows)

    win = [r for r in rows if r[0] > open_d] or rows   # 入场日次日起; 无则退回入场日
    hi_d, hi_v = max(win, key=lambda r: r[1])[0], max(r[1] for r in win)
    lo_d, lo_v = min(win, key=lambda r: r[2])[0], min(r[2] for r in win)
    round_obj["mfe_pct"] = round((hi_v - entry) / entry * 100, 4)
    round_obj["mfe_date"] = hi_d
    round_obj["mae_pct"] = round((lo_v - entry) / entry * 100, 4)
    round_obj["mae_date"] = lo_d

    peak, mdd = 0.0, 0.0
    for _d, hi, lo in win:
        peak = max(peak, hi)
        if peak > 0:
            mdd = min(mdd, (lo - peak) / peak * 100)
    round_obj["max_drawdown_pct"] = round(mdd, 4)


from collections import defaultdict

from backend.models.repo.trade_rounds import (
    get_trades_for_rounds, get_buy_signals_for_user, replace_rounds_for_code,
    get_daily_bars_for_codes,
)


def group_trades_by_code(trades: list[dict]) -> dict[str, list[dict]]:
    """把成交按 code 分组, 保持各组内原有(已按时间升序)顺序."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        grouped[t["code"]].append(t)
    return dict(grouped)


import asyncio as _asyncio

# 同一用户的重建串行化: 导入触发的后台重建与15:20定时任务/重复导入可能并发,
# delete+insert 交错会写出重复回合
_rebuild_locks: dict[int, "_asyncio.Lock"] = {}


async def rebuild_user_rounds(user_id: int) -> int:
    """全量重建某用户的真实交易回合. 返回写入回合数."""
    lock = _rebuild_locks.setdefault(user_id, _asyncio.Lock())
    async with lock:
        return await _rebuild_user_rounds_inner(user_id)


async def _rebuild_user_rounds_inner(user_id: int) -> int:
    trades = await get_trades_for_rounds(user_id)
    grouped = group_trades_by_code(trades)
    signals_by_code = await get_buy_signals_for_user(user_id)  # 一次查询替代逐 code N+1
    # 日线也一次性批量取(同理防 N+1: 174 只逐票查跨云 DB 会累成 8 秒+)
    bars_by_code = {}
    if grouped:
        lo = min(str(t["trade_date"])[:10] for t in trades)
        bars_by_code = await get_daily_bars_for_codes(list(grouped), lo)
    total = 0
    for code, code_trades in grouped.items():
        rounds = build_rounds_from_trades(code_trades)
        if not rounds:
            continue
        signals = signals_by_code.get(code, [])
        bars = bars_by_code.get(code, [])
        for r in rounds:
            attach_entry_signal(r, signals)
            attach_excursions(r, bars)
        await replace_rounds_for_code(user_id, code, "real", rounds)
        total += len(rounds)
    return total
