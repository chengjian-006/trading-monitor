# -*- coding: utf-8 -*-
"""选股 —— 在指定交易日, 跑现有买点模型, 列出 5分钟真实可成交口径下触发的股票。

口径: 5分钟真实可成交(突破那一刻真实累计量/额过闸 + 真实价)。详见 _engine.py。
离线/复盘选股(基于历史库), 非盘中实时(实时选股是生产 app 信号引擎的活)。

环境变量(均可选):
  SEL_DATE=2026-06-18     选股交易日(默认: 库内最新交易日)
  SEL_UNIVERSE=pool:1     范围: all | pool:<用户id> | 代码逗号列表 (默认 pool:1=自选股)
  SEL_MODELS=BUY_RALLY_MA10,BUY_STRONG_START   只跑这些模型(默认全部6个)
  SEL_XLSX=1              额外导出 xlsx 到 skill 目录

运行: cd 项目根 && SEL_DATE=2026-06-18 py -3 -u .claude/skills/stock-screen-backtest/screen.py
"""
import asyncio
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _engine import (MODELS, MODEL_BY_ID, MIN_BARS, load_daily_one, load_5m_one,
                     universe_codes, fire_5m, entry_price, daily_could_fire)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from backend.models.database import init_db
from backend.models.repo._db import _fetchall


async def _latest_trade_date():
    r = await _fetchall("SELECT MAX(trade_date) d FROM cfzy_sys_kline_cache")
    return str(r[0]["d"]) if r and r[0]["d"] else None


async def _names(codes):
    if not codes:
        return {}
    qs = ",".join(["%s"] * len(codes))
    rows = await _fetchall(f"SELECT DISTINCT code,name FROM cfzy_biz_stock_pool WHERE code IN ({qs})", tuple(codes))
    return {str(r["code"]): r["name"] for r in rows}


async def _winrates():
    rows = await _fetchall("SELECT signal_id,win_rate_3m,n_3m FROM cfzy_biz_model_winrate")
    return {r["signal_id"]: (r["win_rate_3m"], r["n_3m"]) for r in rows}


async def run():
    await init_db()
    date = os.environ.get("SEL_DATE") or await _latest_trade_date()
    uni = os.environ.get("SEL_UNIVERSE", "pool:1")
    sel_models = os.environ.get("SEL_MODELS", "")
    models = [MODEL_BY_ID[mid] for mid in sel_models.split(",") if mid in MODEL_BY_ID] if sel_models else MODELS

    codes = await universe_codes(uni)
    wr = await _winrates()
    print(f"选股日={date}  范围={uni}({len(codes)}只)  模型={[m['id'] for m in models]}  口径=5分钟真实可成交", flush=True)

    hits = []  # (model_name, code, entry, reason)
    done = 0
    for code in codes:
        done += 1
        if done % 500 == 0:
            print(f"  ...{done}/{len(codes)}", flush=True)
        df = await load_daily_one(code)
        if df is None:
            continue
        # 定位选股日索引
        idx = df.index[df["date"].astype(str).str[:10] == date]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        if i < MIN_BARS:
            continue
        from backend.services.signal_engine_indicators import compute_indicators
        ind = compute_indicators(df)
        ind["amount_est"] = ind["volume"] * ind["close"]
        day5m = await load_5m_one(code)
        bars = day5m.get(date)
        if not bars:
            continue
        sub = ind.iloc[:i + 1]
        prev_close = float(ind["close"].iloc[i - 1])
        for m in models:
            if not daily_could_fire(m, sub, ind.iloc[i]):
                continue
            fired, _amt, reason = fire_5m(m, sub, ind.iloc[i].copy(), bars, prev_close)
            if fired:
                hits.append((m["name"], m["id"], code, entry_price(m, ind, i), reason))

    names = await _names(sorted(set(h[2] for h in hits)))
    print("\n" + "=" * 96)
    if not hits:
        print(f"{date} 无模型触发(5分钟可成交口径)。")
        return
    rows_out = []
    for mname in [m["name"] for m in models]:
        grp = [h for h in hits if h[0] == mname]
        if not grp:
            continue
        w = wr.get(grp[0][1], (None, None))
        wtag = f"近3月胜率{w[0]:.0f}%(n={w[1]})" if w[0] is not None else "战绩未知"
        print(f"\n■ {mname}  [{wtag}]  触发 {len(grp)} 只")
        for _, mid, code, entry, reason in grp:
            nm = names.get(code, "")
            print(f"   {code} {nm:<6} 入场≈{entry:.2f}  | {reason[:80]}")
            rows_out.append({"模型": mname, "代码": code, "名称": nm, "入场价": round(entry, 2), "理由": reason})
    print(f"\n合计触发 {len(hits)} 条。")

    if os.environ.get("SEL_XLSX") and rows_out:
        out = os.path.join(os.path.dirname(__file__), f"select_{date}.xlsx")
        pd.DataFrame(rows_out).to_excel(out, index=False)
        print(f"已导出 {out}")


if __name__ == "__main__":
    asyncio.run(run())
