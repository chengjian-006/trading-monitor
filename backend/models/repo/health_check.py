# -*- coding: utf-8 -*-
"""系统体检结果与推送心跳 CRUD (v1.7.698).

设计要点: 判定结果先落库、再推送。推送只是展示层, 推失败下轮可补报, 且留下历史。
这是对旧 system_health"进程内累积 + finally 无条件清空"的直接修正。
"""

from backend.models.repo._db import _execute, _executemany, _fetchall, _fetchone


async def save_results(run_at: str, results: list[dict]) -> int:
    """整轮体检结果落库(每项一行)。"""
    if not results:
        return 0
    rows = [(run_at, r["key"], r.get("category", ""), r.get("name", ""),
             r.get("severity", "warn"), 1 if r.get("ok") else 0,
             str(r.get("actual", ""))[:255], str(r.get("expected", ""))[:255],
             str(r.get("detail", ""))[:500]) for r in results]
    return await _executemany(
        "INSERT INTO cfzy_sys_health_check "
        "(run_at, check_key, category, name, severity, ok, actual, expected, detail) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE ok=VALUES(ok), actual=VALUES(actual), "
        "expected=VALUES(expected), detail=VALUES(detail)",
        rows)


async def latest_run() -> list[dict]:
    """最近一轮体检的全部结果。"""
    row = await _fetchone("SELECT MAX(run_at) AS t FROM cfzy_sys_health_check")
    if not row or not row.get("t"):
        return []
    return await _fetchall(
        "SELECT * FROM cfzy_sys_health_check WHERE run_at=%s ORDER BY severity, check_key",
        (row["t"],))


async def failing_streak(check_key: str, limit: int = 10) -> int:
    """某项最近连续失败了几轮(用于区分偶发抖动与持续劣化)。"""
    rows = await _fetchall(
        "SELECT ok FROM cfzy_sys_health_check WHERE check_key=%s "
        "ORDER BY run_at DESC LIMIT %s", (check_key, limit))
    n = 0
    for r in rows:
        if int(r["ok"]) == 1:
            break
        n += 1
    return n


async def prune(days: int = 60) -> None:
    """清理过期体检历史, 防表无限增长。"""
    await _execute(
        "DELETE FROM cfzy_sys_health_check WHERE run_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
        (days,))


# ── 推送心跳 ──

async def get_heartbeat() -> dict:
    row = await _fetchone("SELECT * FROM cfzy_sys_health_heartbeat WHERE id=1")
    return row or {"last_push_at": None, "last_fail_at": None, "fail_streak": 0}


async def mark_push(ok: bool) -> None:
    """记一次报告推送结果。成功清零连败计数, 失败累加。"""
    if ok:
        await _execute(
            "INSERT INTO cfzy_sys_health_heartbeat (id, last_push_at, fail_streak) "
            "VALUES (1, NOW(), 0) "
            "ON DUPLICATE KEY UPDATE last_push_at=NOW(), fail_streak=0")
    else:
        await _execute(
            "INSERT INTO cfzy_sys_health_heartbeat (id, last_fail_at, fail_streak) "
            "VALUES (1, NOW(), 1) "
            "ON DUPLICATE KEY UPDATE last_fail_at=NOW(), fail_streak=fail_streak+1")
