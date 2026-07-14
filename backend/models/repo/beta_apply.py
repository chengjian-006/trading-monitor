"""官网内测申请 CRUD - cfzy_biz_beta_apply 表 (v1.7.613)。

官网(主域名)表单免鉴权提交, 落这张表 + 飞书通知我。status 供以后跟进标记:
new(待处理) / contacted(已联系) / rejected(不邀请)。
"""
from backend.models.repo._db import _execute, _fetchall


async def add_apply(contact: str, remark: str, ip: str, user_agent: str) -> int:
    """新增一条内测申请, 返回新 id。"""
    from backend.models.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_beta_apply (contact, remark, ip, user_agent) "
                "VALUES (%s, %s, %s, %s)",
                (contact, remark, ip, user_agent),
            )
            return cur.lastrowid


async def count_by_ip_recent(ip: str, hours: int = 24) -> int:
    """同 IP 近 N 小时的提交数 —— 防刷用(内存限流重启会清零, 这个不会)。"""
    rows = await _fetchall(
        "SELECT COUNT(*) AS c FROM cfzy_biz_beta_apply "
        "WHERE ip=%s AND created_at > DATE_SUB(NOW(), INTERVAL %s HOUR)",
        (ip, hours),
    )
    return int(rows[0]["c"]) if rows else 0


async def list_applies(limit: int = 200) -> list[dict]:
    """按时间倒序列出申请(供后台查看)。"""
    return await _fetchall(
        "SELECT * FROM cfzy_biz_beta_apply ORDER BY id DESC LIMIT %s",
        (limit,),
    )


async def set_status(apply_id: int, status: str) -> None:
    await _execute(
        "UPDATE cfzy_biz_beta_apply SET status=%s WHERE id=%s",
        (status, apply_id),
    )
