"""持仓守护节流状态落库 (v1.7.569) — cfzy_biz_guard_throttle.

holding_guard 的每股每规则每日计数原来纯进程内存, 盘中重启即清零 → 接近前高/盈利保护/涨停
这类"持续成立"的提醒会在重启后下一个 tick 重推一轮。落库后每个 tick 开头从 DB 恢复今日计数,
重启不再重推。表按 (trade_date, code, rule) 唯一, cnt 累加、last_ts 记末次(供急拉冷却)。
"""
from backend.models.repo._db import _fetchall, _execute


async def load_today(today: str) -> list[dict]:
    """今日全部节流计数 → [{code, rule, cnt, last_ts}]，供 tick 开头恢复内存态。"""
    return await _fetchall(
        "SELECT code, rule, cnt, last_ts FROM cfzy_biz_guard_throttle WHERE trade_date=%s",
        (today,))


async def bump(today: str, code: str, rule: str, last_ts: float | None) -> None:
    """某股某规则当日计数 +1(不存在则建), 记末次时间戳。与内存 mark 同步落库。"""
    await _execute(
        "INSERT INTO cfzy_biz_guard_throttle (trade_date, code, rule, cnt, last_ts) "
        "VALUES (%s, %s, %s, 1, %s) "
        "ON DUPLICATE KEY UPDATE cnt = cnt + 1, last_ts = VALUES(last_ts)",
        (today, code, rule, last_ts))


async def prune(before_days: int = 7) -> None:
    """清理 before_days 天前的历史行(每天首个 tick 调一次, 防表无限增长)。"""
    await _execute(
        "DELETE FROM cfzy_biz_guard_throttle WHERE trade_date < DATE_SUB(CURDATE(), INTERVAL %s DAY)",
        (before_days,))
