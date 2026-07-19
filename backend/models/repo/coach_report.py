"""AI 交易教练复盘缓存 CRUD — cfzy_biz_coach_report。
同用户+同区间(period_key)+同天(gen_date)命中缓存, 免得每次刷新都重新调 LLM。"""
import json

from backend.models.repo._db import _execute, _fetchone


async def save_coach_report(user_id, period_key, gen_date, facts: dict, narrative):
    await _execute(
        "INSERT INTO cfzy_biz_coach_report (user_id, period_key, gen_date, facts_json, narrative) "
        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE facts_json=VALUES(facts_json), "
        "narrative=VALUES(narrative), created_at=CURRENT_TIMESTAMP",
        (user_id, period_key, gen_date, json.dumps(facts, ensure_ascii=False, default=str), narrative),
    )


async def get_coach_report(user_id, period_key, gen_date):
    return await _fetchone(
        "SELECT facts_json, narrative FROM cfzy_biz_coach_report "
        "WHERE user_id=%s AND period_key=%s AND gen_date=%s", (user_id, period_key, gen_date))
