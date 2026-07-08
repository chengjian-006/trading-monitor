"""实盘交割单 vs 模型买卖点 对比分析 (只读, 不写库).

对用户交割单里每只票, 拉历史日K, 在每个交易日重跑买/卖检测器, 与实际买卖时点对齐,
判定「符合 / 偏离」模型, 并对照「听模型 vs 凭感觉」两组的盈亏。

复用 (保证与回测页/实盘完全同款判定):
  backtester._detect_buy_signal : 三个买点检测器派发 (含弱势极限"前N日确认"前置)
  backtester._simulate_trade    : 模型卖出规则 (止盈7%减半/2日未站MA10止损/跌破MA5清仓/40日超时)
  backtester.compute_indicators : 指标计算
  trade_analyzer.analyze_trades : FIFO 配对买卖, 复用同一份盈亏口径

K线走 get_daily_kline (新浪→东财→同花顺→缓存, prefer_cache), 退池历史票也能拉。
"""
import asyncio
import logging
from collections import defaultdict

import pandas as pd

from backend import data_fetcher
from backend.models import repository
from backend.services import backtester
from backend.services.backtester import compute_indicators, get_merged_config
from backend.services.trade_analyzer import analyze_trades

logger = logging.getLogger(__name__)

BUY_SIGNALS = ["BUY_WEAK_EXTREME", "BUY_STRONG_START", "BUY_RALLY_MA20"]
BUY_SIGNAL_NAMES = {
    "BUY_WEAK_EXTREME": "弱势极限(左侧)",
    "BUY_STRONG_START": "强势起点(右侧)",
    "BUY_RALLY_MA20": "回踩20MA缩量后突破昨高(右侧)",
    "BUY_RALLY_MA10": "回踩10MA缩量后突破昨高(右侧)",
    "BUY_RALLY_MA60": "回踩60MA缩量后突破昨高(右侧)",
}

KLINE_DAYS = 400    # 拉取历史K线根数 (覆盖约1年交易 + MA60/主升浪窗口预热)
WARMUP_BARS = 60    # 检测器最少需要的前置K线 (MA60)
CONCURRENCY = 8     # 并发拉K线上限


def _date_str(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _rows_to_trades(rows: list[dict]) -> list[dict]:
    """DB 行 → analyze_trades 所需格式 (trade_date 转 date 对象)。"""
    from datetime import datetime
    out = []
    for r in rows:
        td = r["trade_date"]
        if isinstance(td, str):
            td = datetime.strptime(td, "%Y-%m-%d").date()
        out.append({
            "trade_date": td,
            "trade_time": r.get("trade_time") or "",
            "code": r["code"],
            "name": r.get("name") or "",
            "direction": r["direction"],
            "quantity": int(r["quantity"]),
            "price": float(r["price"]),
            "amount": float(r["amount"]),
            "fee": float(r.get("fee") or 0),
            "stamp_tax": float(r.get("stamp_tax") or 0),
            "transfer_fee": float(r.get("transfer_fee") or 0),
            "net_amount": float(r.get("net_amount") or 0),
        })
    return out


def _run_buy_detectors(ind: pd.DataFrame, cfg: dict) -> dict[int, list[tuple[str, str]]]:
    """逐日重跑3个买点检测器 (走 backtester._detect_buy_signal, 与实盘/回测同款),
    返回 {bar_idx: [(signal_id, detail), ...]}。"""
    out: dict[int, list[tuple[str, str]]] = {}
    for i in range(WARMUP_BARS, len(ind)):
        window = ind.iloc[: i + 1]
        row = ind.iloc[i]
        hits: list[tuple[str, str]] = []
        for sig in BUY_SIGNALS:
            detail = backtester._detect_buy_signal(sig, window, row, cfg, cfg.get(sig, {}))
            if detail:
                hits.append((sig, detail))
        if hits:
            out[i] = hits
    return out


async def _load_indicators(code: str, cfg: dict, sem: asyncio.Semaphore):
    """拉K线 + 算指标 + 注入 amount_est (强势起点检测器依赖)。失败/数据不足返回 None。"""
    async with sem:
        try:
            df = await data_fetcher.get_daily_kline(code, days=KLINE_DAYS, prefer_cache=True)
        except Exception as e:
            logger.warning(f"[trade_compare] K线拉取失败 {code}: {e}")
            return code, None
    if df is None or df.empty or len(df) < WARMUP_BARS + 5:
        return code, None
    ind = compute_indicators(df, cfg).reset_index(drop=True)
    if "amount_est" not in ind.columns:
        ind["amount_est"] = ind["volume"] * ind["close"]
    return code, ind


def _grp_stats(rets: list[float]) -> dict:
    if not rets:
        return {"count": 0, "win_rate": 0.0, "avg_return": 0.0}
    wins = [r for r in rets if r > 0]
    return {
        "count": len(rets),
        "win_rate": round(len(wins) / len(rets) * 100, 1),
        "avg_return": round(sum(rets) / len(rets), 2),
    }


def _count(details: list[dict], val: str) -> int:
    return sum(1 for d in details if d.get("verdict") == val)


async def compare_trades_to_model(user_id: int, signal_window: int = 5) -> dict:
    """主入口。signal_window = 信号有效期(交易日): 买入往前看几天算"信号附近"。"""
    rows = await repository.get_all_trade_records(user_id)
    if not rows:
        return {"ok": False, "msg": "无交割单数据, 请先在交易分析页导入"}

    trades = _rows_to_trades(rows)
    paired = analyze_trades(trades)["trades"]

    user_cfg = await repository.get_signal_config(user_id)
    cfg = get_merged_config(user_cfg)

    code_name = {}
    buys_by_code: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        code_name.setdefault(t["code"], t.get("name") or t["code"])
        if t["direction"] == "buy":
            buys_by_code[t["code"]].append(t)

    codes = sorted({t["code"] for t in trades})

    sem = asyncio.Semaphore(CONCURRENCY)
    loaded = dict(await asyncio.gather(*[_load_indicators(c, cfg, sem) for c in codes]))

    model: dict[str, dict] = {}
    no_kline: list[str] = []
    for code in codes:
        ind = loaded.get(code)
        if ind is None:
            no_kline.append(code)
            continue
        model[code] = {
            "ind": ind,
            "date_idx": {str(d): i for i, d in enumerate(ind["date"])},
            "buy_hits": _run_buy_detectors(ind, cfg),
        }

    # ── 买点对比 ──
    buy_details: list[dict] = []
    buy_verdict: dict[tuple, str] = {}
    for code, blist in buys_by_code.items():
        m = model.get(code)
        for t in blist:
            bdate = _date_str(t["trade_date"])
            rec = {
                "code": code, "name": code_name.get(code, code),
                "buy_date": bdate, "buy_price": round(t["price"], 2),
                "matched_signal": "", "matched_signal_name": "",
                "signal_gap": None, "detail": "",
            }
            if not m or bdate not in m["date_idx"]:
                rec["verdict"] = "无法评估"
            else:
                bidx = m["date_idx"][bdate]
                matched, gap = None, None
                for j in range(bidx, max(bidx - signal_window, -1), -1):
                    if j in m["buy_hits"]:
                        matched = m["buy_hits"][j][0]
                        gap = bidx - j
                        break
                if matched:
                    rec["verdict"] = "符合模型"
                    rec["matched_signal"] = matched[0]
                    rec["matched_signal_name"] = BUY_SIGNAL_NAMES.get(matched[0], matched[0])
                    rec["signal_gap"] = gap
                    rec["detail"] = matched[1]
                else:
                    rec["verdict"] = "偏离模型"
            buy_details.append(rec)
            buy_verdict[(code, bdate)] = rec["verdict"]

    # ── 错过的信号 (模型给了买点, 但N天内没买该票) ──
    missed: list[dict] = []
    for code, m in model.items():
        actual_idxs = {
            m["date_idx"][_date_str(t["trade_date"])]
            for t in buys_by_code.get(code, [])
            if _date_str(t["trade_date"]) in m["date_idx"]
        }
        ind = m["ind"]
        for sidx, hits in sorted(m["buy_hits"].items()):
            if any(sidx <= ab <= sidx + signal_window for ab in actual_idxs):
                continue
            sig_id, detail = hits[0]
            fwd = None
            if sidx + 5 < len(ind):
                p0 = float(ind.iloc[sidx]["close"])
                p5 = float(ind.iloc[sidx + 5]["close"])
                if p0 > 0:
                    fwd = round((p5 - p0) / p0 * 100, 2)
            missed.append({
                "code": code, "name": code_name.get(code, code),
                "signal_date": str(ind.iloc[sidx]["date"]),
                "signal_id": sig_id,
                "signal_name": BUY_SIGNAL_NAMES.get(sig_id, sig_id),
                "detail": detail, "forward_ret_5d": fwd,
            })
    missed.sort(key=lambda x: x["signal_date"], reverse=True)

    # ── 卖点对比 (用模型从你的实际买入日模拟卖出, 比时点) ──
    sell_details: list[dict] = []
    for p in paired:
        code = p["code"]
        m = model.get(code)
        rec = {
            "code": code, "name": p["name"],
            "buy_date": p["buy_date"], "sell_date": p["sell_date"],
            "actual_return": p["return_pct"], "hold_days": p["hold_days"],
            "model_exit_date": "", "model_reason": "", "model_return": None,
            "day_diff": None,
        }
        if not m or p["buy_date"] not in m["date_idx"] or p["sell_date"] not in m["date_idx"]:
            rec["verdict"] = "无法评估"
            sell_details.append(rec)
            continue
        bidx = m["date_idx"][p["buy_date"]]
        sidx = m["date_idx"][p["sell_date"]]
        sim = backtester._simulate_trade(m["ind"], bidx)
        if not sim or not sim["actions"]:
            rec["verdict"] = "无法评估"
            sell_details.append(rec)
            continue
        last = sim["actions"][-1]
        exit_date = str(last["date"])
        rec["model_exit_date"] = exit_date
        rec["model_reason"] = last["reason"]
        rec["model_return"] = sim["total_return_pct"]
        exit_idx = m["date_idx"].get(exit_date, bidx + (sim.get("hold_days") or 0))
        diff = sidx - exit_idx
        rec["day_diff"] = diff
        if abs(diff) <= signal_window:
            rec["verdict"] = "符合模型"
        elif diff > 0:
            rec["verdict"] = "卖太晚"
        else:
            rec["verdict"] = "卖太早"
        sell_details.append(rec)

    # ── 盈亏对照 (符合模型 vs 偏离模型, 基于买点判定的FIFO配对) ──
    aligned_rets, deviated_rets = [], []
    for p in paired:
        v = buy_verdict.get((p["code"], p["buy_date"]))
        if v == "符合模型":
            aligned_rets.append(p["return_pct"])
        elif v == "偏离模型":
            deviated_rets.append(p["return_pct"])

    return {
        "ok": True,
        "buy_compare": {
            "total": len(buy_details),
            "aligned": _count(buy_details, "符合模型"),
            "deviated": _count(buy_details, "偏离模型"),
            "not_evaluable": _count(buy_details, "无法评估"),
            "details": buy_details,
        },
        "sell_compare": {
            "total": len(sell_details),
            "aligned": _count(sell_details, "符合模型"),
            "too_late": _count(sell_details, "卖太晚"),
            "too_early": _count(sell_details, "卖太早"),
            "not_evaluable": _count(sell_details, "无法评估"),
            "details": sell_details,
        },
        "missed_signals": missed,
        "pnl_contrast": {
            "aligned": _grp_stats(aligned_rets),
            "deviated": _grp_stats(deviated_rets),
        },
        "meta": {
            "signal_window": signal_window,
            "stocks_total": len(codes),
            "stocks_evaluated": len(model),
            "stocks_no_kline": no_kline,
            "paired_trades": len(paired),
        },
    }
