"""收盘复盘「今日信号 买X/卖Y（共N）」明细 — 同 review_summary 口径(只读)。
口径: cfzy_biz_signals 中 trigger_date=指定日 且 user_id=1; direction in (sell,reduce)=卖, 其余=买。
跑法: py -3 -m backend.scripts.diag_review_signals_today [YYYY-MM-DD]   默认=今天。
"""
import asyncio
import sys
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backend.models.database import init_db
from backend.models.repo import _db

UID = 1


async def dump(d: str):
    rows = await _db._fetchall(
        "SELECT code, name, signal_id, signal_name, direction, price, triggered_at, eod_audit "
        "FROM cfzy_biz_signals WHERE trigger_date = %s AND user_id = %s "
        "ORDER BY triggered_at ASC",
        (d, UID),
    )
    is_sell = lambda r: str(r.get("direction") or "").lower() in ("sell", "reduce")
    buys = [r for r in rows if not is_sell(r)]
    sells = [r for r in rows if is_sell(r)]
    print(f"\n===== {d}  user_id={UID}  买 {len(buys)} / 卖 {len(sells)}（共 {len(rows)}）=====")

    def show(title, rs):
        print(f"\n-- {title}（{len(rs)}）" + " " + "-" * 70)
        if not rs:
            print("(无)")
            return
        print(f"{'时间':<9}{'代码':<8}{'名称':<10}{'方向':<7}{'价格':<9}{'信号名称':<24}{'EOD核'}")
        for r in rs:
            t = str(r["triggered_at"])[11:19]
            name = (r["name"] or "")
            price = r["price"]
            pr = f"{price:.3f}" if price is not None else "--"
            print(f"{t:<9}{r['code']:<8}{name:<10}{(r['direction'] or ''):<7}{pr:<9}"
                  f"{(r['signal_name'] or '')[:22]:<24}{(r['eod_audit'] or '')}")

    show("买点", buys)
    show("卖点", sells)

    # 按股票看是否同票多条刷量
    from collections import Counter
    c = Counter((r["code"], r["name"]) for r in rows)
    multi = [(k, n) for k, n in c.items() if n > 1]
    if multi:
        print("\n-- 同股多条（疑似一只票刷多个信号）" + " " + "-" * 40)
        for (code, name), n in sorted(multi, key=lambda x: -x[1]):
            print(f"  {code} {name}: {n} 条")


async def main():
    d = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    await init_db()
    await dump(d)


if __name__ == "__main__":
    asyncio.run(main())
