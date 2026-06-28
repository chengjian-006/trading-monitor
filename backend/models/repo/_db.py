"""数据库底层 helper - v1.7.x.

所有 sub-module 从此处取 _fetchall / _fetchone / _execute / _executemany,
保证连接池获取方式统一; 也方便后续切其他 driver/pool 时单点修改.
"""
import aiomysql

from backend.models.database import get_pool


async def _fetchall(sql: str, args=None) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


async def _fetchone(sql: str, args=None) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()


async def _execute(sql: str, args=None):
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)


async def _executemany(sql: str, args_list: list) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(sql, args_list)
            return cur.rowcount
