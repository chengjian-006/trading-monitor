"""自选股风险公告去重台账 CRUD - cfzy_biz_risk_ann_seen 表(v1.7.x).

  save_risk_ann   — INSERT IGNORE, 返回是否新命中(本次实际插入); 同 blogger_posts 思路
  list_risk_anns  — 最近命中列表(给接口/复盘用, 可选)

去重靠 uk_risk_ann(code, ann_id) 唯一索引: 同一公告(同股+同公告ID)只推一次。
"""
from backend.models.repo._db import _execute, _fetchall


async def save_risk_ann(code: str, name: str, ann_id: str, title: str,
                        tags: str, ann_date: str, url: str = "") -> bool:
    """写入一条风险命中。返回 True=新命中(本次插入), False=已存在被 IGNORE。

    用 rowcount 判定: INSERT IGNORE 命中唯一索引时 rowcount=0, 新插入时 rowcount=1。
    """
    from backend.models.database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO cfzy_biz_risk_ann_seen "
                "(code, name, ann_id, title, tags, ann_date, url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (code, name, ann_id, title, tags, ann_date, url),
            )
            return cur.rowcount == 1


async def list_risk_anns(limit: int = 100) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_risk_ann_seen ORDER BY ann_date DESC, id DESC LIMIT %s",
        (limit,),
    )


async def get_recent_risk_anns_by_code(code: str, days: int = 14) -> list[dict]:
    """某票近 N 天风险公告(买卖卡背景标签用), 最新在前。ann_date 为 'YYYY-MM-DD' 文本列。"""
    from datetime import date, timedelta
    lo = (date.today() - timedelta(days=days)).isoformat()
    return await _fetchall(
        "SELECT code, name, title, tags, ann_date FROM cfzy_biz_risk_ann_seen "
        "WHERE code = %s AND ann_date >= %s ORDER BY ann_date DESC, id DESC LIMIT 3",
        (code, lo),
    )
