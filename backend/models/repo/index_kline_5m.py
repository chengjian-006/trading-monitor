# -*- coding: utf-8 -*-
"""指数 5 分钟 K 线 CRUD — cfzy_sys_index_kline_5m (v1.7.692).

code 一律带市场前缀(sh000001/sz399001/sz399006), 见表注释与 fetcher/index_klines.py。
"""

from backend.models.database import get_pool
from backend.models.repo._db import _fetchall, _fetchone


async def upsert_index_bars(code: str, bars: list[dict]) -> int:
    """整批 upsert 指数 5 分钟 K 线, 返回写入根数(幂等: 同 (code,dt) 覆盖)。"""
    if not bars:
        return 0
    rows = [(code, b["dt"], b["open"], b["high"], b["low"], b["close"], b["volume"])
            for b in bars]
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO cfzy_sys_index_kline_5m "
                "(code, dt, open, high, low, close, volume) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE open=VALUES(open), high=VALUES(high), "
                "low=VALUES(low), close=VALUES(close), volume=VALUES(volume)",
                rows,
            )
        await conn.commit()
    return len(rows)


async def get_index_bars(code: str, start: str | None = None,
                         end: str | None = None, limit: int = 5000) -> list[dict]:
    """取某指数的 5 分钟 K 线(升序)。start/end 为 'YYYY-MM-DD' 或完整 datetime 字符串。"""
    sql = "SELECT code, dt, open, high, low, close, volume FROM cfzy_sys_index_kline_5m WHERE code=%s"
    args: list = [code]
    if start:
        sql += " AND dt >= %s"
        args.append(start)
    if end:
        sql += " AND dt <= %s"
        args.append(end)
    sql += " ORDER BY dt ASC LIMIT %s"
    args.append(int(limit))
    return await _fetchall(sql, tuple(args))


async def index_kline_coverage() -> list[dict]:
    """各指数的覆盖情况(根数/起止), 给健康自检与前端展示用。"""
    return await _fetchall(
        "SELECT code, COUNT(*) AS bars, MIN(dt) AS first_dt, MAX(dt) AS last_dt "
        "FROM cfzy_sys_index_kline_5m GROUP BY code ORDER BY code"
    )


async def latest_index_dt(code: str) -> str | None:
    """某指数已存的最新 bar 时刻; 无数据返回 None。"""
    row = await _fetchone(
        "SELECT MAX(dt) AS d FROM cfzy_sys_index_kline_5m WHERE code=%s", (code,))
    d = row.get("d") if row else None
    return str(d) if d else None
