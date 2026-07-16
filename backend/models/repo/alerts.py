"""股票池自定义预警 CRUD - cfzy_biz_stock_alerts 表.

一行 = 一条预警 = 内部多条件 AND; 一只股票可挂多条(任一条各自触发=OR 效果)。
conditions 以 JSON 文本存储, 数组内全部满足才触发。维度见 custom_alert_scanner。

v1.7.626 均线快捷提醒: preset='ma10'|'ma20'|'ma60'(空=普通自定义) 标记一键开关来源;
repeat_daily=1 的预警触发后【不】置 triggered, 只记 last_triggered_at, 当天不再报、
次日自动恢复监控(每股每档每天最多一次)。
"""
import json

from backend.models.database import get_pool
from backend.models.repo._db import _execute, _fetchall, _fetchone


def _row_conditions(row: dict) -> dict:
    """把行里的 conditions 文本解析成 list, 失败则空列表。"""
    raw = row.get("conditions")
    if isinstance(raw, (list, dict)):
        return row
    try:
        row["conditions"] = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        row["conditions"] = []
    return row


async def list_alerts(user_id: int) -> list[dict]:
    """当前用户全部预警(供池页面汇总/打标)。"""
    rows = await _fetchall(
        "SELECT * FROM cfzy_biz_stock_alerts WHERE user_id = %s ORDER BY code, id DESC",
        (user_id,),
    )
    return [_row_conditions(r) for r in rows]


async def list_alerts_by_code(user_id: int, code: str) -> list[dict]:
    rows = await _fetchall(
        "SELECT * FROM cfzy_biz_stock_alerts WHERE user_id = %s AND code = %s ORDER BY id DESC",
        (user_id, code),
    )
    return [_row_conditions(r) for r in rows]


async def list_active_alerts() -> list[dict]:
    """全用户 enabled=1 且 status='active' 的预警, 并带上该票当前行情(price/pct_change/name)。
    JOIN 股票池(未删除)保证只检测仍在池中的票, 行情取 quote_refresher 维护的实时值。
    repeat_daily 的预警当天已触发过(last_triggered_at=今天)则本日不再出列。"""
    rows = await _fetchall(
        "SELECT a.id, a.user_id, a.code, a.note, a.conditions, a.preset, a.repeat_daily, "
        "       p.name AS name, p.price AS price, p.pct_change AS pct_change "
        "FROM cfzy_biz_stock_alerts a "
        "JOIN cfzy_biz_stock_pool p ON p.code = a.code AND p.user_id = a.user_id AND p.deleted_at IS NULL "
        "WHERE a.enabled = 1 AND a.status = 'active' "
        "AND (a.repeat_daily = 0 OR a.last_triggered_at IS NULL OR DATE(a.last_triggered_at) < CURDATE())",
    )
    return [_row_conditions(r) for r in rows]


async def get_alert(user_id: int, alert_id: int) -> dict | None:
    row = await _fetchone(
        "SELECT * FROM cfzy_biz_stock_alerts WHERE id = %s AND user_id = %s",
        (alert_id, user_id),
    )
    return _row_conditions(row) if row else None


async def create_alert(user_id: int, code: str, conditions: list, note: str = "",
                       enabled: int = 1, preset: str = "", repeat_daily: int = 0) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_stock_alerts "
                "(user_id, code, note, conditions, enabled, status, preset, repeat_daily) "
                "VALUES (%s, %s, %s, %s, %s, 'active', %s, %s)",
                (user_id, code, note or None, json.dumps(conditions, ensure_ascii=False),
                 int(enabled), preset or "", int(repeat_daily)),
            )
            return cur.lastrowid


async def get_preset_alert(user_id: int, code: str, preset: str) -> dict | None:
    """该票的某个均线快捷预警(一键开关按 preset 唯一定位)。"""
    row = await _fetchone(
        "SELECT * FROM cfzy_biz_stock_alerts WHERE user_id = %s AND code = %s AND preset = %s",
        (user_id, code, preset),
    )
    return _row_conditions(row) if row else None


async def delete_preset_alert(user_id: int, code: str, preset: str) -> None:
    await _execute(
        "DELETE FROM cfzy_biz_stock_alerts WHERE user_id = %s AND code = %s AND preset = %s",
        (user_id, code, preset),
    )


async def update_alert(user_id: int, alert_id: int, *, conditions: list | None = None,
                       note: str | None = None, enabled: int | None = None,
                       status: str | None = None) -> None:
    """编辑/启停/重启。重启即把 status 传 'active'(会清空触发记录)。"""
    sets, vals = [], []
    if conditions is not None:
        sets.append("conditions = %s")
        vals.append(json.dumps(conditions, ensure_ascii=False))
    if note is not None:
        sets.append("note = %s")
        vals.append(note or None)
    if enabled is not None:
        sets.append("enabled = %s")
        vals.append(int(enabled))
    if status is not None:
        sets.append("status = %s")
        vals.append(status)
        if status == "active":
            sets.append("last_triggered_at = NULL")
            sets.append("triggered_price = NULL")
    if not sets:
        return
    vals.extend([alert_id, user_id])
    await _execute(
        f"UPDATE cfzy_biz_stock_alerts SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
        tuple(vals),
    )


async def delete_alert(user_id: int, alert_id: int) -> None:
    await _execute(
        "DELETE FROM cfzy_biz_stock_alerts WHERE id = %s AND user_id = %s",
        (alert_id, user_id),
    )


async def delete_alerts_for_stock(user_id: int, code: str) -> None:
    """股票移出池时联动清理该票预警。"""
    await _execute(
        "DELETE FROM cfzy_biz_stock_alerts WHERE user_id = %s AND code = %s",
        (user_id, code),
    )


async def mark_triggered(alert_id: int, price: float | None, repeat_daily: bool = False) -> None:
    """触发登记。一次性: 置 triggered 失效需手动重启;
    repeat_daily: 保持 active, 只记触发时间(当天不再出 list_active_alerts, 次日自动恢复)。"""
    if repeat_daily:
        await _execute(
            "UPDATE cfzy_biz_stock_alerts SET "
            "last_triggered_at = NOW(), triggered_price = %s WHERE id = %s",
            (price, alert_id),
        )
        return
    await _execute(
        "UPDATE cfzy_biz_stock_alerts SET status = 'triggered', "
        "last_triggered_at = NOW(), triggered_price = %s WHERE id = %s",
        (price, alert_id),
    )
