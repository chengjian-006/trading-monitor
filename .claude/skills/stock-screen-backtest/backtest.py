# -*- coding: utf-8 -*-
"""历史战绩回测 —— 全程 5 分钟真实可成交口径。

对每个模型: 逐只逐日跑检测器(日线形态前提) → 5分钟盘中触发判定可成交 + 真实入场价 →
5分钟盘中出场仿真 → 统计胜率/均收/PF, 含按月分解。详见 _engine.py。

环境变量(均可选):
  BT_START=2025-06-19  BT_END=2026-06-19   回测区间(默认近1年)
  BT_UNIVERSE=all      范围: all | pool:<用户id> | 代码逗号列表 (默认 all 全市场)
  BT_MODELS=BUY_STRONG_START   只回测这些模型(默认全部6个)
  BT_MONTHLY=1         额外输出按月表

运行: cd 项目根 && BT_UNIVERSE=pool:1 py -3 -u .claude/skills/stock-screen-backtest/backtest.py
注意: 全市场+全部模型 5分钟口径较慢(~1.5h); 自选股或单模型快很多。
"""
import asyncio
import os
import sys
from collections import defaultdict
from datetime import date as _date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _engine import (MODELS, MODEL_BY_ID, MIN_BARS, DEDUP_DAYS, load_daily_one,
                     load_5m_one, universe_codes, fire_5m, entry_price, simulate_exit,
                     daily_could_fire, stat, fmt_stat)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from backend.models.database import init_db
from backend.services.signal_engine_indicators import compute_indicators


def _default_range():
    end = _date.today()
    start = end - timedelta(days=366)
    return (os.environ.get("BT_START", start.isoformat()),
            os.environ.get("BT_END", end.isoformat()))


async def run():
    await init_db()
    start, end = _default_range()
    uni = os.environ.get("BT_UNIVERSE", "all")
    bt_models = os.environ.get("BT_MODELS", "")
    models = [MODEL_BY_ID[mid] for mid in bt_models.split(",") if mid in MODEL_BY_ID] if bt_models else MODELS
    monthly = bool(os.environ.get("BT_MONTHLY"))

    codes = await universe_codes(uni)
    print(f"回测区间={start}~{end}  范围={uni}({len(codes)}只)  模型={[m['id'] for m in models]}  口径=5分钟真实可成交", flush=True)

    # model_id -> {"all":[ret], "month": {ym:[ret]}}
    res = {m["id"]: {"all": [], "month": defaultdict(list)} for m in models}
    done = 0
    for code in codes:
        done += 1
        if done % 500 == 0:
            print(f"  ...{done}/{len(codes)}", flush=True)
        df = await load_daily_one(code)
        if df is None:
            continue
        ind = compute_indicators(df)
        ind["amount_est"] = ind["volume"] * ind["close"]
        dates = ind["date"].astype(str).values
        n = len(ind)
        day5m = await load_5m_one(code)
        if not day5m:
            continue
        for m in models:
            last_dt = None
            for i in range(MIN_BARS, n):
                dstr = dates[i][:10]
                if dstr < start or dstr > end:
                    continue
                bars = day5m.get(dstr)
                if not bars:
                    continue
                sub = ind.iloc[:i + 1]
                row = ind.iloc[i]
                # 日线粗筛: 全天量口径都不触发 → intraday 必不触发, 跳过(必要条件, 不丢信号, 大幅加速)
                if not daily_could_fire(m, sub, row):
                    continue
                prev_close = float(ind["close"].iloc[i - 1])
                fired, _amt, _r = fire_5m(m, sub, row.copy(), bars, prev_close)
                if not fired:   # 日线候选但盘中真实量/额口径下不可成交 → 不计入
                    continue
                pdt = pd.Timestamp(dstr)
                if last_dt is not None and (pdt - last_dt).days <= DEDUP_DAYS:
                    last_dt = pdt
                    continue
                last_dt = pdt
                ret = simulate_exit(entry_price(m, ind, i), i, ind, m["exit"])
                if ret is not None:
                    res[m["id"]]["all"].append(ret)
                    res[m["id"]]["month"][dstr[:7]].append(ret)

    print("\n" + "=" * 96)
    print("历史战绩(5分钟真实可成交口径; 入场=触发bar收盘, 出场=5分钟盘中触及/破均线日收盘):")
    for m in models:
        d = res[m["id"]]
        print(f"\n■ {m['name']} ({m['id']})   {fmt_stat(stat(d['all']))}")
        if monthly and d["month"]:
            for ym in sorted(d["month"]):
                print(f"    {ym}   {fmt_stat(stat(d['month'][ym]))}")


if __name__ == "__main__":
    asyncio.run(run())
