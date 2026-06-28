# 区间复盘清单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/review` 复盘页新增「区间复盘清单」卡片, 用户自选时间区间, 查看该区间触发的个股买卖点/减仓信号逐条明细(当前收益/区间最大浮盈浮亏/T+1·3·5/评估结论) + 按类型汇总 + 导出 xlsx。

**Architecture:** 后端纯逻辑放 `services/review_metrics.py`(可单测不连库), DB 编排放 `repo/signals.py` 的 `get_review_signal_list`(kline 为主算 + perf/outcome 冻结兜底), 端点挂 `routers/signals.py`。前端新建子组件 `IntervalReviewCard.vue` 挂入 `ReviewView.vue`, 经 `api/signals.ts` 取数, 用 sheetjs 导出。

**Tech Stack:** FastAPI + aiomysql(pymysql 本地诊断) · pytest(asyncio_mode=auto) · Vue3 + TS + Naive UI(NDataTable/NDatePicker/NCheckboxGroup) · sheetjs(xlsx) · Vite/npm

**Spec:** `docs/superpowers/specs/2026-06-07-interval-review-list-design.md`

**本地运行约定(项目既有):** 真解释器 `C:\Users\成剑\AppData\Local\Programs\Python\Python313\python.exe`; 连库脚本需 `dangerouslyDisableSandbox` + `$env:PYTHONIOENCODING="utf-8"` + `$env:PYTHONPATH=项目根`。pytest 纯逻辑测试无需连库。

---

## Task 1: 后端纯逻辑模块 `services/review_metrics.py` + 单元测试

**Files:**
- Create: `backend/services/review_metrics.py`
- Test: `backend/tests/test_review_metrics.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_review_metrics.py`:

```python
# -*- coding: utf-8 -*-
from backend.services.review_metrics import (
    build_category_where, extract_trade_plan, parse_exit_plan, compute_kline_returns,
    returns_from_perf, returns_from_outcome, summarize_rows,
)


def test_build_category_where_buy_excludes_sector():
    w = build_category_where(["buy"])
    assert "direction='buy'" in w and "BUY\\_%" in w
    # 板块 SECTOR_ 的 direction 也是 buy, 必须靠前缀分开
    assert "SECTOR" not in w


def test_build_category_where_multi_or():
    w = build_category_where(["buy", "sell", "reduce"])
    assert " OR " in w
    assert "direction='sell'" in w and "direction='reduce'" in w


def test_build_category_where_empty_defaults_to_buy():
    assert "BUY\\_%" in build_category_where([])
    assert "BUY\\_%" in build_category_where(["bogus"])


def test_extract_trade_plan():
    detail = "昨缩量 → 今日放量突破 | 交易计划: +7%卖半/剩半破MA10×0.98/-6%止损/T+10时停 | 成交额 2.6亿"
    assert extract_trade_plan(detail) == "+7%卖半/剩半破MA10×0.98/-6%止损/T+10时停"
    assert extract_trade_plan("无计划段") == ""
    assert extract_trade_plan(None) == ""


def test_parse_exit_plan_full():
    ep = parse_exit_plan("+7%卖半/剩半破MA10×0.98/-6%止损/T+10时停", 100.0)
    assert ep["tp_pct"] == 7.0 and ep["tp_action"] == "卖半"
    assert ep["tp_price"] == 107.0
    assert ep["sl_pct"] == 6.0 and ep["sl_price"] == 94.0
    assert ep["time_stop_days"] == 10
    assert "剩半破MA10×0.98" in ep["other_exit"] and "T+10时停" in ep["other_exit"]


def test_parse_exit_plan_reduce_only():
    ep = parse_exit_plan("+15%减半/-7%止损", 200.0)
    assert ep["tp_pct"] == 15.0 and ep["tp_action"] == "减半" and ep["tp_price"] == 230.0
    assert ep["sl_pct"] == 7.0 and ep["sl_price"] == 186.0
    assert ep["time_stop_days"] is None and ep["other_exit"] == ""


def test_parse_exit_plan_empty():
    ep = parse_exit_plan("", 100.0)
    assert ep["tp_pct"] is None and ep["sl_pct"] is None and ep["other_exit"] == ""
    assert parse_exit_plan(None, None)["tp_price"] is None


def test_compute_kline_returns_basic():
    kl = [
        {"trade_date": "2026-06-01", "high": 105, "low": 98, "close": 102},
        {"trade_date": "2026-06-02", "high": 110, "low": 101, "close": 108},
        {"trade_date": "2026-06-03", "high": 109, "low": 103, "close": 104},
    ]
    r = compute_kline_returns(100.0, "2026-06-01", kl)
    assert round(r["cur_ret_pct"], 2) == 4.0
    assert round(r["max_gain_pct"], 2) == 10.0
    assert round(r["max_dd_pct"], 2) == -2.0
    assert round(r["t1_pct"], 2) == 8.0      # rows[1].close=108
    assert r["t3_pct"] is None               # 不足4根
    assert r["t5_pct"] is None


def test_compute_kline_returns_filters_before_trigger_and_handles_empty():
    kl = [{"trade_date": "2026-05-30", "high": 200, "low": 100, "close": 150}]
    r = compute_kline_returns(100.0, "2026-06-01", kl)   # 全在触发日之前
    assert r["cur_ret_pct"] is None and r["max_gain_pct"] is None
    assert compute_kline_returns(None, "2026-06-01", kl)["cur_ret_pct"] is None


def test_returns_from_perf():
    perf = [
        {"day_offset": 1, "high_pct": 3.0, "low_pct": -1.0, "close_pct": 2.0},
        {"day_offset": 2, "high_pct": 6.0, "low_pct": -4.0, "close_pct": 5.0},
    ]
    r = returns_from_perf(perf)
    assert r["max_gain_pct"] == 6.0 and r["max_dd_pct"] == -4.0
    assert r["t1_pct"] == 2.0 and r["t3_pct"] is None
    assert r["cur_ret_pct"] == 5.0          # 最大 day_offset 的 close_pct
    assert returns_from_perf([]) is None


def test_returns_from_outcome():
    r = returns_from_outcome({"outcome_p1_pct": 1.0, "outcome_p3_pct": 3.0, "outcome_p5_pct": 5.0})
    assert r["t5_pct"] == 5.0 and r["cur_ret_pct"] == 5.0
    assert returns_from_outcome({"outcome_p1_pct": None, "outcome_p3_pct": None, "outcome_p5_pct": None}) is None


def test_summarize_rows():
    rows = [
        {"signal_id": "BUY_X", "signal_name": "X", "cur_ret_pct": 10.0, "max_gain_pct": 12.0,
         "max_dd_pct": -3.0, "t5_pct": 9.0, "outcome": "success"},
        {"signal_id": "BUY_X", "signal_name": "X", "cur_ret_pct": -4.0, "max_gain_pct": 2.0,
         "max_dd_pct": -6.0, "t5_pct": None, "outcome": "fail"},
        {"signal_id": "SECTOR_Y", "signal_name": "Y", "cur_ret_pct": None},  # 板块不计入
    ]
    s = summarize_rows(rows)
    by = {r["signal_id"]: r for r in s}
    assert by["BUY_X"]["count"] == 2
    assert by["BUY_X"]["win_rate"] == 50.0
    assert by["BUY_X"]["avg_cur_ret"] == 3.0
    assert by["BUY_X"]["avg_t5"] == 9.0           # 只 1 个非空
    assert by["BUY_X"]["success_rate"] == 50.0
    assert "SECTOR_Y" not in by                    # 无收益不汇总
    assert by["__ALL__"]["count"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_review_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.review_metrics'`

- [ ] **Step 3: 写实现**

Create `backend/services/review_metrics.py`:

```python
# -*- coding: utf-8 -*-
"""区间复盘清单 — 纯逻辑(无 DB 依赖, 便于单测).

职责: 类别→SQL片段映射、交易计划提取、由 kline/perf/outcome 算收益、按类型汇总。
DB 编排在 repo/signals.py 的 get_review_signal_list。
"""
import re

# 类别 → WHERE 片段。已核实生产库 direction ∈ {buy,sell,reduce,plunge};
# 板块 SECTOR_ 的 direction 也是 buy, 故买点必须叠前缀区分。LIKE 中 _ 转义为 \_。
_CAT_SQL = {
    "buy": "(direction='buy' AND signal_id LIKE 'BUY\\_%')",
    "sell": "direction='sell'",
    "reduce": "direction='reduce'",
    "sector": "signal_id LIKE 'SECTOR\\_%'",
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_review_metrics.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/services/review_metrics.py backend/tests/test_review_metrics.py
git commit -m "feat(review): 区间复盘纯逻辑模块(类别映射/收益计算/汇总)+单测"
```

---

## Task 2: 后端 DB 编排 `get_review_signal_list` + 实库对账

**Files:**
- Modify: `backend/models/repo/signals.py`(新增 `_fetch_perf_map`、`get_review_signal_list`; 复用既有 `fetch_kline_cache_for_codes`、`_fetchall`)
- Create(临时对账, 不提交): `backend/scripts/_verify_review_list.py`

- [ ] **Step 1: 在 `repo/signals.py` 末尾追加编排函数**

先确认文件顶部已 `from datetime import date, timedelta`(若无则在现有 import 区补一行)。在文件末尾追加:

```python
from backend.services.review_metrics import (  # 顶部 import 区更佳, 放文件头
    build_category_where, extract_trade_plan, parse_exit_plan, compute_kline_returns,
    returns_from_perf, returns_from_outcome, summarize_rows,
)


async def _fetch_perf_map(signal_ids):
    """批量取 perf 行, 返回 {signal_pk: [perf_row,...]}。"""
    if not signal_ids:
        return {}
    ph = ",".join(["%s"] * len(signal_ids))
    rows = await _fetchall(
        f"SELECT signal_pk, day_offset, high_pct, low_pct, close_pct "
        f"FROM cfzy_biz_signal_perf WHERE signal_pk IN ({ph})",
        tuple(signal_ids),
    )
    m = {}
    for r in rows:
        m.setdefault(r["signal_pk"], []).append(r)
    return m


def _exit_cols(plan, trigger_price, max_gain_pct, max_dd_pct):
    """解析计划性出场为展示列 + 触及标注(复用区间最大浮盈/浮亏, 不另查; 不算实现收益)。"""
    ep = parse_exit_plan(plan, trigger_price)
    tp_label = f"+{ep['tp_pct']:g}% {ep['tp_action']}" if ep["tp_pct"] else ""
    sl_label = f"-{ep['sl_pct']:g}%" if ep["sl_pct"] else ""
    tp_hit = bool(ep["tp_pct"] and max_gain_pct is not None and max_gain_pct >= ep["tp_pct"])
    sl_hit = bool(ep["sl_pct"] and max_dd_pct is not None and max_dd_pct <= -ep["sl_pct"])
    return {
        "tp_label": tp_label, "tp_price": ep["tp_price"], "tp_hit": tp_hit,
        "sl_label": sl_label, "sl_price": ep["sl_price"], "sl_hit": sl_hit,
        "other_exit": ep["other_exit"],
    }


async def get_review_signal_list(user_id, start, end, categories):
    """区间复盘清单: 返回 {start,end,latest_kline_date,rows,summary}。
    收益主算 kline_cache, 个股移出池(kline缺/不足)时回退 perf, 再回退 outcome 字段。
    """
    where_cat = build_category_where(categories)
    sigs = await _fetchall(
        "SELECT id, code, name, signal_id, signal_name, direction, price, detail, "
        "triggered_at, trigger_date, outcome, outcome_p1_pct, outcome_p3_pct, outcome_p5_pct "
        "FROM cfzy_biz_signals "
        "WHERE user_id=%s AND trigger_date BETWEEN %s AND %s AND " + where_cat +
        " ORDER BY trigger_date DESC, code, signal_id",
        (user_id, start, end),
    )
    if not sigs:
        return {"start": start, "end": end, "latest_kline_date": None, "rows": [], "summary": []}

    codes = sorted({s["code"] for s in sigs})
    earliest = min(str(s["trigger_date"]) for s in sigs)
    # fetch_kline_cache_for_codes 用的是 trade_date > min, 故传触发日前一天以含触发日当根
    kmin = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
    kline_map = await fetch_kline_cache_for_codes(codes, kmin)
    perf_map = await _fetch_perf_map([s["id"] for s in sigs])

    latest_kline_date = None
    for rows in kline_map.values():
        for r in rows:
            d = str(r["trade_date"])
            if latest_kline_date is None or d > latest_kline_date:
                latest_kline_date = d

    out_rows = []
    for s in sigs:
        kl = kline_map.get(s["code"], [])
        kl_after = [k for k in kl if str(k["trade_date"]) >= str(s["trigger_date"])]
        frozen = False
        if s["price"] and kl_after:
            ret = compute_kline_returns(s["price"], s["trigger_date"], kl)
        else:
            ret = returns_from_perf(perf_map.get(s["id"])) or returns_from_outcome(s)
            if ret is None:
                ret = compute_kline_returns(None, s["trigger_date"], [])  # 全 None
            else:
                frozen = True
        plan = extract_trade_plan(s["detail"])
        out_rows.append({
            "code": s["code"], "name": s["name"],
            "signal_id": s["signal_id"], "signal_name": s["signal_name"],
            "direction": s["direction"],
            "trigger_date": str(s["trigger_date"]),
            "trigger_time": s["triggered_at"].strftime("%H:%M") if s["triggered_at"] else "",
            "trigger_price": s["price"],
            "cur_price": ret["cur_price"], "cur_ret_pct": ret["cur_ret_pct"],
            "max_gain_pct": ret["max_gain_pct"], "max_dd_pct": ret["max_dd_pct"],
            "t1_pct": ret["t1_pct"], "t3_pct": ret["t3_pct"], "t5_pct": ret["t5_pct"],
            "frozen": frozen,
            "outcome": s["outcome"],
            "trade_plan": plan,
            "detail": s["detail"],
            **_exit_cols(plan, s["price"], ret["max_gain_pct"], ret["max_dd_pct"]),
        })

    return {
        "start": start, "end": end, "latest_kline_date": latest_kline_date,
        "rows": out_rows, "summary": summarize_rows(out_rows),
    }
```

> 注意: `where_cat` 是受控白名单拼出的静态片段(无用户输入注入风险), 用户输入 `start/end/user_id` 全走参数化 `%s`。

- [ ] **Step 2: 写实库对账脚本**

Create `backend/scripts/_verify_review_list.py`:

```python
# -*- coding: utf-8 -*-
"""对账: get_review_signal_list 6/1~6/5 买点结果须与既有复盘一致(17个股买点)。"""
import asyncio
import aiomysql
from backend.core.config import load_config
from backend.models import database
from backend.models.repo.signals import get_review_signal_list


async def main():
    cfg = load_config().get("database", {})
    database._pool = await aiomysql.create_pool(
        host=cfg["host"], port=cfg.get("port", 3306), user=cfg["user"],
        password=cfg["password"], db=cfg["db"], charset="utf8mb4",
        autocommit=True, minsize=1, maxsize=3,
    )
    res = await get_review_signal_list(1, "2026-06-01", "2026-06-05", ["buy"])
    print("latest_kline_date:", res["latest_kline_date"], " rows:", len(res["rows"]))
    for r in res["rows"]:
        print(f"  {r['code']} {r['name']} {r['signal_id']} 触发{r['trigger_price']} "
              f"现{r['cur_price']} 收益{r['cur_ret_pct']:.2f}% "
              f"高{r['max_gain_pct']:.2f}% 低{r['max_dd_pct']:.2f}% frozen={r['frozen']} "
              f"| 止盈{r['tp_label']}@{r['tp_price']}触及{r['tp_hit']} "
              f"止损{r['sl_label']}@{r['sl_price']}触及{r['sl_hit']} {r['other_exit']}")
    print("汇总:")
    for g in res["summary"]:
        print(f"  {g['signal_id']:<22} 笔{g['count']} 胜{g['win_rate']} 均{g['avg_cur_ret']}")
    database._pool.close()
    await database._pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 跑对账(连库)**

Run(PowerShell, 需 dangerouslyDisableSandbox):
```
$env:PYTHONPATH="D:\财务管理\交易系统\trading-monitor"; $env:PYTHONIOENCODING="utf-8"
& "C:\Users\成剑\AppData\Local\Programs\Python\Python313\python.exe" -m backend.scripts._verify_review_list
```
Expected: `rows: 17`; 明细与上次复盘一致(立昂微 +12.75%、阳光电源 -10.39% 等); 汇总 BUY_VOL_BREAKOUT 笔8 胜25.0、全部17笔。

- [ ] **Step 4: 提交(不含临时脚本)**

```bash
git add backend/models/repo/signals.py
git commit -m "feat(review): get_review_signal_list 区间信号编排(kline主算+perf/outcome兜底)"
```

---

## Task 3: 后端端点 `GET /api/signals/review-list`

**Files:**
- Modify: `backend/routers/signals.py`(新增端点, 仿 outcome-stats)

- [ ] **Step 1: 加端点**

在 `backend/routers/signals.py` 中(`outcome-stats` 端点附近), 确认已 `from fastapi import Query`、`from typing import Annotated`、`Depends(get_current_user)` 可用, 新增:

```python
@router.get("/review-list")
async def review_signal_list(
    user: Annotated[dict, Depends(get_current_user)],
    start: str = Query(..., description="区间起 YYYY-MM-DD"),
    end: str = Query(..., description="区间止 YYYY-MM-DD"),
    categories: str = Query("buy,sell,reduce", description="逗号分隔: buy/sell/reduce/sector/plunge"),
):
    """区间复盘清单: 该区间触发的信号逐条明细(当前收益/区间最大浮盈浮亏/T+1·3·5/评估) + 按类型汇总。"""
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    return await repository.get_review_signal_list(user["id"], start, end, cats)
```

> 核对该文件中 repository 的实际引用名(可能是 `repository` 或 `from backend.models.repo import signals as repository`), 与同文件 outcome-stats 用法保持一致。

- [ ] **Step 2: 手测端点(连库起服务或直调)**

最稳: 复用 Task 2 对账脚本已验证 repo 层; 端点仅薄封装。若要验 HTTP, 本地起后端后:
Run: `curl "http://127.0.0.1:8888/api/signals/review-list?start=2026-06-01&end=2026-06-05&categories=buy"`(需带鉴权, 视项目 get_current_user 实现; 若鉴权复杂则跳过 HTTP 手测, 依赖 Task 2 对账 + 前端联调)。
Expected: JSON 含 `rows`(17) 与 `summary`。

- [ ] **Step 3: 提交**

```bash
git add backend/routers/signals.py
git commit -m "feat(review): GET /api/signals/review-list 端点"
```

---

## Task 4: 前端 API 函数 + 类型 `api/signals.ts`

**Files:**
- Modify: `frontend/src/api/signals.ts`

- [ ] **Step 1: 加类型与函数**

在 `frontend/src/api/signals.ts` 末尾追加:

```typescript
export interface ReviewSignalRow {
  code: string
  name: string
  signal_id: string
  signal_name: string
  direction: string
  trigger_date: string
  trigger_time: string
  trigger_price: number | null
  cur_price: number | null
  cur_ret_pct: number | null
  max_gain_pct: number | null
  max_dd_pct: number | null
  t1_pct: number | null
  t3_pct: number | null
  t5_pct: number | null
  frozen: boolean
  outcome: string | null
  tp_label: string
  tp_price: number | null
  tp_hit: boolean
  sl_label: string
  sl_price: number | null
  sl_hit: boolean
  other_exit: string
  trade_plan: string
  detail: string
}

export interface ReviewSummaryRow {
  signal_id: string
  signal_name: string
  count: number
  win_rate: number | null
  avg_cur_ret: number | null
  median_cur_ret: number | null
  avg_max_gain: number | null
  avg_max_dd: number | null
  avg_t5: number | null
  success_rate: number | null
}

export interface ReviewListResp {
  start: string
  end: string
  latest_kline_date: string | null
  rows: ReviewSignalRow[]
  summary: ReviewSummaryRow[]
}

export async function fetchReviewSignals(
  start: string, end: string, categories: string[],
): Promise<ReviewListResp> {
  const { data } = await client.get('/api/signals/review-list', {
    params: { start, end, categories: categories.join(',') },
  })
  return data
}
```

> 确认 `client` 在该文件已 import(其它 fetch* 函数同款); 若类型集中在单独 d.ts 则按项目惯例放置。

- [ ] **Step 2: 类型检查**

Run: `cd frontend; npx vue-tsc --noEmit`(或项目既有 lint 脚本 `npm run build` 的类型阶段)
Expected: 无新增类型错误。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/api/signals.ts
git commit -m "feat(review): 前端 fetchReviewSignals API + 类型"
```

---

## Task 5: 前端 xlsx 导出工具 `utils/exportXlsx.ts`

**Files:**
- Modify: `frontend/package.json`(加 xlsx 依赖)
- Create: `frontend/src/utils/exportXlsx.ts`

- [ ] **Step 1: 安装 sheetjs**

Run: `cd frontend; npm install xlsx`
Expected: package.json dependencies 出现 `"xlsx": "^0.18.x"`; 生成 package-lock 变更。

- [ ] **Step 2: 写导出工具**

Create `frontend/src/utils/exportXlsx.ts`:

```typescript
import * as XLSX from 'xlsx'
import type { ReviewSignalRow, ReviewSummaryRow } from '../api/signals'

const pct = (v: number | null) => (v == null ? '' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`)

export function exportReviewXlsx(
  rows: ReviewSignalRow[], summary: ReviewSummaryRow[], start: string, end: string,
) {
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
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend; npx vue-tsc --noEmit`
Expected: 无错误(xlsx 自带类型)。

- [ ] **Step 4: 提交**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/utils/exportXlsx.ts
git commit -m "feat(review): xlsx 导出工具 + sheetjs 依赖"
```

---

## Task 6: 前端卡片组件 `IntervalReviewCard.vue`

**Files:**
- Create: `frontend/src/components/review/IntervalReviewCard.vue`

- [ ] **Step 1: 写组件**

Create `frontend/src/components/review/IntervalReviewCard.vue`:

```vue
<script setup lang="ts">
import { ref, h, onMounted } from 'vue'
import {
  NCard, NButton, NButtonGroup, NDatePicker, NCheckboxGroup, NCheckbox,
  NSpace, NDataTable, NSkeleton, NText, NTooltip, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  fetchReviewSignals, type ReviewSignalRow, type ReviewSummaryRow,
} from '../../api/signals'
import { exportReviewXlsx } from '../../utils/exportXlsx'

const message = useMessage()
const loading = ref(false)
const rows = ref<ReviewSignalRow[]>([])
const summary = ref<ReviewSummaryRow[]>([])
const latestKline = ref<string | null>(null)
const range = ref<[number, number] | null>(presetRange(5))
const categories = ref<string[]>(['buy', 'sell', 'reduce'])

const catOptions = [
  { label: '买点', value: 'buy' }, { label: '卖点', value: 'sell' },
  { label: '减仓', value: 'reduce' }, { label: '板块预警', value: 'sector' },
  { label: '大盘风控', value: 'plunge' },
]

// 近 n 个交易日近似: 回推 n*1.5 自然日(覆盖周末), 实际以库内交易日数据为准
function presetRange(n: number): [number, number] {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - Math.ceil(n * 1.5))
  return [start.getTime(), end.getTime()]
}
function fmtDate(ts: number): string {
  const d = new Date(ts)
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const day = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}
function setPreset(n: number) {
  range.value = presetRange(n)
  load()
}

const pctCell = (v: number | null) => {
  if (v == null) return h('span', { style: { color: 'var(--text3,#999)' } }, '—')
  const color = v >= 0 ? 'var(--red,#dc2626)' : 'var(--green,#16a34a)' // A股 正红负绿
  return h('span', { style: { color, fontWeight: 600, fontFamily: 'monospace' } },
    `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`)
}
const outcomeCell = (o: string | null) => {
  const cfg: Record<string, [string, string]> = {
    success: ['成功', 'var(--red,#dc2626)'], fail: ['失败', 'var(--green,#16a34a)'],
    neutral: ['中性', '#888'],
  }
  const [label, color] = cfg[o ?? ''] ?? ['待评估', '#d97706']
  return h('span', { style: { color, fontWeight: 700, fontSize: '12px' } }, label)
}
// 计划出场单元格: label + 目标价 + 触及徽标(已触及橙色高亮)
const exitCell = (label: string, price: number | null, hit: boolean) => {
  if (!label) return h('span', { style: { color: 'var(--text3,#999)' } }, '—')
  const parts = [h('span', {}, `${label} @${price ?? '—'}`)]
  if (hit) parts.push(h('span', { style: { color: '#b45309', fontWeight: 700, marginLeft: '4px' } }, '已触及'))
  return h('span', { style: { fontSize: '12px' } }, parts)
}

const columns: DataTableColumns<ReviewSignalRow> = [
  { title: '代码', key: 'code', width: 72 },
  { title: '名称', key: 'name', width: 80 },
  { title: '信号类型', key: 'signal_name', width: 130 },
  { title: '触发日', key: 'trigger_date', width: 96 },
  { title: '触发价', key: 'trigger_price', width: 80,
    render: r => h('span', {}, r.trigger_price?.toFixed(2) ?? '—') },
  { title: '现价', key: 'cur_price', width: 80,
    render: r => h('span', {}, r.cur_price?.toFixed(2) ?? (r.frozen ? '冻结' : '—')) },
  { title: '当前收益', key: 'cur_ret_pct', width: 92, render: r => pctCell(r.cur_ret_pct) },
  { title: '区间最大浮盈', key: 'max_gain_pct', width: 104, render: r => pctCell(r.max_gain_pct) },
  { title: '区间最大浮亏', key: 'max_dd_pct', width: 104, render: r => pctCell(r.max_dd_pct) },
  { title: 'T+1', key: 't1_pct', width: 78, render: r => pctCell(r.t1_pct) },
  { title: 'T+3', key: 't3_pct', width: 78, render: r => pctCell(r.t3_pct) },
  { title: 'T+5', key: 't5_pct', width: 78, render: r => pctCell(r.t5_pct) },
  { title: '评估', key: 'outcome', width: 72, render: r => outcomeCell(r.outcome) },
  { title: '计划止盈', key: 'tp_label', width: 150, render: r => exitCell(r.tp_label, r.tp_price, r.tp_hit) },
  { title: '计划止损', key: 'sl_label', width: 130, render: r => exitCell(r.sl_label, r.sl_price, r.sl_hit) },
  { title: '时停/其他出场', key: 'other_exit', width: 170, ellipsis: { tooltip: true },
    render: r => r.other_exit || '—' },
  { title: '形态详情', key: 'detail', width: 240,
    render: r => h(NTooltip, null, {
      trigger: () => h('span', { style: { cursor: 'help' } },
        (r.detail ?? '').slice(0, 24) + ((r.detail?.length ?? 0) > 24 ? '…' : '')),
      default: () => r.detail,
    }) },
]

const summaryColumns: DataTableColumns<ReviewSummaryRow> = [
  { title: '信号类型', key: 'signal_name', width: 140,
    render: g => g.signal_id === '__ALL__' ? h('b', {}, '全部') : g.signal_name },
  { title: '笔数', key: 'count', width: 64 },
  { title: '胜率', key: 'win_rate', width: 80, render: g => pctCell(g.win_rate) },
  { title: '均当前收益', key: 'avg_cur_ret', width: 100, render: g => pctCell(g.avg_cur_ret) },
  { title: '中位', key: 'median_cur_ret', width: 90, render: g => pctCell(g.median_cur_ret) },
  { title: '均最大浮盈', key: 'avg_max_gain', width: 100, render: g => pctCell(g.avg_max_gain) },
  { title: '均最大浮亏', key: 'avg_max_dd', width: 100, render: g => pctCell(g.avg_max_dd) },
  { title: 'T+5均', key: 'avg_t5', width: 84, render: g => pctCell(g.avg_t5) },
  { title: 'success率', key: 'success_rate', width: 92, render: g => pctCell(g.success_rate) },
]

async function load() {
  if (!range.value) { message.warning('请选择区间'); return }
  if (!categories.value.length) { message.warning('至少勾选一个类别'); return }
  loading.value = true
  try {
    const resp = await fetchReviewSignals(
      fmtDate(range.value[0]), fmtDate(range.value[1]), categories.value)
    rows.value = resp.rows
    summary.value = resp.summary
    latestKline.value = resp.latest_kline_date
  } catch (e) {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

function onExport() {
  if (!rows.value.length) { message.warning('无数据可导出'); return }
  exportReviewXlsx(rows.value, summary.value,
    fmtDate(range.value![0]), fmtDate(range.value![1]))
}

onMounted(load)
</script>

<template>
  <NCard title="区间复盘清单" size="small" :bordered="false" style="margin-bottom:16px">
    <NSpace vertical :size="10">
      <NSpace align="center" :size="8" wrap>
        <NButtonGroup size="small">
          <NButton @click="setPreset(5)">近5日</NButton>
          <NButton @click="setPreset(10)">近2周</NButton>
          <NButton @click="setPreset(22)">近1月</NButton>
          <NButton @click="setPreset(66)">近3月</NButton>
        </NButtonGroup>
        <NDatePicker v-model:value="range" type="daterange" size="small" clearable to="body" />
        <NButton size="small" type="primary" @click="load">查询</NButton>
        <NButton size="small" @click="onExport">导出xlsx</NButton>
      </NSpace>
      <NCheckboxGroup v-model:value="categories">
        <NSpace :size="12">
          <NCheckbox v-for="o in catOptions" :key="o.value" :value="o.value" :label="o.label" />
        </NSpace>
      </NCheckboxGroup>
      <NText depth="3" style="font-size:12px">
        当前收益基准 = 行情库最新收盘 {{ latestKline ?? '—' }}; "冻结"= 个股已移出池, 用历史快照兜底。
      </NText>

      <NSkeleton v-if="loading" :repeat="4" text />
      <template v-else>
        <NDataTable :columns="columns" :data="rows" size="small" :bordered="false"
          :scroll-x="2150" :max-height="460" :row-key="(r:ReviewSignalRow)=>r.code+r.signal_id+r.trigger_date" />
        <NText strong style="margin-top:8px;display:block">按信号类型汇总</NText>
        <NDataTable :columns="summaryColumns" :data="summary" size="small" :bordered="false" />
      </template>
    </NSpace>
  </NCard>
</template>
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend; npx vue-tsc --noEmit`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/review/IntervalReviewCard.vue
git commit -m "feat(review): 区间复盘清单卡片组件(筛选/明细/汇总/导出)"
```

---

## Task 7: 挂入 ReviewView + 变更日志 + 端到端手测

**Files:**
- Modify: `frontend/src/views/ReviewView.vue`(挂入卡片)
- Modify: `frontend/src/data/changelog.ts`(版本记录)

- [ ] **Step 1: 在 ReviewView 顶部挂卡片**

`frontend/src/views/ReviewView.vue` script 顶部 import:
```typescript
import IntervalReviewCard from '../components/review/IntervalReviewCard.vue'
```
template 中, 在 `<div class="page-header">…</div>` 之后、原有 `<NSkeleton>` / `compare-grid` 之前插入:
```vue
    <IntervalReviewCard />
```

- [ ] **Step 2: 加变更日志(项目规约: 改逻辑必加)**

`frontend/src/data/changelog.ts` 数组**头部**插入(版本号在当前最新 v1.7.323 基础上+1):
```typescript
  {
    version: 'v1.7.324',
    date: '2026-06-07',
    title: '复盘页新增「区间复盘清单」',
    changes: [
      { text: '复盘页顶部新增「区间复盘清单」: 自选时间区间(预设近5日/2周/1月/3月 或自定义起止), 按类别(买点/卖点/减仓/板块/大盘)勾选, 逐条列出该区间触发信号的触发价/当前收益/区间最大浮盈浮亏/T+1·3·5/评估结论, 并按信号类型汇总。', tag: 'new' },
      { text: '收益主算行情库最新收盘, 个股移出池则回退冻结快照(perf/outcome)兜底; 支持一键导出 xlsx(个股明细+按类型汇总两表)。', tag: 'new' },
      { text: '每个买点解析自带「交易计划」的计划性出场(止盈价/止损价/减仓动作/时停), 并按实际K线路径标注是否已触及(只列计划目标价, 不做卖出收益仿真)。', tag: 'new' },
    ],
  },
```

- [ ] **Step 3: 构建确认**

Run: `cd frontend; npm run build`
Expected: 构建成功, 无类型/编译错误。

- [ ] **Step 4: 端到端手测(起后端+前端)**

启动后端与前端 dev(项目既有方式), 浏览器开 `/review`:
- 默认载入"近5日 + 买卖减"清单, 表格有数据, 收益正红负绿。
- 点「近1月」按钮 → 区间与数据刷新。
- 自定义起止 2026-06-01~2026-06-05 + 只勾买点 → 17 行, 立昂微 +12.75%。
- 计划止盈/止损列正确: 宇环数控止盈+7%@47.21 已触及; 京能电力止损-6%@8.97 已触及; 强势起点/弱势极限无计划显示 —。
- 勾「大盘风控」→ 出现 PLUNGE_ 行且收益列为 —。
- 点「导出xlsx」→ 下载 `区间复盘_2026-06-01_2026-06-05.xlsx`, 两个 sheet 数据正确。
- 汇总表末行「全部」合计正确。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/ReviewView.vue frontend/src/data/changelog.ts
git commit -m "feat(review): 区间复盘清单挂入复盘页 + changelog v1.7.324"
```

---

## Task 8: 清理临时脚本 + 部署

- [ ] **Step 1: 删除本会话临时诊断脚本**

```bash
git rm -f --ignore-unmatch backend/scripts/_review_recent_buys.py backend/scripts/_review_regime.py backend/scripts/_review_export_xlsx.py backend/scripts/_verify_review_list.py
```
(这些是探索期一次性脚本, 功能已产品化进正式代码; 若用户想留作离线工具则跳过此步。)

- [ ] **Step 2: 部署(项目规约: 改完默认部署云端)**

按 [[auto-deploy]] 既有步骤部署(deploy.ps1)。部署后云端 `/review` 验证卡片正常。

- [ ] **Step 3: 提交清理**

```bash
git add -A backend/scripts
git commit -m "chore(review): 清理探索期临时复盘脚本"
```

---

## Self-Review 结论(写计划时已核对)

- **Spec 覆盖**: 位置(Task7)/区间预设+自定义(Task6)/类别过滤(Task1·2)/四收益口径(Task1·2)/取数方案A(Task2)/计划性出场卖点列(Task1 parse_exit_plan + Task2 _exit_cols + Task5·6 列)/导出xlsx(Task5)/汇总(Task1·6) 均有对应任务。
- **类型一致**: 后端 ret dict 七键(cur_price/cur_ret_pct/max_gain_pct/max_dd_pct/t1·t3·t5_pct)贯穿 Task1→2; 计划出场七字段(tp_label/tp_price/tp_hit/sl_label/sl_price/sl_hit/other_exit)贯穿 Task2→4→5→6; 前端 ReviewSignalRow/ReviewSummaryRow 字段贯穿 Task4→5→6; 端点 `/api/signals/review-list` 与 fetchReviewSignals 路径一致。
- **占位符**: 无 TBD/TODO, 各步含完整代码与命令。
- **已知留口**: 端点 HTTP 手测依赖项目鉴权实现(Task3 Step2 给了降级路径, 用 Task2 实库对账兜底), 非阻塞。
