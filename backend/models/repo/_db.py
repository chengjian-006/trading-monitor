"""数据库底层 helper - v1.7.x.

所有 sub-module 从此处取 _fetchall / _fetchone / _execute / _executemany,
保证连接池获取方式统一; 也方便后续切其他 driver/pool 时单点修改.

连接类错误自动重试一次 (v1.7.693)
─────────────────────────────────
起因: 持仓态前向分布(全市场五年扫描)跑了约21分钟, handler 明明已算完并落库,
却在 wrapped_handler 写 "success" 状态那一步挂掉:
    (2013, 'Lost connection to MySQL server during query ([Errno 104] ...)')
结果任务被记成 error 并累计 consecutive_failures —— "做完了却记成失败"。

为什么 pool_recycle=3600 挡不住: 它只在**借出连接时**检查年龄。本次任务远不到 1 小时,
问题出在连接借出后长时间不发包, 被跨云 RDS 主动 reset(Connection reset by peer)。
借出前是好的、用的时候才发现是死的 —— 回收策略天然管不到。

修法: 在本层(既有的单点收口处)对**连接类**错误重试一次, 重试前把坏连接 close 掉,
让池在 release 时丢弃而非回收复用。只认连接类错误码, 语法错误/约束冲突照常抛,
不掩盖真失败。所有长任务(模型胜率重算/5分钟K线追加/全市场回测/信号回填)同受保护。
"""
import asyncio

import aiomysql
import pymysql

from backend.models.database import get_pool

# 连接已断的 MySQL 错误码: 2006 server has gone away / 2013 lost connection during query
# / 2055 lost connection to server (读超时)
_CONN_ERRNOS = frozenset({2006, 2013, 2055})
_RETRY_DELAY = 0.2


def _is_conn_error(exc: BaseException) -> bool:
    """是否为"连接断了"这类可重试错误(区别于 SQL 本身写错、约束冲突等真失败)。"""
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError)):
        return True
    if isinstance(exc, pymysql.err.InterfaceError):
        return True          # 连接已关闭时 pymysql 抛这个
    if isinstance(exc, pymysql.err.OperationalError):
        code = exc.args[0] if exc.args else None
        return code in _CONN_ERRNOS
    return False


async def _run(fn, retries: int = 1):
    """借连接执行 fn(conn); 连接类错误则丢弃坏连接、重取一条再试(默认多试 1 次)。"""
    attempt = 0
    while True:
        pool = get_pool()
        try:
            async with pool.acquire() as conn:
                try:
                    return await fn(conn)
                except Exception as e:
                    if _is_conn_error(e):
                        try:
                            conn.close()    # 标记坏连接: release 时被池丢弃, 不回收复用
                        except Exception:
                            pass
                    raise
        except Exception as e:
            if attempt >= retries or not _is_conn_error(e):
                raise
            attempt += 1
            await asyncio.sleep(_RETRY_DELAY)


async def _fetchall(sql: str, args=None) -> list[dict]:
    async def _op(conn):
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()
    return await _run(_op)


async def _fetchone(sql: str, args=None) -> dict | None:
    async def _op(conn):
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()
    return await _run(_op)


async def _execute(sql: str, args=None):
    async def _op(conn):
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
    return await _run(_op)


async def _executemany(sql: str, args_list: list) -> int:
    async def _op(conn):
        async with conn.cursor() as cur:
            await cur.executemany(sql, args_list)
            return cur.rowcount
    return await _run(_op)
