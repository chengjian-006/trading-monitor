"""交割单 CRUD + 持仓同步 - cfzy_biz_trades 表 + cfzy_biz_stock_pool 联动."""
from backend.models.database import get_pool
from backend.models.repo._db import _fetchall, _fetchone


async def has_import_today(user_id: int) -> bool:
    """用户今天(按 imported_at)是否上传过交割单。"""
    row = await _fetchone(
        "SELECT 1 AS x FROM cfzy_biz_trades WHERE user_id = %s AND DATE(imported_at) = CURDATE() LIMIT 1",
        (user_id,),
    )
    return row is not None


async def get_latest_import_time(user_id: int):
    """用户最近一次上传交割单的时间(无则 None)。"""
    row = await _fetchone(
        "SELECT MAX(imported_at) AS t FROM cfzy_biz_trades WHERE user_id = %s",
        (user_id,),
    )
    return row.get("t") if row else None


async def delete_trades_on_date(user_id: int, trade_date) -> int:
    """删除用户某交易日的全部成交记录, 返回删除行数。

    历史成交「替换该日」导入用: 先清该日再写入这批, 防与交割单同日双重计数。
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM cfzy_biz_trades WHERE user_id = %s AND trade_date = %s",
                (user_id, trade_date),
            )
            return cur.rowcount


async def save_trade_records(user_id: int, records: list[dict]) -> int:
    """增量保存交割记录, 重复数据自动跳过. 返回新增条数."""
    if not records:
        return 0
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT IGNORE INTO cfzy_biz_trades "
                "(user_id, trade_date, trade_time, code, name, direction, quantity, price, amount, fee, stamp_tax, transfer_fee, net_amount) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(user_id, r["trade_date"], r["trade_time"], r["code"], r["name"],
                  r["direction"], r["quantity"], r["price"], r["amount"],
                  r["fee"], r["stamp_tax"], r["transfer_fee"], r["net_amount"])
                 for r in records],
            )
            return cur.rowcount


async def get_all_trade_records(user_id: int) -> list[dict]:
    """获取用户全量交割记录 (用于分析)."""
    return await _fetchall(
        "SELECT trade_date, trade_time, code, name, direction, quantity, price, amount, "
        "fee, stamp_tax, transfer_fee, net_amount FROM cfzy_biz_trades "
        "WHERE user_id = %s ORDER BY trade_date, trade_time",
        (user_id,),
    )


async def sync_positions_from_trades(user_id: int, holdings: dict):
    """根据交割单分析结果同步持仓状态到股票池.
    holdings: {code: {"name": str, "quantity": int}}
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for code, info in holdings.items():
                await cur.execute(
                    "INSERT INTO cfzy_biz_stock_pool (code, user_id, name, status, hold_source) "
                    "VALUES (%s, %s, %s, 'hold', 'trade') "
                    "ON DUPLICATE KEY UPDATE status='hold', hold_source='trade', deleted_at=NULL",
                    (code, user_id, info["name"]),
                )
            if holdings:
                placeholders = ",".join(["%s"] * len(holdings))
                params = [user_id] + list(holdings.keys())
                await cur.execute(
                    f"UPDATE cfzy_biz_stock_pool SET status='watch', hold_source='' "
                    f"WHERE user_id = %s AND status = 'hold' AND hold_source = 'trade' AND code NOT IN ({placeholders})",
                    params,
                )
            else:
                await cur.execute(
                    "UPDATE cfzy_biz_stock_pool SET status='watch', hold_source='' "
                    "WHERE user_id = %s AND status = 'hold' AND hold_source = 'trade'",
                    (user_id,),
                )
