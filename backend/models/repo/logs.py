"""操作日志 CRUD + 查询 / 清理 - cfzy_biz_operation_logs 表."""
from backend.models.repo._db import _execute, _fetchall, _fetchone


async def add_log(user_id: int, username: str, action: str, target: str = "",
                  old_value: dict | None = None, new_value: dict | None = None):
    import json
    await _execute(
        "INSERT INTO cfzy_biz_operation_logs (user_id, username, action, target, old_value, new_value) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, username, action, target,
         json.dumps(old_value, ensure_ascii=False) if old_value else None,
         json.dumps(new_value, ensure_ascii=False) if new_value else None),
    )


def _build_log_where(user_id: int | None, action: str | None = None,
                     keyword: str | None = None, date_from: str | None = None,
                     date_to: str | None = None) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if user_id:
        clauses.append("user_id = %s")
        params.append(user_id)
    if action:
        clauses.append("action = %s")
        params.append(action)
    if keyword:
        like = f"%{keyword}%"
        clauses.append("(username LIKE %s OR target LIKE %s OR action LIKE %s)")
        params.extend([like, like, like])
    if date_from:
        clauses.append("created_at >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("created_at < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(date_to)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


async def get_logs(user_id: int | None = None, limit: int = 200, offset: int = 0,
                   action: str | None = None, keyword: str | None = None,
                   date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    where, params = _build_log_where(user_id, action, keyword, date_from, date_to)
    params.extend([limit, offset])
    return await _fetchall(
        f"SELECT * FROM cfzy_biz_operation_logs{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        tuple(params),
    )


async def count_logs(user_id: int | None = None, action: str | None = None,
                     keyword: str | None = None, date_from: str | None = None,
                     date_to: str | None = None) -> int:
    where, params = _build_log_where(user_id, action, keyword, date_from, date_to)
    row = await _fetchone(
        f"SELECT COUNT(*) AS cnt FROM cfzy_biz_operation_logs{where}",
        tuple(params) if params else None,
    )
    return row["cnt"] if row else 0


async def get_log_actions() -> list[str]:
    rows = await _fetchall("SELECT DISTINCT action FROM cfzy_biz_operation_logs ORDER BY action")
    return [r["action"] for r in rows]


async def purge_old_logs(months: int = 3) -> int:
    result = await _execute(
        "DELETE FROM cfzy_biz_operation_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL %s MONTH)",
        (months,),
    )
    return result


async def purge_old_logs_days(days: int = 30) -> int:
    """删除 N 天前的操作日志, 返回删除条数(_execute 不回行数, 故先 COUNT)。给每日日志清理任务用。"""
    row = await _fetchone(
        "SELECT COUNT(*) AS cnt FROM cfzy_biz_operation_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
        (days,),
    )
    cnt = row["cnt"] if row else 0
    if cnt:
        await _execute(
            "DELETE FROM cfzy_biz_operation_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
            (days,),
        )
    return cnt
