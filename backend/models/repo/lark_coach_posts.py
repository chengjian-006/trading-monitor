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


async def recent_coach_texts(minutes: int = 30, limit: int = 80) -> list[dict]:
    """最近 N 分钟内已入库的文本观点(message_id + content), 供近似去重比对。

    按 posted_at 卡窗口: 播报机器人重发的两条时间戳不同, 但都落在窗口内。
    """
    return await _fetchall(
        "SELECT message_id, posted_at, content FROM cfzy_biz_lark_coach_posts "
        "WHERE msg_type <> 'image' AND posted_at >= (NOW() - INTERVAL %s MINUTE) "
        "ORDER BY posted_at DESC LIMIT %s",
        (minutes, limit),
    )


async def list_coach_posts(limit: int = 100, offset: int = 0) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_lark_coach_posts ORDER BY posted_at DESC, id DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


async def get_coach_post_by_message_id(message_id: str) -> dict | None:
    rows = await _fetchall(
        "SELECT * FROM cfzy_biz_lark_coach_posts WHERE message_id = %s LIMIT 1", (message_id,))
    return rows[0] if rows else None


async def list_unrelayed_coach_posts(limit: int = 40) -> list[dict]:
    """待转发到自建群的消息, 老的先转(保持群里时序)。"""
    return await _fetchall(
        "SELECT * FROM cfzy_biz_lark_coach_posts WHERE relayed_at IS NULL "
        "ORDER BY posted_at ASC, id ASC LIMIT %s", (limit,))


async def mark_coach_post_relayed(post_id: int) -> None:
    from backend.models.database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE cfzy_biz_lark_coach_posts SET relayed_at = NOW() WHERE id = %s",
                (post_id,))
