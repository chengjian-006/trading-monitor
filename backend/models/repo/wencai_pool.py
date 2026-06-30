"""问财候选榜 CRUD - cfzy_sys_wencai_pool 表 (每选股语句一行, 整行 UPSERT)."""
import json

from backend.models.repo._db import _execute, _fetchall


async def upsert_wencai_strategy(strategy_id: str, strategy_name: str, query_text: str,
                                 trade_date: str, items: list[dict],
                                 last_error: str = "") -> None:
    """整行替换某条选股语句的候选快照 (单行 UPSERT)。

    last_error 非空(拉取失败)时仍写一行: 保留传入的 items(上一次成功结果, 调用方负责传),
    只更新 last_error 标记本次失败, 供前端提示"该策略本次刷新失败"。"""
    await _execute(
        "INSERT INTO cfzy_sys_wencai_pool "
        "(strategy_id, strategy_name, query_text, trade_date, stock_count, items, last_error, computed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE strategy_name=VALUES(strategy_name), query_text=VALUES(query_text), "
        "trade_date=VALUES(trade_date), stock_count=VALUES(stock_count), items=VALUES(items), "
        "last_error=VALUES(last_error), computed_at=NOW()",
        (strategy_id, strategy_name, query_text, trade_date, len(items),
         json.dumps(items, ensure_ascii=False), last_error or ""),
    )


async def set_wencai_error(strategy_id: str, last_error: str) -> None:
    """只标记某策略本次拉取失败, 不动 items(保留上次成功结果)。整行不存在则跳过。"""
    await _execute(
        "UPDATE cfzy_sys_wencai_pool SET last_error=%s, computed_at=NOW() WHERE strategy_id=%s",
        (last_error[:255], strategy_id),
    )


async def list_wencai_pool() -> list[dict]:
    """取全部选股语句的最新候选快照 (问财候选榜页用)。"""
    rows = await _fetchall(
        "SELECT * FROM cfzy_sys_wencai_pool ORDER BY strategy_id"
    )
    for row in rows:
        if isinstance(row.get("items"), str):
            try:
                row["items"] = json.loads(row["items"])
            except (ValueError, TypeError):
                row["items"] = []
    return rows
