"""用户自定义问财选股语句 CRUD - cfzy_biz_wencai_query 表 (v1.7.546)。

每用户可增删改自己的常驻榜语句; scan_wencai 定时跑 enabled 的语句, 结果存进
cfzy_sys_wencai_pool(strategy_id=u{user_id}_q{id}, user_id=该用户)。
"""
from backend.models.repo._db import _execute, _fetchall, _fetchone


def pool_strategy_id(user_id: int, query_id: int) -> str:
    """用户自定义语句 → 候选榜行的全局唯一 strategy_id。"""
    return f"u{user_id}_q{query_id}"


async def list_user_queries(user_id: int) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_wencai_query WHERE user_id=%s ORDER BY sort_order, id",
        (user_id,),
    )


async def get_query(query_id: int, user_id: int) -> dict | None:
    return await _fetchone(
        "SELECT * FROM cfzy_biz_wencai_query WHERE id=%s AND user_id=%s",
        (query_id, user_id),
    )


async def add_query(user_id: int, name: str, query_text: str,
                    enabled: int = 1, sort_order: int = 0) -> int:
    """新增一条语句, 返回新 id。"""
    from backend.models.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_wencai_query (user_id, name, query_text, enabled, sort_order) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, name, query_text, enabled, sort_order),
            )
            return cur.lastrowid


async def update_query(query_id: int, user_id: int, **fields) -> None:
    """改 name/query_text/enabled/sort_order (仅本人)。"""
    allowed = {"name", "query_text", "enabled", "sort_order"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=%s")
            args.append(v)
    if not sets:
        return
    args += [query_id, user_id]
    await _execute(
        f"UPDATE cfzy_biz_wencai_query SET {', '.join(sets)} WHERE id=%s AND user_id=%s",
        tuple(args),
    )


async def delete_query(query_id: int, user_id: int) -> None:
    await _execute(
        "DELETE FROM cfzy_biz_wencai_query WHERE id=%s AND user_id=%s",
        (query_id, user_id),
    )


async def list_all_enabled_queries() -> list[dict]:
    """全部用户的启用语句(供 scan_wencai 逐条跑)。"""
    return await _fetchall(
        "SELECT * FROM cfzy_biz_wencai_query WHERE enabled=1 ORDER BY user_id, sort_order, id"
    )
