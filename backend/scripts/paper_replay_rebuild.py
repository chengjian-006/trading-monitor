# -*- coding: utf-8 -*-
"""用 5 分钟 K 线重放重建模拟盘 (持仓/流水/资金曲线)。

默认 **试跑**: 只算不写, 打印重建后的账户长什么样, 与现状对比。
加 --apply 才真正落库(先删该账户的 position/trade/equity 三表旧数据, 再写重建结果, 单事务)。

    python backend/scripts/paper_replay_rebuild.py                    # 试跑, 两个账户
    python backend/scripts/paper_replay_rebuild.py --apply            # 落库
    python backend/scripts/paper_replay_rebuild.py --account default  # 只跑一个账户

落库前请确认已有备份: docs/backups/模拟盘数据备份-5分钟回放重建前-20260714.md
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import aiomysql

from backend.models.database import init_db, close_db, get_pool
from backend.models.repo._db import _fetchall
from backend.models.repo.paper_trading import ACCOUNT_KEYS
from backend.services import paper_replay

APPLY = "--apply" in sys.argv
ONLY = None
if "--account" in sys.argv:
    ONLY = sys.argv[sys.argv.index("--account") + 1]


async def _current(account_id: int) -> dict:
    pos = await _fetchall("SELECT * FROM cfzy_biz_paper_position WHERE account_id=%s", (account_id,))
    tr = await _fetchall(
        "SELECT side, status, COUNT(*) n FROM cfzy_biz_paper_trade WHERE account_id=%s "
        "GROUP BY side, status", (account_id,))
    return {"positions": len(pos), "trades": {f"{r['side']}/{r['status']}": r["n"] for r in tr}}


def _report(book, acct, before: dict) -> None:
    init = float(acct["initial_capital"])
    key = acct["account_key"]
    buys = [t for t in book.trades if t["side"] == "buy" and t["status"] == "success"]
    sells = [t for t in book.trades if t["side"] == "sell"]
    fails = [t for t in book.trades if t["status"] == "failed"]
    last = book.equity[-1] if book.equity else None

    print("\n" + "=" * 78)
    print(f"账户 {key} (本金 {init:,.0f})")
    print("=" * 78)
    print(f"  改动前: 持仓 {before['positions']} 只, 流水 {before['trades']}")
    print(f"  重建后: 持仓 {len(book.positions)} 只, 买入成交 {len(buys)} 笔, "
          f"卖出成交 {len(sells)} 笔, 失败留痕 {len(fails)} 笔")
    if last:
        print(f"  现金 {last['cash']:>12,.0f}   持仓市值 {last['holdings_mv']:>12,.0f}   "
              f"总资产 {last['total_equity']:>12,.0f}   收益率 {last['total_return_pct']:+.2f}%")

    if sells:
        wins = [t for t in sells if float(t["realized_pnl"] or 0) > 0]
        pnl = sum(float(t["realized_pnl"] or 0) for t in sells)
        gain = sum(float(t["realized_pnl"]) for t in sells if float(t["realized_pnl"] or 0) > 0)
        loss = -sum(float(t["realized_pnl"]) for t in sells if float(t["realized_pnl"] or 0) < 0)
        pf = (gain / loss) if loss > 0 else 99.0
        print(f"\n  已实现: {len(sells)} 笔, 胜率 {len(wins) / len(sells) * 100:.1f}%, "
              f"总盈亏 {pnl:+,.0f}, PF {pf:.2f}")
        print("\n  卖出明细(按出场原因):")
        by_reason: dict[str, list] = {}
        for t in sells:
            by_reason.setdefault(t["signal_id"], []).append(float(t["realized_pnl"] or 0))
        for sid, ps in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            w = sum(1 for p in ps if p > 0)
            print(f"    {sid:<20} {len(ps):>3} 笔  胜 {w:>2}  合计 {sum(ps):>+10,.0f}")

        print("\n  逐笔卖出:")
        for t in sorted(sells, key=lambda x: x["trade_time"]):
            print(f"    {t['trade_time']:%m-%d %H:%M} 卖 {t['name'][:6]:<6}({t['code']}) "
                  f"{t['qty']:>5}股 @{t['price']:>7.2f}  {t['signal_id']:<18} "
                  f"{float(t['realized_pnl'] or 0):>+9,.0f} ({float(t['realized_pnl_pct'] or 0):>+6.2f}%)")

    if fails:
        reasons: dict[str, int] = {}
        for t in fails:
            reasons[t["fail_reason"]] = reasons.get(t["fail_reason"], 0) + 1
        print(f"\n  买入失败原因分布: {reasons}")

    if book.positions:
        print("\n  重建后仍持仓:")
        for c, p in sorted(book.positions.items(), key=lambda kv: kv[1]["open_date"]):
            cps = float(p["cost_amount"]) / int(p["qty"])
            print(f"    {c} {p['name'][:6]:<6} {p['qty']:>5}股 成本 {cps:>7.2f} "
                  f"建仓 {p['open_date']} {p['entry_signal_id']}")


async def _persist(book, acct) -> None:
    aid, uid = acct["id"], acct["user_id"]
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.begin()
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM cfzy_biz_paper_trade WHERE account_id=%s", (aid,))
                await cur.execute("DELETE FROM cfzy_biz_paper_position WHERE account_id=%s", (aid,))
                await cur.execute("DELETE FROM cfzy_biz_paper_equity WHERE account_id=%s", (aid,))
                for t in book.trades:
                    await cur.execute(
                        "INSERT INTO cfzy_biz_paper_trade (account_id,user_id,code,name,side,qty,price,"
                        "amount,fee,cash_after,signal_id,signal_name,signal_direction,realized_pnl,"
                        "realized_pnl_pct,note,trade_date,trade_time,status,fail_reason) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (aid, uid, t["code"], t["name"], t["side"], t["qty"], t["price"], t["amount"],
                         t["fee"], t["cash_after"], t["signal_id"], t["signal_name"],
                         t["signal_direction"], t["realized_pnl"], t["realized_pnl_pct"],
                         t["note"], t["trade_date"], t["trade_time"], t["status"],
                         t["fail_reason"] or ""))   # fail_reason 是 NOT NULL
                for p in book.positions.values():
                    await cur.execute(
                        "INSERT INTO cfzy_biz_paper_position (account_id,user_id,code,name,qty,"
                        "cost_amount,open_date,entry_signal_id,entry_model_name) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (aid, uid, p["code"], p["name"], p["qty"], p["cost_amount"],
                         p["open_date"], p["entry_signal_id"], p["entry_model_name"]))
                for e in book.equity:
                    await cur.execute(
                        "INSERT INTO cfzy_biz_paper_equity (account_id,user_id,snap_date,cash,"
                        "holdings_mv,total_equity,total_return_pct,position_count) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (aid, uid, e["snap_date"], e["cash"], e["holdings_mv"], e["total_equity"],
                         e["total_return_pct"], e["position_count"]))
                await cur.execute(
                    "UPDATE cfzy_biz_paper_account SET cash=%s WHERE id=%s", (book.cash, aid))
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
    print(f"  ✅ 已落库: {len(book.trades)} 笔流水 / {len(book.positions)} 只持仓 / "
          f"{len(book.equity)} 个曲线点, 现金 {book.cash:,.2f}")


async def main():
    await init_db()
    try:
        from backend.models import repository
        user_config = await repository.get_signal_config(1)
        keys = [ONLY] if ONLY else list(ACCOUNT_KEYS)
        for key in keys:
            acct = await paper_replay.load_account(1, key)
            before = await _current(acct["id"])
            book = await paper_replay.replay(1, key, user_config=user_config)
            _report(book, acct, before)
            if APPLY:
                await _persist(book, acct)
        if not APPLY:
            print("\n" + "=" * 78)
            print("以上为【试跑】结果, 数据库未改动。确认无误后加 --apply 落库。")
    finally:
        await close_db()


asyncio.run(main())
