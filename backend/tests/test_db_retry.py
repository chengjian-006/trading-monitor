# -*- coding: utf-8 -*-
"""DB 连接类错误自动重试 (v1.7.693) — 用假连接池, 不连真库.

背景: 长任务(全市场扫描 21 分钟)算完并落库后, 写 "success" 状态时连接已被跨云 RDS
reset, 任务被记成 error —— "做完了却记成失败"。pool_recycle 只查借出时的年龄, 挡不住
"借出后被对端掐断"。故在 _db 层对连接类错误重试一次。
"""
import asyncio

import pymysql
import pytest

from backend.models.repo import _db


# ── 错误分类 ──

@pytest.mark.parametrize("exc", [
    ConnectionResetError(104, "Connection reset by peer"),
    BrokenPipeError(),
    pymysql.err.InterfaceError("connection closed"),
    pymysql.err.OperationalError(2006, "MySQL server has gone away"),
    pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query"),
    pymysql.err.OperationalError(2055, "Lost connection to server"),
])
def test_connection_errors_are_retryable(exc):
    assert _db._is_conn_error(exc) is True


@pytest.mark.parametrize("exc", [
    pymysql.err.ProgrammingError(1064, "You have an error in your SQL syntax"),
    pymysql.err.IntegrityError(1062, "Duplicate entry"),
    pymysql.err.OperationalError(1054, "Unknown column 'x' in 'field list'"),
    ValueError("nope"),
])
def test_real_failures_are_not_retried(exc):
    """SQL 写错/约束冲突/未知列不能重试 —— 重试只会掩盖真问题。"""
    assert _db._is_conn_error(exc) is False


# ── 重试行为 ──

class _FakeConn:
    def __init__(self):
        self.closed_called = False

    def close(self):
        self.closed_called = True


class _FakePool:
    """最小可用的 pool.acquire() 异步上下文管理器替身。"""

    def __init__(self):
        self.conns: list[_FakeConn] = []

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self_inner):
                c = _FakeConn()
                pool.conns.append(c)
                return c

            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_retries_once_then_succeeds(monkeypatch):
    """首次连接断开 → 丢弃坏连接、重取一条 → 第二次成功。"""
    pool = _FakePool()
    monkeypatch.setattr(_db, "get_pool", lambda: pool)
    monkeypatch.setattr(_db, "_RETRY_DELAY", 0)
    calls = []

    async def op(conn):
        calls.append(conn)
        if len(calls) == 1:
            raise pymysql.err.OperationalError(2013, "Lost connection")
        return "ok"

    assert _run(_db._run(op)) == "ok"
    assert len(calls) == 2, "应当重试一次"
    assert calls[0] is not calls[1], "重试必须换一条连接"
    assert pool.conns[0].closed_called, "坏连接要 close 掉, 否则会被池回收复用"
    assert not pool.conns[1].closed_called


def test_gives_up_after_retry_budget(monkeypatch):
    """一直断则最终抛出, 不无限重试。"""
    pool = _FakePool()
    monkeypatch.setattr(_db, "get_pool", lambda: pool)
    monkeypatch.setattr(_db, "_RETRY_DELAY", 0)
    n = []

    async def op(conn):
        n.append(1)
        raise pymysql.err.OperationalError(2006, "gone away")

    with pytest.raises(pymysql.err.OperationalError):
        _run(_db._run(op))
    assert len(n) == 2, "默认 retries=1 → 共尝试 2 次"


def test_non_conn_error_fails_fast(monkeypatch):
    """语法错误立刻抛, 不重试、也不 close 连接。"""
    pool = _FakePool()
    monkeypatch.setattr(_db, "get_pool", lambda: pool)
    n = []

    async def op(conn):
        n.append(1)
        raise pymysql.err.ProgrammingError(1064, "syntax error")

    with pytest.raises(pymysql.err.ProgrammingError):
        _run(_db._run(op))
    assert len(n) == 1, "真失败不该重试"
    assert not pool.conns[0].closed_called


def test_success_path_does_not_close_connection(monkeypatch):
    """正常路径不能误伤: 连接要还给池复用, 不能被 close。"""
    pool = _FakePool()
    monkeypatch.setattr(_db, "get_pool", lambda: pool)

    async def op(conn):
        return 42

    assert _run(_db._run(op)) == 42
    assert len(pool.conns) == 1 and not pool.conns[0].closed_called
