"""回踩20MA缩量后突破昨高 买卖提醒 — 虚拟持仓跟踪 repo - v1.7.x.

cfzy_biz_rally_track: 每个回踩20MA缩量后突破昨高买点 → 一笔跟踪持仓, 监控 +7%卖半/剩半破MA20/-6%止损/T+10时停.
买入价: 有交割单以交割单为准, 否则用触发价。
"""
from backend.models.repo._db import _execute, _fetchall, _fetchone


async def track_exists(code: str, signal_date: str) -> bool:
    r = await _fetchone(
        "SELECT id FROM cfzy_biz_rally_track WHERE code=%s AND signal_date=%s",
        (code, signal_date))
    return r is not None


async def create_track(code: str, name: str, signal_id: str, signal_date: str,
                       entry_price: float, entry_source: str):
    await _execute(
        "INSERT IGNORE INTO cfzy_biz_rally_track "
        "(code, name, signal_id, signal_date, entry_price, entry_source) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (code, name, signal_id, signal_date, entry_price, entry_source))


async def get_holding_tracks() -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_rally_track WHERE status='holding' ORDER BY signal_date")


async def mark_half_sold(track_id: int):
    await _execute("UPDATE cfzy_biz_rally_track SET half_sold=1 WHERE id=%s", (track_id,))


async def close_track(track_id: int, reason: str):
    await _execute(
        "UPDATE cfzy_biz_rally_track SET status='closed', close_reason=%s WHERE id=%s",
        (reason, track_id))


async def update_entry(track_id: int, entry_price: float, entry_source: str):
    await _execute(
        "UPDATE cfzy_biz_rally_track SET entry_price=%s, entry_source=%s WHERE id=%s",
        (entry_price, entry_source, track_id))


async def set_days_held(track_id: int, days: int):
    await _execute("UPDATE cfzy_biz_rally_track SET days_held=%s WHERE id=%s", (days, track_id))
