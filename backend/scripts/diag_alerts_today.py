"""列出指定两天触发的预警(信号)明细 — 只读诊断。
跑法: py -3 -m backend.scripts.diag_alerts_today [YYYY-MM-DD] [YYYY-MM-DD]
默认= 今天 + 昨天。
"""
import asyncio
import sys
from datetime import date, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backend.models.database import init_db
from backend.models.repo import _db


async def dump(d: str):
    rows = await _db._fetchall(
        "SELECT code, name, signal_id, signal_name, direction, price, "
        "triggered_at, user_id, eod_audit, eod_audit_note, "
        "outcome, outcome_p1_pct, outcome_p3_pct, outcome_p5_pct "
        "FROM cfzy_biz_signals WHERE trigger_date = %s "
        "ORDER BY user_id, triggered_at ASC",
        (d,),
    )
    print(f"\n===== {d}  共 {len(rows)} 条 =====")
    if not rows:
        print("(无)")
        return
    print(f"{'时间':<9}{'代码':<8}{'名称':<9}{'方向':<6}{'价格':<9}{'信号(预警)名称':<22}"
          f"{'用户':<4}{'EOD核':<8}{'1/3/5日收益%'}")
    print("-" * 110)
    for r in rows:
        t = str(r["triggered_at"])[11:19]
        name = (r["name"] or "").ljust(6)
        price = r["price"]
        pr = f"{price:.3f}" if price is not None else "--"
        p = lambda x: ("--" if x is None else f"{x:+.1f}")
        outc = f"{p(r['outcome_p1_pct'])}/{p(r['outcome_p3_pct'])}/{p(r['outcome_p5_pct'])}"
        audit = (r["eod_audit"] or "")
        print(f"{t:<9}{r['code']:<8}{name}{r['direction']:<6}{pr:<9}"
              f"{(r['signal_name'] or '')[:20]:<22}{str(r['user_id']):<4}{audit:<8}{outc}")


async def main():
    args = sys.argv[1:]
    if len(args) >= 2:
        days = [args[0], args[1]]
    else:
        today = date(2026, 6, 18)
        days = [today.isoformat(), (today - timedelta(days=1)).isoformat()]
    await init_db()
    for d in days:
        await dump(d)


if __name__ == "__main__":
    asyncio.run(main())
