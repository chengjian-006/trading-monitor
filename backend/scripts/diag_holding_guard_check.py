"""诊断 持仓守护·接近前高 是否误报 — 只读。
拉指定票的 实时价 + 日K尾段 + prior_high 窗口(跳最近5根的60日波段高), 看触发是否合理。
跑法: py -3 -m backend.scripts.diag_holding_guard_check 000725 002463
"""
import asyncio
import sys

from backend.models.database import init_db, close_db
from backend import data_fetcher
from backend.services.holding_guard import prior_high, is_near_high, WINDOW_HIGH, SKIP_RECENT

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def main():
    codes = sys.argv[1:] or ["000725", "002463"]
    await init_db()
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
        for code in codes:
            q = quotes.get(code) or {}
            price = float(q.get("price") or 0)
            name = q.get("name") or code
            df = await data_fetcher.get_daily_kline(code, days=80)
            print("=" * 64)
            print(f"{name}({code})  实时价={price}  开={q.get('open')} 昨收={q.get('pre_close')} "
                  f"今高={q.get('high')} 今低={q.get('low')} 涨跌={q.get('pct_change')}%")
            if df is None or df.empty:
                print("  日K为空")
                continue
            print(f"  日K末8根(含今日形成中bar):")
            for _, r in df.tail(8).iterrows():
                print(f"    {str(r['date'])[:10]}  H={float(r['high']):.2f} "
                      f"L={float(r['low']):.2f} C={float(r['close']):.2f}")
            ph, ph_date = prior_high(df)
            # 对照: 不跳最近5根的真·近60日最高
            recent_win = df.tail(WINDOW_HIGH)
            true_hi = float(recent_win["high"].astype(float).max())
            true_hi_date = str(recent_win.loc[recent_win["high"].astype(float).idxmax(), "date"])[:10]
            print(f"  prior_high(跳最近{SKIP_RECENT}根)= {ph}({ph_date})  ← 推送用的阻力位")
            print(f"  真·近{WINDOW_HIGH}日最高(不跳)= {true_hi}({true_hi_date})")
            if ph:
                print(f"  现价距阻力 {price/ph-1:+.2%}  is_near_high={is_near_high(price, ph)} "
                      f"(触发带 [{ph*0.98:.2f}, {ph:.2f}])")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
