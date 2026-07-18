"""交易日记 CRUD - cfzy_biz_trade_journal 表 (v1.7.669).

手动记录每笔买卖的理由/心态/复盘, 事后回看自己的决策模式。与"交易分析"(从交割单来的客观数据)互补。
"""
import aiomysql

from backend.models.database import get_pool
from backend.models.repo._db import _execute, _fetchall


async def list_journal(user_id: int, limit: int = 200) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_trade_journal WHERE user_id=%s "
        "ORDER BY (trade_date IS NULL), trade_date DESC, id DESC LIMIT %s",
        (user_id, limit),
    )


async def create_journal(user_id: int, data: dict) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_trade_journal "
                "(user_id, code, name, side, trade_date, price, qty, reason, emotion, review) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (user_id, data.get("code", ""), data.get("name", ""), data.get("side", ""),
                 data.get("trade_date") or None, data.get("price"), data.get("qty"),
                 data.get("reason", ""), data.get("emotion", ""), data.get("review", "")),
            )
            return cur.lastrowid


async def update_journal(user_id: int, jid: int, data: dict) -> None:
    fields = ["code", "name", "side", "trade_date", "price", "qty", "reason", "emotion", "review"]
    sets, args = [], []
    for f in fields:
        if f in data:
            sets.append(f"{f}=%s")
            args.append(data[f] if data[f] != "" or f not in ("trade_date",) else None)
    if not sets:
        return
    args.extend([jid, user_id])
    await _execute(
        f"UPDATE cfzy_biz_trade_journal SET {', '.join(sets)} WHERE id=%s AND user_id=%s",
        tuple(args),
    )


async def delete_journal(user_id: int, jid: int) -> None:
    await _execute("DELETE FROM cfzy_biz_trade_journal WHERE id=%s AND user_id=%s", (jid, user_id))
