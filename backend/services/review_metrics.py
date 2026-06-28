# -*- coding: utf-8 -*-
"""区间复盘清单 — 纯逻辑(无 DB 依赖, 便于单测).

职责: 类别→SQL片段映射、交易计划提取、由 kline/perf/outcome 算收益、按类型汇总。
DB 编排在 repo/signals.py 的 get_review_signal_list。
"""
import re

# 类别 → WHERE 片段。已核实生产库 direction ∈ {buy,sell,reduce,plunge};
# 板块 SECTOR_ 的 direction 也是 buy, 故买点必须叠前缀区分。LIKE 中 _ 转义为 \_。
# 注: %% 是 aiomysql/pymysql 格式化时对字面 % 的转义(避免被误当参数占位符)。
_CAT_SQL = {
    "buy": "(direction='buy' AND signal_id LIKE 'BUY\\_%%')",
    "sell": "direction='sell'",
    "reduce": "direction='reduce'",
    "sector": "signal_id LIKE 'SECTOR\\_%%'",
    "plunge": "direction='plunge'",
}

_PLAN_RE = re.compile(r"交易计划[:：]\s*([^|]+)")
_TP_RE = re.compile(r"\+(\d+(?:\.\d+)?)%(卖半|减半|卖|减)")
_SL_RE = re.compile(r"-(\d+(?:\.\d+)?)%止损")
_TS_RE = re.compile(r"T\+(\d+)时停")
_REST_RE = re.compile(r"剩半([^/|]+)")


def build_category_where(categories):
    """拼出类别 OR 片段; 空/非法回退买点(安全默认)。返回带外括号的字符串。"""
    frags = [_CAT_SQL[c] for c in (categories or []) if c in _CAT_SQL]
    if not frags:
        frags = [_CAT_SQL["buy"]]
    return "(" + " OR ".join(frags) + ")"


def extract_trade_plan(detail):
    if not detail:
        return ""
    m = _PLAN_RE.search(detail)
    return m.group(1).strip() if m else ""


def parse_exit_plan(plan, trigger_price):
    """从交易计划串解析计划性出场(卖点)。缺项 None/''; 不算实现收益。"""
    out = {"tp_action": None, "tp_pct": None, "tp_price": None,
           "sl_pct": None, "sl_price": None, "time_stop_days": None, "other_exit": ""}
    if not plan:
        return out
    m = _TP_RE.search(plan)
    if m:
        out["tp_pct"] = float(m.group(1))
        out["tp_action"] = m.group(2)
        if trigger_price:
            out["tp_price"] = round(trigger_price * (1 + out["tp_pct"] / 100), 2)
    m = _SL_RE.search(plan)
    if m:
        out["sl_pct"] = float(m.group(1))
        if trigger_price:
            out["sl_price"] = round(trigger_price * (1 - out["sl_pct"] / 100), 2)
    m = _TS_RE.search(plan)
    if m:
        out["time_stop_days"] = int(m.group(1))
    rest = []
    m = _REST_RE.search(plan)
    if m:
        rest.append("剩半" + m.group(1).strip())
    if out["time_stop_days"]:
        rest.append(f"T+{out['time_stop_days']}时停")
    out["other_exit"] = " / ".join(rest)
    return out


def _empty_returns():
    return {"cur_price": None, "cur_ret_pct": None, "max_gain_pct": None,
            "max_dd_pct": None, "t1_pct": None, "t3_pct": None, "t5_pct": None}


def compute_kline_returns(trigger_price, trigger_date, klines):
    """主算源: 由 kline_cache OHLC 算各口径收益。
    klines: [{trade_date,high,low,close}] 升序; 内部再按 >= trigger_date 过滤(含触发日)。
    T+N = 触发日起第 N 根(0-based, 触发日=index0)的 close 相对触发价; 不足则 None。
    """
    if not trigger_price:
        return _empty_returns()
    rows = [k for k in klines if str(k["trade_date"]) >= str(trigger_date)]
    if not rows:
        return _empty_returns()
    cur = rows[-1]["close"]
    hi = max(k["high"] for k in rows)
    lo = min(k["low"] for k in rows)

    def tn(n):
        return (rows[n]["close"] - trigger_price) / trigger_price * 100 if len(rows) > n else None

    return {
        "cur_price": cur,
        "cur_ret_pct": (cur - trigger_price) / trigger_price * 100,
        "max_gain_pct": (hi - trigger_price) / trigger_price * 100,
        "max_dd_pct": (lo - trigger_price) / trigger_price * 100,
        "t1_pct": tn(1), "t3_pct": tn(3), "t5_pct": tn(5),
    }


def returns_from_perf(perf_rows):
    """兜底1: 由冻结 perf 表(close/high/low_pct 相对触发价)算收益。无价格基准, cur_price=None。"""
    if not perf_rows:
        return None
    mg = max(float(r["high_pct"]) for r in perf_rows)
    md = min(float(r["low_pct"]) for r in perf_rows)
    byd = {int(r["day_offset"]): float(r["close_pct"]) for r in perf_rows}
    return {"cur_price": None, "cur_ret_pct": byd[max(byd)], "max_gain_pct": mg, "max_dd_pct": md,
            "t1_pct": byd.get(1), "t3_pct": byd.get(3), "t5_pct": byd.get(5)}


def returns_from_outcome(sig):
    """兜底2: 信号行自带 outcome_p1/p3/p5_pct。"""
    p1, p3, p5 = sig.get("outcome_p1_pct"), sig.get("outcome_p3_pct"), sig.get("outcome_p5_pct")
    if p1 is None and p3 is None and p5 is None:
        return None
    cur = p5 if p5 is not None else (p3 if p3 is not None else p1)
    return {"cur_price": None, "cur_ret_pct": cur, "max_gain_pct": None, "max_dd_pct": None,
            "t1_pct": p1, "t3_pct": p3, "t5_pct": p5}


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarize_rows(rows):
    """按 signal_id 汇总(仅计 cur_ret_pct 非空的个股行; 板块/大盘不计)。末尾追加 __ALL__ 合计。"""
    groups = {}
    order = []
    for r in rows:
        if r.get("cur_ret_pct") is None:
            continue
        sid = r["signal_id"]
        if sid not in groups:
            groups[sid] = []
            order.append(sid)
        groups[sid].append(r)

    def agg(label, name, items):
        rets = [x["cur_ret_pct"] for x in items]
        mg = [x["max_gain_pct"] for x in items if x.get("max_gain_pct") is not None]
        md = [x["max_dd_pct"] for x in items if x.get("max_dd_pct") is not None]
        t5 = [x["t5_pct"] for x in items if x.get("t5_pct") is not None]
        evaluated = [x for x in items if x.get("outcome") in ("success", "fail", "neutral")]
        succ = [x for x in evaluated if x["outcome"] == "success"]
        return {
            "signal_id": label, "signal_name": name, "count": len(items),
            "win_rate": round(sum(1 for v in rets if v > 0) / len(rets) * 100, 1) if rets else None,
            "avg_cur_ret": round(sum(rets) / len(rets), 2) if rets else None,
            "median_cur_ret": round(_median(rets), 2) if rets else None,
            "avg_max_gain": round(sum(mg) / len(mg), 2) if mg else None,
            "avg_max_dd": round(sum(md) / len(md), 2) if md else None,
            "avg_t5": round(sum(t5) / len(t5), 2) if t5 else None,
            "success_rate": round(len(succ) / len(evaluated) * 100, 1) if evaluated else None,
        }

    out = [agg(sid, groups[sid][0]["signal_name"], groups[sid]) for sid in order]
    allitems = [x for sid in order for x in groups[sid]]
    if allitems:
        out.append(agg("__ALL__", "全部个股买卖点", allitems))
    return out
