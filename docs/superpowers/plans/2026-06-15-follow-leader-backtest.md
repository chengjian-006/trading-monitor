# 看A做B 龙头跟风 — Phase 0 粗验证回测 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用全市场历史日线粗验证「龙头涨停封板 → 同行业滞涨放量走强跟风」这条滤网相对「随便买同行业票」基线有没有正向 edge,作为是否进 Phase 1 live 的闸门。

**Architecture:** 一个一次性预拉脚本 `bt_follow_prep.py`(本机连东财拉全市场 code→行业 缓存到 `industry_map.json`),一个回测脚本 `bt_follow.py`(逐日跨股票:按行业分组找涨停龙头→筛同组滞涨放量走强跟风→次日开盘买入→右侧快出族出场模拟→与基线对照出报告)。纯函数(涨停判定/跟风谓词/出场模拟)抽出来做单测,跨股票逐日逻辑放 `main()` 由报告产物校验。

**Tech Stack:** Python 3 (`py -3` 本机跑), pandas/numpy, 复用 `backend.services.signal_engine_indicators.compute_indicators`,东财 push2 clist 接口(本机可用,封的是 prod IP),bt_cache 全市场日线库(`backend/scripts/bt_cache/kl/*.pkl`,~4957 只)。

> **入库约定(2026-06-15 校正):** 本项目 `backend/scripts/bt_*.py` 与 `backend/scripts/bt_cache/` 均被 `.gitignore` 刻意忽略——71 个回测脚本全本地、不入库。故本计划所有脚本(`bt_follow_prep.py`/`bt_follow.py`)、产物、以及依赖它们的单测 `test_bt_follow.py` **一律留本地、不 commit**(单测照写、本地 `py -3 -m pytest` 验)。下方各任务原"提交"步骤改为本地留存,不再 git add/commit。

**口径**(来自 spec `docs/superpowers/specs/2026-06-15-follow-leader-model-design.md`):
- 同板块 = 东财行业板块(回测期口径,概念留到 live)
- 龙头 A = 行业内当日涨停封板(日线近似:收盘≈涨停价),leader_pct = 组内涨停票最大涨幅
- 跟风 B = 同行业 + 当日涨幅 ∈ (0, 5%] 且 < leader_pct + 放量(量 ≥ 近10日均量×1.5)+ 走强(收盘在当日 (high+low)/2 上方,作"站上分时均价"的日线近似)
- 买入价 = 信号次日开盘价
- 出场 = 右侧快出族:+7% 卖半 / 剩半收盘破 MA10×0.98 全清 / 盘中 −6% 止损 / T+10 时停
- 基线 = 同一「发车」交易日同行业**全部非龙头票**(不加跟风滤网)同样次日开盘买入、同样出场
- 局限(报告须标注):日线近似封板时点偏乐观;ST/北交所按代码无法精确剔,靠 universe 已基本不含。

---

### Task 1: 全市场 code→行业 预拉脚本

**Files:**
- Create: `backend/scripts/bt_follow_prep.py`
- 产物: `backend/scripts/bt_cache/industry_map.json`(`{code: 行业名}`)

> 说明:本机连东财(`push2.eastmoney.com` clist,`fs=b:{bk}`)逐行业拉成分股,封的是 prod IP,本机可用。一次性运行,结果落盘供回测复用。无单测(纯一次性取数脚本,产物覆盖率人工核对)。

- [ ] **Step 1: 写预拉脚本**

```python
# -*- coding: utf-8 -*-
"""一次性预拉: 全市场 code → 东财行业板块名, 落盘 bt_cache/industry_map.json.
本机运行 (py -3 backend/scripts/bt_follow_prep.py). 封的是 prod IP, 本机可用.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.fetcher.http_client import EM_HEADERS, _get_client
from backend import data_fetcher

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "bt_cache", "industry_map.json")


async def main():
    bk_map = await data_fetcher.get_industry_bk_map()   # {行业名: bk_code}
    if not bk_map:
        print("行业 BK 映射为空, 东财不可用?"); return
    client = _get_client()
    code2ind: dict[str, str] = {}
    for name, bk in bk_map.items():
        url = (f"https://push2.eastmoney.com/api/qt/clist/get"
               f"?pn=1&pz=1000&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:{bk}&fields=f12,f14")
        try:
            resp = await client.get(url, headers=EM_HEADERS)
            diff = resp.json().get("data", {}).get("diff", []) or []
        except Exception as e:
            print(f"  行业 {name}({bk}) 取数失败: {e}"); continue
        cnt = 0
        for d in diff:
            code = d.get("f12")
            if code:
                code2ind[code] = name
                cnt += 1
        print(f"  {name}({bk}): {cnt} 只")
        await asyncio.sleep(0.1)
    json.dump(code2ind, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"写入 {OUT}: {len(code2ind)} 只票, {len(set(code2ind.values()))} 个行业")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 运行预拉并核对覆盖率**

Run: `py -3 backend/scripts/bt_follow_prep.py`
Expected: 末行打印类似 `写入 .../industry_map.json: 5000+ 只票, 80+ 个行业`。若总数 <4000,说明东财部分行业没拉到,排查后重跑(覆盖应接近 bt_cache 的 4957 只)。

- [ ] **Step 3: 本地留存(不入库)**

`bt_follow_prep.py` 被 `.gitignore` 覆盖,与其它 bt 脚本一致,留本地即可,不 git add/commit。

---

### Task 2: 纯函数 — 涨停判定 / 跟风谓词 / 出场模拟 + 单测

**Files:**
- Create: `backend/scripts/bt_follow.py`(本任务只放顶部纯函数)
- Test: `backend/tests/test_bt_follow.py`

- [ ] **Step 1: 写失败的单测**

```python
# backend/tests/test_bt_follow.py
"""bt_follow 纯函数单测: 涨停判定 / 跟风谓词 / 右侧快出族出场模拟. 不连库不联网."""
import numpy as np

from backend.scripts.bt_follow import is_limit_up, is_follower, exit_simulate


def test_limit_up_main_board():
    assert is_limit_up("600000", close=11.0, prev_close=10.0) is True
    assert is_limit_up("600000", close=10.9, prev_close=10.0) is False


def test_limit_up_chinext_20pct():
    assert is_limit_up("300001", close=12.0, prev_close=10.0) is True
    assert is_limit_up("300001", close=11.5, prev_close=10.0) is False


def test_follower_positive():
    assert is_follower(pct=0.03, vol=2000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.4, leader_pct=0.10) is True


def test_follower_rejects_too_strong():
    # 涨幅 6% > 5% 上限
    assert is_follower(pct=0.06, vol=2000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.4, leader_pct=0.10) is False


def test_follower_rejects_no_volume():
    # 量 1000 < 近10日均×1.5
    assert is_follower(pct=0.03, vol=1000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.4, leader_pct=0.10) is False


def test_follower_rejects_weak_close():
    # 收盘 10.1 在 (high+low)/2=10.25 之下 → 不算走强
    assert is_follower(pct=0.03, vol=2000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.1, leader_pct=0.10) is False


def test_exit_hits_target_then_runner():
    # entry 日开盘 10; 次日盘中冲 10.8(≥+7%卖半); 再隔日收盘 9.0 破 MA10×0.98 清剩半
    opens = np.array([10.0, 10.0, 10.6])
    highs = np.array([10.2, 10.8, 10.7])
    lows = np.array([9.9, 10.3, 8.9])
    closes = np.array([10.0, 10.6, 9.0])
    ma10 = np.array([np.nan, 10.5, 10.0])
    ret, hold, status, hit = exit_simulate(10.0, opens, highs, lows, closes, ma10, j=0, n=3)
    assert status == "runner_ma10" and hit is True
    # 0.5*0.07 + 0.5*(9.0/10-1) = 0.035 - 0.05 = -0.015
    assert abs(ret - (-0.015)) < 1e-9


def test_exit_hard_stop():
    opens = np.array([10.0, 10.0])
    highs = np.array([10.1, 10.0])
    lows = np.array([9.3, 9.3])   # 盘中触及 -6% (≤9.4)
    closes = np.array([10.0, 9.5])
    ma10 = np.array([np.nan, np.nan])
    ret, hold, status, hit = exit_simulate(10.0, opens, highs, lows, closes, ma10, j=0, n=2)
    assert status == "stop_-6%" and hit is False
    assert abs(ret - (-0.06)) < 1e-9


def test_exit_time_stop_full():
    # 全程不触发任何规则 → T+10 时停按收盘截尾
    n = 12
    opens = np.full(n, 10.0); highs = np.full(n, 10.2)
    lows = np.full(n, 9.9); closes = np.full(n, 10.1)
    ma10 = np.full(n, 9.0)
    ret, hold, status, hit = exit_simulate(10.0, opens, highs, lows, closes, ma10, j=0, n=n)
    assert status == "to_full" and hit is False
    assert hold == 10
```

- [ ] **Step 2: 运行测试确认失败**

Run: `py -3 -m pytest backend/tests/test_bt_follow.py -v`
Expected: FAIL,`ModuleNotFoundError` 或 `ImportError: cannot import name 'is_limit_up' from 'backend.scripts.bt_follow'`(文件/函数还没建)。

- [ ] **Step 3: 写 bt_follow.py 顶部纯函数实现**

```python
# -*- coding: utf-8 -*-
"""看A做B 龙头跟风 (BUY_FOLLOW_LEADER) 粗验证回测.

口径(spec 2026-06-15-follow-leader-model-design.md):
  龙头 A = 行业内当日涨停封板(日线近似: 收盘≈涨停价)
  跟风 B = 同行业 + 涨幅∈(0,5%]且<龙头 + 放量(≥近10日均×1.5) + 走强(收盘在(H+L)/2上方)
  买入 = 信号次日开盘; 出场 = 右侧快出族 (+7%卖半/剩半破MA10×0.98/-6%/T+10)
  基线 = 同发车日同行业全部非龙头票, 不加跟风滤网, 同买同出
局限: 日线近似封板时点偏乐观; ST 按代码无法剔(universe 已基本不含).
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.services.signal_engine_indicators import compute_indicators

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "bt_cache")
KL = os.path.join(CACHE, "kl")

WINDOW_START = "2025-12-01"
LISTED_BEFORE = "2025-08-31"
MIN_BARS = 65
DEDUP_GAP_DAYS = 5

# 跟风谓词参数
FOLLOW_MAX_PCT = 0.05      # 滞涨上限
VOL_MULT = 1.5            # 放量倍数(对近10日均量)
VOL_WIN = 10
# 出场(右侧快出族)
TARGET = 0.07
HARD_STOP = -0.06
MA10_TOL = 0.02
CAP = 10


def limit_pct(code):
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.startswith(("8", "43", "92")):   # 北交所(universe 已基本不含, 兜底)
        return 0.30
    return 0.10


def is_limit_up(code, close, prev_close):
    if prev_close is None or prev_close <= 0 or np.isnan(prev_close) or np.isnan(close):
        return False
    return close >= prev_close * (1 + limit_pct(code)) * 0.995   # 容差吸收四舍五入


def is_follower(pct, vol, vol_ma10, high, low, close, leader_pct):
    if np.isnan(pct) or np.isnan(vol_ma10) or vol_ma10 <= 0:
        return False
    if not (0 < pct <= FOLLOW_MAX_PCT):       # 滞涨: 涨幅 (0, 5%]
        return False
    if not (pct < leader_pct):                # 仍弱于龙头
        return False
    if vol < vol_ma10 * VOL_MULT:             # 放量
        return False
    if close < (high + low) / 2:              # 走强(站上分时均价的日线近似)
        return False
    return True


def exit_simulate(entry, opens, highs, lows, closes, ma10, j, n):
    """j = 买入日(信号次日)索引; 从 j 当日起可触发. 返回 (ret, hold, status, hit_target)."""
    half = False
    ret_first = 0.0
    last = min(j + CAP, n - 1)
    for t in range(j, last + 1):
        o, h, l, c = opens[t], highs[t], lows[t], closes[t]
        m10 = ma10[t]
        if not half:
            if l <= entry * (1 + HARD_STOP):                       # 盘中 -6% 止损
                fill = min(entry * (1 + HARD_STOP), o)             # 跳空低开按开盘
                return fill / entry - 1.0, t - j, "stop_-6%", False
            if h >= entry * (1 + TARGET):                          # +7% 卖半
                ret_first = (o / entry - 1.0) if o >= entry * (1 + TARGET) else TARGET
                half = True
                continue
        else:
            if not np.isnan(m10) and c < m10 * (1 - MA10_TOL):     # 剩半破 MA10×0.98
                return 0.5 * ret_first + 0.5 * (c / entry - 1.0), t - j, "runner_ma10", True
    c = closes[last]
    if half:
        return 0.5 * ret_first + 0.5 * (c / entry - 1.0), last - j, "to_half", True
    return c / entry - 1.0, last - j, "to_full", False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `py -3 -m pytest backend/tests/test_bt_follow.py -v`
Expected: PASS(9 个用例全绿)。

- [ ] **Step 5: 本地留存(不入库)**

`bt_follow.py` 与依赖它的 `test_bt_follow.py` 均留本地(脚本 gitignore;单测 import 该脚本,入库会在干净检出下 ImportError),不 git add/commit。单测以本地 pytest 通过为准。

---

### Task 3: 逐日跨股票候选生成 + 出场模拟 + 基线对照 + 报告

**Files:**
- Modify: `backend/scripts/bt_follow.py`(追加数据装载、`main()`)
- 产物: `bt_cache/follow_trades.csv`、`bt_cache/follow_baseline.csv`、`bt_cache/follow_report.txt`

- [ ] **Step 1: 追加数据装载与逐日扫描 main()**

在 `bt_follow.py` 末尾(纯函数之后)追加:

```python
def board_of(code):
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("60", "00")):
        return "主板"
    return "其他"


def load_store():
    """逐票算指标, 产出 store[code]=arrays(供出场模拟/取次日开盘) 与
    跨截面长表 rows(每个窗口内交易日一行: date/code/industry/pct/is_limit/...)。"""
    ind_map = json.load(open(os.path.join(CACHE, "industry_map.json"), encoding="utf-8"))
    uni = json.load(open(os.path.join(CACHE, "universe.json"), encoding="utf-8"))
    name_by = {c: n for c, s, n in uni}
    files = [f for f in os.listdir(KL) if f.endswith(".pkl")]
    store = {}
    rows = []
    for f in files:
        code = f[:-4]
        industry = ind_map.get(code)
        if not industry:                       # 无行业归属的票不参与(无法判同板块)
            continue
        try:
            df = pd.read_pickle(os.path.join(KL, f))
        except Exception:
            continue
        if df.empty or len(df) < MIN_BARS + 5:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        if df["date"].iloc[0] > LISTED_BEFORE:
            continue
        ind = compute_indicators(df)
        opens = ind["open"].values; highs = ind["high"].values
        lows = ind["low"].values; closes = ind["close"].values
        ma10 = ind["ma10"].values; vol = ind["volume"].values
        dates = ind["date"].values
        n = len(ind)
        prev_close = ind["close"].shift(1).values
        vol_ma10 = ind["volume"].rolling(VOL_WIN).mean().shift(1).values  # 近10日(不含当日)均量
        pct = closes / prev_close - 1.0
        store[code] = {"opens": opens, "highs": highs, "lows": lows,
                       "closes": closes, "ma10": ma10, "dates": dates, "n": n}
        for i in range(MIN_BARS, n - 1):       # 需有 i+1 做次日买入
            d = str(dates[i])[:10]
            if d < WINDOW_START:
                continue
            rows.append({
                "date": d, "code": code, "industry": industry,
                "i": i, "pct": pct[i], "vol": vol[i], "vol_ma10": vol_ma10[i],
                "high": highs[i], "low": lows[i], "close": closes[i],
                "is_limit": is_limit_up(code, closes[i], prev_close[i]),
            })
    return store, name_by, pd.DataFrame(rows)


def run_trades(cands, store, name_by, label):
    """cands: [{code,date,i}] → 次日开盘买入 + 出场模拟 → trades DataFrame。"""
    trades = []
    for c in cands:
        code = c["code"]; i = c["i"]; s = store[code]
        j = i + 1
        if j >= s["n"]:
            continue
        entry = s["opens"][j]
        if entry <= 0 or np.isnan(entry):
            continue
        ret, hold, status, hit = exit_simulate(
            entry, s["opens"], s["highs"], s["lows"], s["closes"], s["ma10"], j, s["n"])
        trades.append({"code": code, "name": name_by.get(code, code), "board": board_of(code),
                       "date": c["date"], "entry": float(entry), "ret": ret,
                       "hold": hold, "status": status, "hit": hit, "set": label})
    return pd.DataFrame(trades)


def dedup_per_code(cands):
    """同股信号间隔 ≤DEDUP_GAP_DAYS 交易日合并为一笔(按 i 间隔近似交易日)。"""
    by_code = defaultdict(list)
    for c in cands:
        by_code[c["code"]].append(c)
    kept = []
    for code, lst in by_code.items():
        lst.sort(key=lambda x: x["i"])
        last_i = None
        for c in lst:
            if last_i is None or c["i"] - last_i > DEDUP_GAP_DAYS:
                kept.append(c)
                last_i = c["i"]
    return kept


def main():
    store, name_by, df = load_store()
    follow_cands = []
    base_cands = []
    # 逐(交易日, 行业)分组: 组内有涨停=发车; 跟风按谓词, 基线=组内全部非龙头
    for (date, industry), g in df.groupby(["date", "industry"]):
        limits = g[g["is_limit"]]
        if limits.empty:
            continue
        leader_pct = float(limits["pct"].max())
        non_leader = g[~g["is_limit"]]
        for _, r in non_leader.iterrows():
            base_cands.append({"code": r["code"], "date": date, "i": int(r["i"])})
            if is_follower(r["pct"], r["vol"], r["vol_ma10"], r["high"],
                           r["low"], r["close"], leader_pct):
                follow_cands.append({"code": r["code"], "date": date, "i": int(r["i"])})

    follow_cands = dedup_per_code(follow_cands)
    base_cands = dedup_per_code(base_cands)
    tf = run_trades(follow_cands, store, name_by, "follow")
    tb = run_trades(base_cands, store, name_by, "baseline")
    tf.to_csv(os.path.join(CACHE, "follow_trades.csv"), index=False, encoding="utf-8-sig")
    tb.to_csv(os.path.join(CACHE, "follow_baseline.csv"), index=False, encoding="utf-8-sig")
    report(tf, tb)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 追加 report() 统计输出**

在 `bt_follow.py` 末尾(`if __name__` 之前)追加:

```python
def _stat_line(t):
    r = t["ret"].values * 100
    n = len(r)
    if n == 0:
        return "  (无样本)"
    wins = r[r > 0]; losses = r[r <= 0]
    wr = len(wins) / n * 100
    aw = wins.mean() if len(wins) else 0.0
    al = losses.mean() if len(losses) else 0.0
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    return (f"  n={n} 覆盖{t['code'].nunique()}股 | 胜率 {wr:.1f}% | 均收益 {r.mean():+.2f}% | "
            f"中位 {np.median(r):+.2f}% | 平均盈 {aw:+.2f}% 亏 {al:+.2f}% | 盈利因子 {pf:.2f} | "
            f"均持有 {t['hold'].mean():.1f}日")


def report(tf, tb):
    out = []
    out.append("=" * 88)
    out.append(f"看A做B 龙头跟风 粗验证  窗口 {WINDOW_START}~ (信号次日开盘买入, 未扣费)")
    out.append("龙头=行业内涨停封板(日线近似) | 跟风=同行业+涨幅(0,5%]<龙头+放量≥10日均×1.5+收盘在(H+L)/2上方")
    out.append("出场=右侧快出族 +7%卖半/剩半破MA10×0.98/-6%/T+10")
    out.append("⚠ 局限: 日线近似封板时点偏乐观(日内龙头封板时跟风票可能已涨上去), 实盘可得性更低, 结论打折看")
    out.append("=" * 88)
    out.append("【跟风滤网】")
    out.append(_stat_line(tf))
    out.append("【基线: 发车日同行业随便买非龙头票】")
    out.append(_stat_line(tb))
    if len(tf) and len(tb):
        edge = tf["ret"].mean() * 100 - tb["ret"].mean() * 100
        wr_edge = (tf["ret"] > 0).mean() * 100 - (tb["ret"] > 0).mean() * 100
        out.append("-" * 88)
        out.append(f"增量 edge: 单笔均收益 {edge:+.2f}pp | 胜率 {wr_edge:+.1f}pp  (跟风 − 基线)")
    if len(tf):
        out.append("跟风出场分布: " + " | ".join(
            f"{k}:{v}笔({v/len(tf)*100:.0f}%,均{tf[tf['status']==k]['ret'].mean()*100:+.1f}%)"
            for k, v in tf["status"].value_counts().items()))
        tf2 = tf.copy(); tf2["mo"] = tf2["date"].str[:7]
        out.append("跟风分月: " + " ".join(
            f"{mo}:n{gp.size}/胜{(gp>0).mean()*100:.0f}%/{gp.mean()*100:+.1f}%"
            for mo, gp in tf2.groupby("mo")["ret"]))
    txt = "\n".join(out)
    print(txt)
    open(os.path.join(CACHE, "follow_report.txt"), "w", encoding="utf-8").write(txt)
```

- [ ] **Step 3: 跑回测**

Run: `py -3 backend/scripts/bt_follow.py`
Expected: 终端打印报告,出现「跟风滤网」「基线」两组 `n=.../胜率.../盈利因子...` 与「增量 edge」行;`bt_cache/follow_report.txt`、`follow_trades.csv`、`follow_baseline.csv` 生成。若跟风 n=0,放宽前先核对 `industry_map.json` 是否拉全、`is_limit_up` 容差是否过严(打印当日涨停票数自检)。

- [ ] **Step 4: 本地留存(不入库)**

`bt_follow.py` 与 `bt_cache/` 产物均 gitignore,留本地即可,不 git add/commit。

---

### Task 4: 闸门决策(人工)

> 非代码任务。读 `follow_report.txt`,据「增量 edge」与样本量判定是否进 Phase 1 live。

- [ ] **Step 1: 判据**
  - 跟风滤网相对基线:单笔均收益 edge 显著为正(≳ +0.5pp)**或**胜率 edge 显著为正,且跟风样本量足够(n ≳ 数百,非个位数),且分月不是靠单月堆出来 → **进 Phase 1**(另写 live 实现计划)。
  - edge 不显著 / 跟风 ≤ 基线 / 样本过薄 → **不上线**,把结论写进 `project_open-threads` 台账与一条 `project_*` 记忆,留存回测产物。

- [ ] **Step 2: 记录结论**
  - 无论上不上,都在内存台账记一笔(模型名、窗口、edge 数字、是否上线、局限),保持「未闭环事项实时入账」规约。

---

## 自检(plan vs spec)

- spec §6 回测的数据/涨停近似/行业口径/逻辑/基线/局限/判据 → Task 1~4 全覆盖。
- spec §4 模型规则(滞涨≤5%且<龙头、放量≥10日均×1.5、走强、右侧出场)→ `is_follower`/`exit_simulate` 参数一一对应。
- spec §7 Phase 1 live、§8 收口清单 → **不在本计划**,待回测过闸后另写(本计划仅 Phase 0)。
- 类型/命名一致:`exit_simulate(...,j,n)` 在 Task2 定义、Task3 调用签名一致;`is_follower`/`is_limit_up` 参数跨任务一致;产物文件名 `follow_trades.csv`/`follow_baseline.csv`/`follow_report.txt` 前后一致。
- 无占位符:每个代码步骤为完整可运行代码。
