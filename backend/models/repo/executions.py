"""信号执行记录 + AI 报告反馈 - cfzy_biz_signal_executions / cfzy_biz_report_feedback 表.

用户对系统输出的反馈通道:
  signal_executions:  对每条信号标记"已执行/已跳过", 选填实际成交价 → 真实跟单收益
  report_feedback:    对每份 AI 时段报告点赞/点踩 + 备注 → 后续 LLM 优化输入
"""
from backend.models.repo._db import _execute, _fetchall, _fetchone


# ── 信号执行记录 (信号 → 我的操作闭环) ──

async def upsert_signal_execution(
    user_id: int, signal_pk: int, code: str, action: str,
    actual_price: float | None = None, actual_qty: int | None = None,
    notes: str | None = None,
) -> int:
    """user 标记某条信号的执行状态. 同 (user, signal_pk) 已存在则更新."""
    await _execute(
        "INSERT INTO cfzy_biz_signal_executions "
        "(user_id, signal_pk, code, action, actual_price, actual_qty, notes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "action = VALUES(action), actual_price = VALUES(actual_price), "
        "actual_qty = VALUES(actual_qty), notes = VALUES(notes)",
        (user_id, signal_pk, code, action, actual_price, actual_qty, notes),
    )
    row = await _fetchone(
        "SELECT id FROM cfzy_biz_signal_executions WHERE user_id = %s AND signal_pk = %s",
        (user_id, signal_pk),
    )
    return int(row["id"]) if row else 0


async def delete_signal_execution(user_id: int, signal_pk: int) -> None:
    await _execute(
        "DELETE FROM cfzy_biz_signal_executions WHERE user_id = %s AND signal_pk = %s",
        (user_id, signal_pk),
    )


async def list_signal_executions(user_id: int, signal_pks: list[int] | None = None) -> list[dict]:
    """按 signal_pk 列表批量取执行记录, 用于前端给信号列表附标记."""
    if signal_pks is None:
        return await _fetchall(
            "SELECT * FROM cfzy_biz_signal_executions WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
    if not signal_pks:
        return []
    placeholders = ",".join(["%s"] * len(signal_pks))
    return await _fetchall(
        f"SELECT * FROM cfzy_biz_signal_executions WHERE user_id = %s AND signal_pk IN ({placeholders})",
        (user_id, *signal_pks),
    )


# ── AI 报告反馈 (用户对 AI 时段报告点赞/点踩) ──

async def upsert_report_feedback(user_id: int, report_id: int, vote: str, notes: str | None = None) -> int:
    await _execute(
        "INSERT INTO cfzy_biz_report_feedback (user_id, report_id, vote, notes) "
        "VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE vote = VALUES(vote), notes = VALUES(notes)",
        (user_id, report_id, vote, notes),
    )
    row = await _fetchone(
        "SELECT id FROM cfzy_biz_report_feedback WHERE user_id = %s AND report_id = %s",
        (user_id, report_id),
    )
    return int(row["id"]) if row else 0


async def delete_report_feedback(user_id: int, report_id: int) -> None:
    await _execute(
        "DELETE FROM cfzy_biz_report_feedback WHERE user_id = %s AND report_id = %s",
        (user_id, report_id),
    )


async def list_report_feedback(user_id: int, report_ids: list[int] | None = None) -> list[dict]:
    if report_ids is None:
        return await _fetchall(
            "SELECT * FROM cfzy_biz_report_feedback WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
    if not report_ids:
        return []
    placeholders = ",".join(["%s"] * len(report_ids))
    return await _fetchall(
        f"SELECT * FROM cfzy_biz_report_feedback WHERE user_id = %s AND report_id IN ({placeholders})",
        (user_id, *report_ids),
    )
