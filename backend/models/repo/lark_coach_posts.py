"""飞书群「藏龙岛观点」CRUD - cfzy_biz_lark_coach_posts 表.

  save_coach_post   — INSERT IGNORE, 返回是否新插入(判定"新消息"用)
  list_coach_posts  — 分页查询(给前端/接口用, 按发布时间倒序)

去重靠 uk_msg(message_id) 唯一索引 + INSERT IGNORE, 同 cfzy_biz_blogger_posts 思路。
"""
import json

from backend.models.repo._db import _fetchall


async def save_coach_post(message_id: str, chat_id: str, sender_open_id: str,
                          coach_name: str, posted_at, content: str,
                          msg_type: str = "text", raw: dict | None = None) -> bool:
    """写入一条藏龙岛消息。返回 True=新消息(本次实际插入), False=已存在被 IGNORE。"""
    from backend.models.database import get_pool
    import aiomysql  # noqa: F401

    raw_str = json.dumps(raw, ensure_ascii=False) if raw else None
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_biz_lark_coach_posts "
                "(message_id, chat_id, sender_open_id, coach_name, posted_at, content, msg_type, raw) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (message_id, chat_id, sender_open_id, coach_name, posted_at, content, msg_type, raw_str),
            )
            return cur.rowcount == 1


async def list_coach_posts(limit: int = 100, offset: int = 0) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_lark_coach_posts ORDER BY posted_at DESC, id DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
