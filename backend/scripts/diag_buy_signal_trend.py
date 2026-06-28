"""统计最近N天每天的买点信号数量(按 signal_id 前缀分类) — 只读诊断。
跑法: python -m backend.scripts.diag_buy_signal_trend [天数]
"""
import asyncio
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backend.models.database import init_db
from backend.models.repo import _db


async def main():
    await init_db()
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    # 每天买点总数
    rows = await _db._fetchall(
        "SELECT trigger_date, signal_id, COUNT(*) AS n "
        "FROM cfzy_biz_signals "
        "WHERE direction = 'BUY' "
        "AND trigger_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY) "
        "GROUP BY trigger_date, signal_id "
        "ORDER BY trigger_date ASC",
        (days,),
    )
    by_day = defaultdict(int)
    by_day_model = defaultdict(lambda: defaultdict(int))
    for r in rows:
        d = str(r["trigger_date"])
        by_day[d] += r["n"]
        by_day_model[d][r["signal_id"] or "?"] += r["n"]

    print(f"\n===== 最近{days}天 每日买点信号数 =====")
    print(f"{'日期':<12}{'买点总数':<8}  明细")
    print("-" * 80)
    for d in sorted(by_day):
        detail = "  ".join(f"{k.replace('BUY_','')}:{v}" for k, v in sorted(by_day_model[d].items(), key=lambda x: -x[1]))
        print(f"{d:<12}{by_day[d]:<8}  {detail}")


if __name__ == "__main__":
    asyncio.run(main())
