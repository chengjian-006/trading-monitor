"""同花顺投资圈博主发帖 CRUD - cfzy_biz_blogger_posts 表.

  save_post              — INSERT IGNORE, 返回是否新插入(判定"新帖"用)
  get_recent_posts       — 某博主最近 N 条 (按发帖时间倒序)
  list_posts             — 分页查询(给前端/接口用)
  mark_pushed            — 批量标记已推送

去重靠 uk_blogger_post(blogger_fid, post_id) 唯一索引 + INSERT IGNORE, 同 cfzy_biz_signals 的 uk_signal_day 思路。
"""
import json

from backend.models.repo._db import _execute, _fetchall


async def save_post(blogger_fid: str, blogger_name: str, post_id: str,
                    posted_at, content: str, stock_codes: list[str] | None = None,
                    url: str = "", raw: dict | None = None) -> bool:
    """写入一条博主帖子。返回 True 表示是新帖(本次实际插入), False 表示已存在被 IGNORE。

    用 rowcount 判定: INSERT IGNORE 命中唯一索引时 rowcount=0, 新插入时 rowcount=1。
    """
    from backend.models.database import get_pool
    import aiomysql  # noqa: F401

    codes_str = ",".join(stock_codes) if stock_codes else ""
    raw_str = json.dumps(raw, ensure_ascii=False) if raw else None
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_biz_blogger_posts "
                "(blogger_fid, blogger_name, post_id, posted_at, content, stock_codes, url, raw) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (blogger_fid, blogger_name, post_id, posted_at, content, codes_str, url, raw_str),
            )
            return cur.rowcount == 1


async def get_recent_posts(blogger_fid: str, limit: int = 20) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_blogger_posts WHERE blogger_fid = %s "
        "ORDER BY posted_at DESC, id DESC LIMIT %s",
        (blogger_fid, limit),
    )


async def list_posts(limit: int = 50, offset: int = 0) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_blogger_posts ORDER BY posted_at DESC, id DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


async def mark_pushed(ids: list[int]):
    if not ids:
        return
    ph = ",".join(["%s"] * len(ids))
    await _execute(
        f"UPDATE cfzy_biz_blogger_posts SET pushed = 1 WHERE id IN ({ph})",
        tuple(ids),
    )
