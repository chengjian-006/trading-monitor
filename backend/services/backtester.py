"""
回测引擎 — 对历史K线数据模拟信号触发和交易执行
"""

import logging
import pandas as pd
import numpy as np
from typing import Optional

from backend import data_fetcher
from backend.models import repository
from backend.services.signal_engine import (
    compute_indicators,
    get_merged_config,
    _detect_s3_rally_pullback,
    _detect_s0_weak_extreme,
    _detect_strong_start_right,
    _detect_rally_ma20_pullback,
)

logger = logging.getLogger(__name__)

LIMIT_DOWN_THRESHOLD = -0.095


def _is_limit_down(row: pd.Series, prev_close: float) -> bool:
    if prev_close <= 0:
        return False
    pct = (row["close"] - prev_close) / prev_close
    return pct <= LIMIT_DOWN_THRESHOLD and row["close"] == row["low"]


def _simulate_trade(df: pd.DataFrame, buy_idx: int) -> Optional[dict]:
    """
    从 buy_idx 位置买入，模拟后续交易过程。
    df 必须已经包含 ma5, ma10 等指标列。
    """
    buy_row = df.iloc[buy_idx]
    buy_price = buy_row["close"]
    buy_date = buy_row["date"]

    if buy_price <= 0:
        return None

    remaining_pct = 100  # 剩余仓位百分比
    actions = []
    profit_taken = False
    # 止盈目标：买入价 × 1.07（+7%），减仓 50%
    target_price = buy_price * 1.07
    # MA5 卖出阈值：close ≤ MA5 × 0.98（大幅跌破 2%）
    ma5_break_ratio = 0.98

    total_len = len(df)

    # T+0 不可卖出，从 T+1 开始
    t0_close_below_ma10 = buy_row["close"] < buy_row["ma10"]

    pending_sell = None  # {"pct": x, "reason": "..."} 次日待执行的卖出

    i = buy_idx + 1
    while i < total_len and remaining_pct > 0:
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]
        day_offset = i - buy_idx  # T+1, T+2, ...
        profit_taken_today = False  # +7% 减仓当天，跳过后续 MA5 / MA10 卖出

        # 检查是否跌停（无法卖出）
        prev_close = prev_row["close"]
        is_ld = _is_limit_down(row, prev_close)

        # 执行前一日挂起的卖出（破MA5次日卖出）
        if pending_sell and not is_ld:
            sell_price = row["open"]
            sell_pct = min(pending_sell["pct"], remaining_pct)
            actions.append({
                "date": row["date"],
                "type": "close",
                "price": round(sell_price, 3),
                "pct": sell_pct,
                "reason": pending_sell["reason"],
            })
            remaining_pct -= sell_pct
            pending_sell = None
            if remaining_pct <= 0:
                break
            i += 1
            continue
        elif pending_sell and is_ld:
            # 跌停无法执行，继续顺延
            i += 1
            continue

        # 规则1: 止盈减仓 — 盘中最高价 >= 买入价×1.07，减仓50%（当天不再触发其他卖出）
        if not profit_taken and not is_ld and row["high"] >= target_price:
            profit_taken = True
            profit_taken_today = True
            sell_price = target_price
            # 如果开盘已超过目标价，以开盘价卖（更优）
            if row["open"] >= target_price:
                sell_price = row["open"]
            sell_pct = min(50, remaining_pct)
            actions.append({
                "date": row["date"],
                "type": "reduce",
                "price": round(sell_price, 3),
                "pct": sell_pct,
                "reason": "盈利7%减仓",
            })
            remaining_pct -= sell_pct
            if remaining_pct <= 0:
                break

        # 规则2: 止损 — T+0和T+1收盘均低于MA10，T+1卖出（安全网）
        if day_offset == 1 and t0_close_below_ma10 and not profit_taken_today:
            t1_close_below_ma10 = row["close"] < row["ma10"]
            if t1_close_below_ma10 and remaining_pct > 0:
                if not is_ld:
                    # 跳空高开则以开盘价卖出，否则以收盘价（14:45）卖出
                    sell_price = max(row["open"], row["close"])
                    actions.append({
                        "date": row["date"],
                        "type": "stop_loss",
                        "price": round(sell_price, 3),
                        "pct": remaining_pct,
                        "reason": "2日未站上MA10止损",
                    })
                    remaining_pct = 0
                    break
                else:
                    # 跌停无法卖出，挂起到次日
                    pending_sell = {"pct": remaining_pct, "reason": "2日未站上MA10止损(顺延)"}
                    i += 1
                    continue

        # 规则3: 大幅跌破MA5清仓 — 收盘 ≤ MA5 × 0.98（短线趋势结束）
        # +7% 减仓当天跳过；T+0 不可卖；模拟"14:45 前观察"用日收盘价代替
        if (remaining_pct > 0 and not profit_taken_today
                and not pd.isna(row["ma5"])):
            if row["close"] <= row["ma5"] * ma5_break_ratio:
                if not is_ld:
                    actions.append({
                        "date": row["date"],
                        "type": "close",
                        "price": round(row["close"], 3),
                        "pct": remaining_pct,
                        "reason": "大幅跌破MA5清仓",
                    })
                    remaining_pct = 0
                    break
                else:
                    pending_sell = {"pct": remaining_pct, "reason": "大幅跌破MA5清仓(跌停顺延)"}

        i += 1

    # 如果仍有持仓（回测窗口结束仍未卖出），按最后一天收盘价标记
    if remaining_pct > 0:
        last_row = df.iloc[min(total_len - 1, buy_idx + 40)]
        actions.append({
            "date": last_row["date"],
            "type": "holding",
            "price": round(last_row["close"], 3),
            "pct": remaining_pct,
            "reason": "回测期结束仍持仓",
        })

    # 计算总收益
    total_return = 0.0
    for a in actions:
        weight = a["pct"] / 100
        ret = (a["price"] - buy_price) / buy_price
        total_return += weight * ret

    hold_days = 0
    if actions:
        last_action = actions[-1]
        buy_date_idx = list(df["date"]).index(buy_date) if buy_date in list(df["date"]) else buy_idx
        last_date_idx_list = [j for j, d in enumerate(df["date"]) if d == last_action["date"]]
        if last_date_idx_list:
            hold_days = last_date_idx_list[0] - buy_date_idx

    return {
        "buy_date": buy_date,
        "buy_price": round(buy_price, 3),
        "actions": actions,
        "total_return_pct": round(total_return * 100, 2),
        "hold_days": hold_days,
    }


def _detect_buy_signal(signal_id: str, window: pd.DataFrame, row: pd.Series,
                       cfg: dict, signal_cfg: dict) -> Optional[str]:
    """按 signal_id 派发到对应买点检测器, 复刻实盘 signal_engine 的判定逻辑.

    - BUY_WEAK_EXTREME(弱势极限/左侧): 原始检测 + 实盘的"前N日同为弱势极限"前置确认
    - BUY_STRONG_START(启动初期/右侧): 需把 BUY_WEAK_EXTREME 配置块一并传入
    - 其它(默认 S3_BUY): 已下线的老版主升浪回踩(S3)逻辑, 仅供历史分析
    """
    if signal_id == "BUY_WEAK_EXTREME":
        res = _detect_s0_weak_extreme(window, row, signal_cfg)
        if res is None:
            return None
        prior_days = int(signal_cfg.get("prior_weak_days_required", 1))
        if prior_days > 0:
            n = len(window)
            for offset in range(1, prior_days + 1):
                prev_idx = n - 1 - offset
                if prev_idx < 12:
                    return None
                if _detect_s0_weak_extreme(window.iloc[:prev_idx + 1], window.iloc[prev_idx], signal_cfg) is None:
                    return None
            return f"前{prior_days}日同为弱势极限 | {res}"
        return res

    if signal_id == "BUY_STRONG_START":
        return _detect_strong_start_right(window, row, signal_cfg, cfg.get("BUY_WEAK_EXTREME", {}))

    if signal_id == "BUY_RALLY_MA20":
        return _detect_rally_ma20_pullback(window, row, signal_cfg)

    return _detect_s3_rally_pullback(window, row, signal_cfg)


async def run_backtest(
    codes: list[str],
    signal_id: str = "S3_BUY",
    lookback_days: int = 40,
    user_config: dict | None = None,
) -> dict:
    cfg = get_merged_config(user_config)
    signal_cfg = cfg.get(signal_id, {})

    all_trades = []
    fetch_days = lookback_days + 80  # 多取数据用于计算MA60

    for code in codes:
        try:
            df_raw = await data_fetcher.get_daily_kline(code, days=fetch_days, prefer_cache=True)
            if df_raw.empty or len(df_raw) < 60:
                continue

            df = compute_indicators(df_raw, cfg)
            # 右侧 BUY_STRONG_START 检测器需 amount_est(盘中估算全天成交额); 回测无盘中,
            # 用 EOD 近似 量×收盘 注入, 否则该信号在回测里恒不触发 (对齐 run_holding_curve).
            if "amount_est" not in df.columns:
                df["amount_est"] = df["volume"] * df["close"]

            stocks = await repository.list_all_stocks(include_deleted=True)
            name_map = {s["code"]: s["name"] for s in stocks}
            stock_name = name_map.get(code, code)

            # 确定回测起始位置（倒推 lookback_days 个交易日）
            start_idx = max(60, len(df) - lookback_days)

            last_exit_date = None  # 上一笔交易的出场日; 出场前不再开新仓
            for idx in range(start_idx, len(df)):
                row = df.iloc[idx]
                if last_exit_date is not None and row["date"] <= last_exit_date:
                    continue

                # 截取到当前位置的子集用于信号检测
                window = df.iloc[:idx + 1]
                detail = _detect_buy_signal(signal_id, window, row, cfg, signal_cfg)

                if detail is not None:
                    trade = _simulate_trade(df, idx)
                    if trade:
                        trade["code"] = code
                        trade["name"] = stock_name
                        trade["signal_detail"] = detail
                        all_trades.append(trade)

                        # 记录出场日, 持仓期间(信号日 <= 出场日)不再重复开仓
                        if trade["actions"]:
                            last_exit_date = trade["actions"][-1]["date"]

        except Exception as e:
            logger.error(f"[backtest] {code} 回测失败: {e}")
            continue

    # 汇总统计
    total = len(all_trades)
    if total == 0:
        return {"trades": [], "summary": _empty_summary()}

    returns = [t["total_return_pct"] for t in all_trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    # 区分已完结和仍持仓的交易
    closed_trades = [t for t in all_trades if not any(a["type"] == "holding" for a in t["actions"])]
    holding_trades = [t for t in all_trades if any(a["type"] == "holding" for a in t["actions"])]

    closed_returns = [t["total_return_pct"] for t in closed_trades]
    closed_wins = [r for r in closed_returns if r > 0]
    closed_losses = [r for r in closed_returns if r <= 0]

    summary = {
        "total_trades": total,
        "closed_trades": len(closed_trades),
        "holding_trades": len(holding_trades),
        "win_count": len(closed_wins),
        "loss_count": len(closed_losses),
        "win_rate": round(len(closed_wins) / len(closed_trades) * 100, 1) if closed_trades else 0,
        "avg_return_pct": round(sum(closed_returns) / len(closed_returns), 2) if closed_returns else 0,
        "max_profit_pct": round(max(closed_returns), 2) if closed_returns else 0,
        "max_loss_pct": round(min(closed_returns), 2) if closed_returns else 0,
        "total_return_pct": round(sum(closed_returns), 2),
        "avg_hold_days": round(sum(t["hold_days"] for t in closed_trades) / len(closed_trades), 1) if closed_trades else 0,
    }

    return {"trades": all_trades, "summary": summary}


async def run_holding_curve(
    codes: list[str],
    signal_id: str = "BUY_WEAK_EXTREME",
    lookback_days: int = 120,
    max_days: int = 10,
    user_config: dict | None = None,
) -> dict:
    """逐日持仓曲线 — 买点触发后第 T+1..T+N 天的"当日最高点 / 当日收盘 / 累计峰值"收益.

    entry = 触发日收盘价 (与 outcome 闭环口径一致, 可比). 不套用卖出规则, 纯前向收益,
    用于量化买点本身的质量与最优持有节奏 (左侧慢热 vs 右侧快冲).
    每个触发独立计入 (不去重连续触发).
    """
    cfg = get_merged_config(user_config)
    signal_cfg = cfg.get(signal_id, {})
    max_days = max(1, min(int(max_days), 30))

    day_high: dict[int, list[float]] = {n: [] for n in range(1, max_days + 1)}
    day_close: dict[int, list[float]] = {n: [] for n in range(1, max_days + 1)}
    day_peak: dict[int, list[float]] = {n: [] for n in range(1, max_days + 1)}
    triggers: list[dict] = []
    fetch_days = lookback_days + 80 + max_days

    for code in codes:
        try:
            df_raw = await data_fetcher.get_daily_kline(code, days=fetch_days, prefer_cache=True)
            if df_raw.empty or len(df_raw) < 60:
                continue
            df = compute_indicators(df_raw, cfg)
            # 右侧 BUY_STRONG_START 检测器需 amount_est(盘中估算全天成交额); 回测无盘中,
            # 用 EOD 近似 量×收盘 注入, 否则该信号在回测里恒不触发. pct_change 已由 compute_indicators 生成.
            if "amount_est" not in df.columns:
                df["amount_est"] = df["volume"] * df["close"]
            start_idx = max(60, len(df) - lookback_days)
            for idx in range(start_idx, len(df)):
                row = df.iloc[idx]
                window = df.iloc[:idx + 1]
                detail = _detect_buy_signal(signal_id, window, row, cfg, signal_cfg)
                if detail is None:
                    continue
                entry = float(row["close"])
                if entry <= 0:
                    continue
                triggers.append({
                    "code": code, "date": str(row["date"]),
                    "entry": round(entry, 3), "signal_detail": detail,
                })
                peak = None
                for n in range(1, max_days + 1):
                    j = idx + n
                    if j >= len(df):
                        break
                    hi = float(df.iloc[j]["high"])
                    cl = float(df.iloc[j]["close"])
                    peak = hi if peak is None else max(peak, hi)
                    day_high[n].append((hi - entry) / entry * 100)
                    day_close[n].append((cl - entry) / entry * 100)
                    day_peak[n].append((peak - entry) / entry * 100)
        except Exception as e:
            logger.error(f"[holding_curve] {code} 失败: {e}")
            continue

    curve = []
    for n in range(1, max_days + 1):
        c = day_close[n]
        if not c:
            continue
        m = len(c)
        curve.append({
            "day": n,
            "samples": m,
            "avg_high_pct": round(sum(day_high[n]) / m, 2),     # 当日最高点收益(均)
            "avg_close_pct": round(sum(c) / m, 2),               # 当日收盘收益(均)
            "avg_peak_pct": round(sum(day_peak[n]) / m, 2),      # 持有至当日的累计峰值收益(均)
            "median_close_pct": round(sorted(c)[m // 2], 2),
            "up_rate": round(sum(1 for x in c if x > 0) / m * 100, 1),
            "win_rate": round(sum(1 for x in c if x >= 5) / m * 100, 1),
            "loss_rate": round(sum(1 for x in c if x <= -3) / m * 100, 1),
        })

    triggers.sort(key=lambda t: t["date"], reverse=True)
    return {
        "signal_id": signal_id,
        "lookback_days": lookback_days,
        "max_days": max_days,
        "trigger_count": len(triggers),
        "curve": curve,
        "triggers": triggers[:60],
    }


def _empty_summary() -> dict:
    return {
        "total_trades": 0,
        "closed_trades": 0,
        "holding_trades": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0,
        "avg_return_pct": 0,
        "max_profit_pct": 0,
        "max_loss_pct": 0,
        "total_return_pct": 0,
        "avg_hold_days": 0,
    }
