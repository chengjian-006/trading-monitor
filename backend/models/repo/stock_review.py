"""AI 个股研判缓存 CRUD — cfzy_biz_stock_review。
同用户+同票(code)+同天(gen_date)命中缓存, 免得每次点开都重新调 LLM。
count_reviews_today 供路由每日限额判断(数的是当日已落缓存的不同票数)。"""
import json

from backend.models.repo._db import _execute, _fetchone


async def save_stock_review(user_id, code, gen_date, facts: dict, narrative):
    await _execute(
        "INSERT INTO cfzy_biz_stock_review (user_id, code, gen_date, facts_json, narrative) "
        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE facts_json=VALUES(facts_json), "
        "narrative=VALUES(narrative), created_at=CURRENT_TIMESTAMP",
        (user_id, code, gen_date, json.dumps(facts, ensure_ascii=False, default=str), narrative),
    )


async def get_stock_review(user_id, code, gen_date):
    return await _fetchone(
        "SELECT facts_json, narrative FROM cfzy_biz_stock_review "
        "WHERE user_id=%s AND code=%s AND gen_date=%s", (user_id, code, gen_date))


async def count_reviews_today(user_id: int) -> int:
    row = await _fetchone(
        "SELECT COUNT(*) AS n FROM cfzy_biz_stock_review WHERE user_id=%s AND gen_date=CURDATE()",
        (user_id,))
    return int(row["n"]) if row else 0
