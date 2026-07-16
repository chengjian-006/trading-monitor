"""推送偏好 cfzy_biz_push_pref 读写 (v1.7.464+).

生效判定全在 SQL: revoked_at IS NULL 且 until_date >= CURDATE(), 过期行自动失效不必清理.
纯逻辑(签名/decide/until 计算)在 services/push_pref.py.
"""
from datetime import date

from ._db import _execute, _fetchall


async def add_pref(user_id: int, kind: str, target: str, until_date: date) -> None:
    """新增一条偏好; 同 (kind,target) 若已有生效行先撤销, 等价于刷新截止日(防重复堆叠)."""
    await _execute(
        "UPDATE cfzy_biz_push_pref SET revoked_at=NOW() "
        "WHERE user_id=%s AND kind=%s AND target=%s AND revoked_at IS NULL",
        (user_id, kind, target),
    )
    await _execute(
        "INSERT INTO cfzy_biz_push_pref (user_id, kind, target, until_date) VALUES (%s,%s,%s,%s)",
        (user_id, kind, target, until_date.isoformat()),
    )


async def active_prefs(user_id: int) -> list[dict]:
    """当前生效中的偏好(未撤销 + 未过期), 新到旧."""
    return await _fetchall(
        "SELECT id, kind, target, until_date, created_at FROM cfzy_biz_push_pref "
        "WHERE user_id=%s AND revoked_at IS NULL AND until_date >= CURDATE() "
        "ORDER BY created_at DESC",
        (user_id,),
    )


async def active_prefs_of_kinds(kinds: list[str]) -> list[dict]:
    """全用户按 kind 集合列生效中的偏好(未撤销+未过期), 供扫描任务整表拉订阅
    (均线到线提醒 ma_touch_alert 用: 订阅行自带 user_id, 不按单用户查)。"""
    if not kinds:
        return []
    ph = ",".join(["%s"] * len(kinds))
    return await _fetchall(
        f"SELECT id, user_id, kind, target, until_date, created_at FROM cfzy_biz_push_pref "
        f"WHERE kind IN ({ph}) AND revoked_at IS NULL AND until_date >= CURDATE() "
        "ORDER BY id",
        tuple(kinds),
    )


async def revoke(user_id: int, pref_id: int) -> None:
    await _execute(
        "UPDATE cfzy_biz_push_pref SET revoked_at=NOW() "
        "WHERE id=%s AND user_id=%s AND revoked_at IS NULL",
        (pref_id, user_id),
    )


async def revoke_kind(user_id: int, kind: str, target: str = "") -> int:
    """按 kind(+可选 target)撤销当前生效行; 供签名快捷链接「恢复」用(无 pref_id)。返回撤销条数。"""
    where = "user_id=%s AND kind=%s AND revoked_at IS NULL AND until_date >= CURDATE()"
    args: list = [user_id, kind]
    if target:
        where += " AND target=%s"
        args.append(target)
    rows = await _fetchall(f"SELECT id FROM cfzy_biz_push_pref WHERE {where}", tuple(args))
    if rows:
        await _execute(f"UPDATE cfzy_biz_push_pref SET revoked_at=NOW() WHERE {where}", tuple(args))
    return len(rows)
