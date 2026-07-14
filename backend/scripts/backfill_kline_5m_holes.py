# -*- coding: utf-8 -*-
"""补 cfzy_sys_kline_5m 在回放窗口内的数据洞(baostock, 后复权, 幂等 upsert)。

背景: 5分钟表由「一次性回填(停在2026-06-18)」+「每晚20:00增量追加(v1.7.599起)」两段拼成,
中间 2026-06-19 整天没人管 → 真空洞; 尾部 07-13/07-14 也未追全。
每日追加任务 _plan_windows 是「按每票库内最后一天+1 往后追」, 结构上补不了中间的洞。

本脚本按显式日期窗口整段重拉指定票, upsert 覆盖, 天然补洞。
默认票池 = 模拟盘回放涉及的票(买点信号涉及的 code ∪ 模拟盘现有持仓 code), 不是全市场。

用法:
    python backend/scripts/backfill_kline_5m_holes.py                 # 默认: 回放票池, 06-01~07-14
    python backend/scripts/backfill_kline_5m_holes.py 2026-06-19 2026-07-14
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.models.database import init_db, close_db
from backend.models.repo._db import _executemany, _fetchall
from backend.services.kline_5m_appender import _UPSERT, _fetch_5m_sync, _to_bs

START = sys.argv[1] if len(sys.argv) > 1 else "2026-06-01"
END = sys.argv[2] if len(sys.argv) > 2 else "2026-07-14"


async def replay_codes() -> list[str]:
    """回放涉及的票: 买点信号(user1, 窗口内) ∪ 模拟盘现有持仓。"""
    sig = await _fetchall(
        "SELECT DISTINCT code FROM cfzy_biz_signals "
        "WHERE user_id=1 AND direction='buy' AND DATE(triggered_at) >= %s", (START,))
    pos = await _fetchall("SELECT DISTINCT code FROM cfzy_biz_paper_position")
    codes = {str(r["code"]) for r in sig} | {str(r["code"]) for r in pos}
    return sorted(codes)


async def main():
    import baostock as bs
    await init_db()
    try:
        codes = await replay_codes()
        print(f"票池 {len(codes)} 只, 窗口 {START} ~ {END}")

        before = await _fetchall(
            "SELECT DATE(dt) d, COUNT(DISTINCT code) c FROM cfzy_sys_kline_5m "
            "WHERE DATE(dt) BETWEEN %s AND %s AND code IN "
            f"({','.join(['%s'] * len(codes))}) GROUP BY DATE(dt) ORDER BY d",
            (START, END, *codes))
        print("补之前, 票池在窗口内的逐日覆盖:")
        for r in before:
            print(f"   {r['d']}  {r['c']:>4}/{len(codes)} 只")

        def _login():
            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError(f"baostock login 失败: {lg.error_msg}")

        await asyncio.to_thread(_login)
        ok = empty = fail = rows_total = 0
        try:
            for i, code in enumerate(codes, 1):
                bs_code = _to_bs(code)
                if not bs_code:
                    fail += 1
                    continue
                try:
                    rows = await asyncio.to_thread(_fetch_5m_sync, bs, bs_code, START, END)
                except Exception as e:
                    fail += 1
                    print(f"  [{i}/{len(codes)}] {code} 拉取失败: {e}")
                    continue
                if not rows:
                    empty += 1
                    continue
                await _executemany(_UPSERT, rows)
                ok += 1
                rows_total += len(rows)
                if i % 20 == 0 or i == len(codes):
                    print(f"  [{i}/{len(codes)}] 已写 {rows_total} 根 (ok={ok} 空={empty} 失败={fail})")
        finally:
            await asyncio.to_thread(bs.logout)

        after = await _fetchall(
            "SELECT DATE(dt) d, COUNT(DISTINCT code) c FROM cfzy_sys_kline_5m "
            "WHERE DATE(dt) BETWEEN %s AND %s AND code IN "
            f"({','.join(['%s'] * len(codes))}) GROUP BY DATE(dt) ORDER BY d",
            (START, END, *codes))
        print("\n补之后, 票池在窗口内的逐日覆盖:")
        for r in after:
            flag = "" if r["c"] >= len(codes) * 0.95 else "   <-- 仍不全"
            print(f"   {r['d']}  {r['c']:>4}/{len(codes)} 只{flag}")
        print(f"\n完成: {ok} 只写入, {empty} 只无数据, {fail} 只失败, 共 {rows_total} 根")
    finally:
        await close_db()


asyncio.run(main())
