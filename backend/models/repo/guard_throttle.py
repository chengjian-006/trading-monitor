"""持仓守护节流状态落库 (v1.7.569) — cfzy_biz_guard_throttle.

holding_guard 的每股每规则每日计数原来纯进程内存, 盘中重启即清零 → 接近前高/盈利保护/涨停
这类"持续成立"的提醒会在重启后下一个 tick 重推一轮。落库后每个 tick 开头从 DB 恢复今日计数,
重启不再重推。表按 (trade_date, code, rule) 唯一, cnt 累加、last_ts 记末次(供急拉冷却)。

v1.7.642 起同表复用为"解除通知"跨日标记(stop_escalation: 曾升级/站回检查点/已解除),
是通用 (trade_date, code, rule) 计数设计, holding_guard 的 load 只读自己的 rule 键不受影响;
prune(7天) 对这些标记同样适用(升级红卡活跃期间每日续写, 解除后标记随灰卡当轮闭环, 7天足够)。
"""
from backend.models.repo._db import _fetchall, _fetchone, _execute


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


async def last_date(code: str, rule: str) -> str | None:
    """该股该规则最近一次记录的 trade_date(跨日查询; 解除通知判'曾升级/是否已解除'用)。"""
    row = await _fetchone(
        "SELECT MAX(trade_date) AS d FROM cfzy_biz_guard_throttle "
        "WHERE code=%s AND rule=%s", (code, rule))
    return str(row["d"])[:10] if row and row.get("d") else None


async def total_cnt(code: str, rule: str) -> int:
    """该股该规则跨日累计计数(解除通知'连续站回检查点'用; clear 后归零)。"""
    row = await _fetchone(
        "SELECT COALESCE(SUM(cnt), 0) AS n FROM cfzy_biz_guard_throttle "
        "WHERE code=%s AND rule=%s", (code, rule))
    return int(row["n"]) if row else 0


async def clear(code: str, rule: str) -> None:
    """删除该股该规则全部记录(站回连续确认断链重置用)。"""
    await _execute(
        "DELETE FROM cfzy_biz_guard_throttle WHERE code=%s AND rule=%s", (code, rule))


async def recent_rule_codes(rule: str, days: int = 7) -> list[str]:
    """最近 N 天有该规则记录的股票代码(解除通知扫'曾升级但持仓已消失'用)。"""
    rows = await _fetchall(
        "SELECT DISTINCT code FROM cfzy_biz_guard_throttle "
        "WHERE rule=%s AND trade_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)",
        (rule, days))
    return [str(r["code"]) for r in rows]


async def prune(before_days: int = 7) -> None:
    """清理 before_days 天前的历史行(每天首个 tick 调一次, 防表无限增长)。"""
    await _execute(
        "DELETE FROM cfzy_biz_guard_throttle WHERE trade_date < DATE_SUB(CURDATE(), INTERVAL %s DAY)",
        (before_days,))
