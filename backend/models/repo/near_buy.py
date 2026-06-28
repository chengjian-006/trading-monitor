"""临近买点快照 CRUD - cfzy_sys_near_buy_snapshot 表 (每用户一行, 整表 UPSERT)."""
import json

from backend.models.repo._db import _execute, _fetchone


async def save_near_buy_snapshot(user_id: int, trade_date: str,
                                 items: list[dict], scanned: int) -> None:
    """整表替换某用户的临近买点快照 (单行 UPSERT)。"""
    await _execute(
        "INSERT INTO cfzy_sys_near_buy_snapshot "
        "(user_id, trade_date, near_count, scanned, items, computed_at) "
        "VALUES (%s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE trade_date=VALUES(trade_date), near_count=VALUES(near_count), "
        "scanned=VALUES(scanned), items=VALUES(items), computed_at=NOW()",
        (user_id, trade_date, len(items), scanned,
         json.dumps(items, ensure_ascii=False)),
    )


async def get_near_buy_snapshot(user_id: int) -> dict | None:
    """取某用户最新临近买点快照 (盯盘当前榜)。"""
    row = await _fetchone(
        "SELECT * FROM cfzy_sys_near_buy_snapshot WHERE user_id = %s", (user_id,)
    )
    if not row:
        return None
    if isinstance(row.get("items"), str):
        try:
            row["items"] = json.loads(row["items"])
        except (ValueError, TypeError):
            row["items"] = []
    return row
