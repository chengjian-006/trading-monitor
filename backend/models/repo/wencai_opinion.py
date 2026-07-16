"""问财观点参考 CRUD - cfzy_biz_wencai_opinion 表 (v1.7.627).

同花顺问财 chat「智能调度」投顾式推荐的存档: 一条口语问题一行, 存整段话术 + 撞出的股票。
本地油猴代跑上报(共享密钥鉴权), 观点默认全局(user_id=0)供主账号查看。是 LLM 观点非回测信号。
"""
import json

from backend.models.repo._db import _execute, _fetchall, _fetchone


async def insert_opinion(user_id: int, question: str, answer_text: str,
                         stocks: list[dict], agent_mode: str, trace_id: str,
                         uploader: str = "") -> int:
    """插入一条问财观点, 返回新行 id。"""
    from backend.models.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_wencai_opinion "
                "(user_id, question, answer_text, stocks, agent_mode, trace_id, uploader) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (user_id, question[:255], answer_text or "",
                 json.dumps(stocks or [], ensure_ascii=False), (agent_mode or "")[:20],
                 (trace_id or "")[:64], (uploader or "")[:40]),
            )
            return cur.lastrowid


async def list_opinions(user_id: int, limit: int = 100) -> list[dict]:
    """取「全局(user_id=0) + 该用户」的观点, 按时间倒序。"""
    rows = await _fetchall(
        "SELECT * FROM cfzy_biz_wencai_opinion WHERE user_id IN (0, %s) "
        "ORDER BY created_at DESC LIMIT %s",
        (user_id, int(limit)),
    )
    for row in rows:
        if isinstance(row.get("stocks"), str):
            try:
                row["stocks"] = json.loads(row["stocks"])
            except (ValueError, TypeError):
                row["stocks"] = []
    return rows


async def delete_opinion(opinion_id: int, user_id: int) -> None:
    """删一条观点(仅限本人或全局那条)。"""
    await _execute(
        "DELETE FROM cfzy_biz_wencai_opinion WHERE id=%s AND user_id IN (0, %s)",
        (opinion_id, user_id),
    )
