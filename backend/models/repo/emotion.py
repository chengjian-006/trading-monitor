"""短线情绪温度快照 CRUD - cfzy_sys_emotion_snapshot 表 (P1)."""
import json

from backend.models.repo._db import _execute, _fetchall, _fetchone


def _decode(row: dict | None) -> dict | None:
    if not row:
        return row
    for k in ("board_ladder", "board_stocks", "limit_up_codes"):
        if isinstance(row.get(k), str):
            try:
                row[k] = json.loads(row[k])
            except (ValueError, TypeError):
                row[k] = None
    return row


async def save_emotion_snapshot(snap: dict) -> None:
    """插入一条情绪快照 (每次采集一行, 同日多行构成当日情绪曲线)。"""
    await _execute(
        "INSERT INTO cfzy_sys_emotion_snapshot "
        "(trade_date, source, limit_up_count, limit_up_history, limit_down_count, limit_down_history, "
        " broken_board_count, up_count, down_count, seal_rate, highest_board, board_ladder, board_stocks, "
        " limit_up_codes, yest_limit_up_premium, emotion_phase, "
        " market_amount, volume_ratio, emotion_score, emotion_cycle) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            snap["trade_date"], snap.get("source", ""),
            snap.get("limit_up_count"), snap.get("limit_up_history"),
            snap.get("limit_down_count"), snap.get("limit_down_history"),
            snap.get("broken_board_count"),
            snap.get("up_count"), snap.get("down_count"),
            snap.get("seal_rate"),
            snap.get("highest_board"),
            json.dumps(snap.get("board_ladder") or [], ensure_ascii=False),
            json.dumps(snap.get("board_stocks") or [], ensure_ascii=False),
            json.dumps(snap.get("limit_up_codes") or [], ensure_ascii=False),
            snap.get("yest_limit_up_premium"), snap.get("emotion_phase", ""),
            snap.get("market_amount"), snap.get("volume_ratio"),
            snap.get("emotion_score"), snap.get("emotion_cycle"),
        ),
    )


async def get_latest_emotion() -> dict | None:
    """最近一条情绪快照 (盯盘当前值)。"""
    # ORDER BY 带上 trade_date 前导列 → 完整命中 idx_date_time(trade_date, captured_at) 反向扫;
    # 只按 captured_at 排序用不上该索引, 表逐日增长会越来越慢(全表 filesort)
    row = await _fetchone(
        "SELECT * FROM cfzy_sys_emotion_snapshot ORDER BY trade_date DESC, captured_at DESC LIMIT 1"
    )
    return _decode(row)


async def get_emotion_history(trade_date: str) -> list[dict]:
    """某交易日全部快照, 按时间升序 (画当日情绪曲线)。"""
    rows = await _fetchall(
        "SELECT * FROM cfzy_sys_emotion_snapshot WHERE trade_date = %s "
        "ORDER BY captured_at ASC",
        (trade_date,),
    )
    return [_decode(r) for r in rows]


async def get_last_emotion_before(trade_date: str) -> dict | None:
    """trade_date 之前最近一个交易日的最后一条快照 (取昨涨停 codes 用)。"""
    row = await _fetchone(
        "SELECT * FROM cfzy_sys_emotion_snapshot WHERE trade_date < %s "
        "ORDER BY trade_date DESC, captured_at DESC LIMIT 1",
        (trade_date,),
    )
    return _decode(row)
