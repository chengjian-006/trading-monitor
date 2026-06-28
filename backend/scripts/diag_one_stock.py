# -*- coding: utf-8 -*-
"""单股票买点诊断 — 为什么今天没触发买点模型 (只读, 真连库真跑).

跑法:
    set PYTHONPATH=<项目根> && set PYTHONIOENCODING=utf-8
    py -3 -m backend.scripts.diag_one_stock 605358
"""
import asyncio
import sys

import aiomysql
import numpy as np
import pandas as pd

from backend.core.config import load_config
from backend.models import database
from backend.fetcher.klines import get_daily_kline
from backend.fetcher.quotes import get_realtime_quotes
from backend.services.signal_engine import _merge_realtime_bar
from backend.services.signal_engine_config import get_merged_config
from backend.services.signal_engine_indicators import compute_indicators
from backend.services.signal_engine_detectors import (
    _detect_s0_weak_extreme,
    _detect_strong_start_right,
    _detect_rally_ma20_pullback,
    _detect_vol_breakout,
    _detect_platform_breakout,
    _detect_auction_strength,
)
from backend.services.trading_concepts import detect_main_rally
from backend.services.intraday_estimator import is_intraday


async def _ensure_pool():
    cfg = load_config().get("database", {})
    database._pool = await aiomysql.create_pool(
        host=cfg.get("host", "127.0.0.1"), port=cfg.get("port", 3306),
        user=cfg.get("user", "root"), password=cfg.get("password", ""),
        db=cfg.get("db", "trading"), charset="utf8mb4",
        autocommit=True, minsize=1, maxsize=4,
    )
    return cfg


def P(ok, label, detail=""):
    mark = "OK  " if ok else "FAIL"
    print(f"   [{mark}] {label}  {detail}")


async def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "605358"
    cfg = await _ensure_pool()
    print(f"[diag] 连库 {cfg.get('host')}/{cfg.get('db')}  code={code}  is_intraday={is_intraday()}")

    try:
        df = await get_daily_kline(code, 150)
        if df is None or len(df) < 20:
            print(f"K线不足: len={0 if df is None else len(df)}")
            return
        rt_all = await get_realtime_quotes([code])
        rt = rt_all.get(code)
        print(f"[diag] K线根数={len(df)}  末根日期={str(df.iloc[-1]['date'])[:10]}  实时={rt}")

        c = get_merged_config(None)
        d = compute_indicators(df, c)
        if rt and rt.get("price", 0) > 0:
            d = _merge_realtime_bar(d, rt)
            d = compute_indicators(d, c)
        latest = d.iloc[-1]
        prev = d.iloc[-2]

        close = float(latest["close"]); ma5 = float(latest["ma5"])
        ma10 = float(latest["ma10"]); ma20 = float(latest["ma20"]); ma60 = float(latest["ma60"])
        vol = float(latest["volume"]); pct = float(latest.get("pct_change", 0) or 0)
        amt_est = float(latest.get("amount_est", 0) or 0)

        print("=" * 78)
        print(f"现价 {close:.2f}  涨幅 {pct*100:+.2f}%")
        print(f"MA5 {ma5:.2f}({(close-ma5)/ma5*100:+.2f}%)  MA10 {ma10:.2f}({(close-ma10)/ma10*100:+.2f}%)  "
              f"MA20 {ma20:.2f}({(close-ma20)/ma20*100:+.2f}%)  MA60 {ma60:.2f}({(close-ma60)/ma60*100:+.2f}%)")
        win = int(c["BUY_WEAK_EXTREME"].get("vol_floor_window", 10))
        vols = d.tail(win)["volume"]
        print(f"今量 {vol:.0f}  近{win}日最低 {vols.min():.0f}(今={vol/vols.min():.2f}x)  "
              f"近{win}日均 {vols.mean():.0f}(今={vol/vols.mean():.2f}x)")
        print(f"预估全天成交额 {amt_est/1e8:.2f}亿")
        rally = detect_main_rally(d)
        bsp = (len(d)-1-rally.peak_idx) if rally.peak_idx is not None else None
        print(f"主升浪: ever_qualified={rally.peak_idx is not None and rally.ever_qualified}  "
              f"峰值涨幅={getattr(rally,'peak_gain_pct',None)}  距峰={bsp}日")
        print("=" * 78)

        print("[1] BUY_WEAK_EXTREME 弱势极限(左侧):")
        r = _detect_s0_weak_extreme(d, latest, c["BUY_WEAK_EXTREME"])
        print(f"   => {r if r else '未触发'}")

        print("[2] BUY_STRONG_START 强势起点(右侧):")
        r = _detect_strong_start_right(d, latest, c["BUY_STRONG_START"], c["BUY_WEAK_EXTREME"])
        print(f"   => {r if r else '未触发'}")

        print("[3] BUY_RALLY_MA20 / MA10 回踩缩量突破昨高:")
        r20 = _detect_rally_ma20_pullback(d, latest, c["BUY_RALLY_MA20"])
        r10 = _detect_rally_ma20_pullback(d, latest, c["BUY_RALLY_MA10"])
        print(f"   MA20 => {r20 if r20 else '未触发'}")
        print(f"   MA10 => {r10 if r10 else '未触发'}")

        print("[4] BUY_VOL_BREAKOUT 缩量后放量突破:")
        r = _detect_vol_breakout(d, latest, c["BUY_VOL_BREAKOUT"])
        print(f"   => {r if r else '未触发'}")

        print("[5] BUY_PLATFORM_BREAKOUT 中继平台突破:")
        r = _detect_platform_breakout(d, latest, c["BUY_PLATFORM_BREAKOUT"])
        print(f"   => {r if r else '未触发'}")

        print("[6] BUY_AUCTION_STRENGTH 竞价高开弱转强:")
        r = _detect_auction_strength(d, latest, c["BUY_AUCTION_STRENGTH"])
        print(f"   => {r if r else '未触发'}")

        # 昨日 setup 关键数字(回踩/缩量类用)
        print("=" * 78)
        ph = float(prev["high"]); pc = float(prev["close"]); pv = float(prev["volume"])
        pm10 = float(prev["ma10"]); pm20 = float(prev["ma20"])
        avg10_prev = float(d["volume"].iloc[-11:-1].mean())
        print(f"昨日: 收{pc:.2f} 高{ph:.2f} 量{pv:.0f}  距MA10 {(pc-pm10)/pm10*100:+.2f}%  "
              f"距MA20 {(pc-pm20)/pm20*100:+.2f}%  昨量/近10均 {pv/avg10_prev:.2f}")
        print(f"今最高 {float(latest['high']):.2f}  突破昨高需 > {ph*1.025:.2f}(MA口径2.5%) / {ph*1.02:.2f}(放量口径2%)")
    finally:
        await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
