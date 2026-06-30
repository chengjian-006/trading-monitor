"""问财候选榜 CRUD - cfzy_sys_wencai_pool 表 (每选股语句一行, 整行 UPSERT)。

strategy_id 全局唯一: 预置榜 breakout/pullback/theme (user_id=0 全局共享);
用户自定义榜 u{uid}_q{qid} (user_id=该用户)。列表按 user_id IN (0, 当前用户) 取。
"""
import json

from backend.models.repo._db import _execute, _fetchall


async def upsert_wencai_strategy(strategy_id: str, user_id: int, strategy_name: str,
                                 query_text: str, trade_date: str, items: list[dict],
                                 last_error: str = "") -> None:
    """整行替换某条选股语句的候选快照 (单行 UPSERT)。"""
    await _execute(
        "INSERT INTO cfzy_sys_wencai_pool "
        "(strategy_id, user_id, strategy_name, query_text, trade_date, stock_count, items, last_error, computed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id), strategy_name=VALUES(strategy_name), "
        "query_text=VALUES(query_text), trade_date=VALUES(trade_date), stock_count=VALUES(stock_count), "
        "items=VALUES(items), last_error=VALUES(last_error), computed_at=NOW()",
        (strategy_id, user_id, strategy_name, query_text, trade_date, len(items),
         json.dumps(items, ensure_ascii=False), last_error or ""),
    )


async def set_wencai_error(strategy_id: str, last_error: str) -> None:
    """只标记某策略本次拉取失败, 不动 items(保留上次成功结果)。整行不存在则跳过。"""
    await _execute(
        "UPDATE cfzy_sys_wencai_pool SET last_error=%s, computed_at=NOW() WHERE strategy_id=%s",
        (last_error[:255], strategy_id),
    )


async def delete_wencai_pool_row(strategy_id: str) -> None:
    """删除某条策略的候选快照行(用户删除自定义语句时连带清掉)。"""
    await _execute("DELETE FROM cfzy_sys_wencai_pool WHERE strategy_id=%s", (strategy_id,))


async def list_wencai_pool(user_id: int) -> list[dict]:
    """取「预置榜(user_id=0) + 该用户自定义榜」的最新候选快照。"""
    rows = await _fetchall(
        "SELECT * FROM cfzy_sys_wencai_pool WHERE user_id IN (0, %s) ORDER BY user_id, strategy_id",
        (user_id,),
    )
    for row in rows:
        if isinstance(row.get("items"), str):
            try:
                row["items"] = json.loads(row["items"])
            except (ValueError, TypeError):
                row["items"] = []
    return rows
