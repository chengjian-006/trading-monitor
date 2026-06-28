"""缩量后放量突破 9:45 vs 10:00 闸门 A/B - cfzy_biz_gate_ab 表 (v1.7.x 临时实验)."""
from backend.models.repo._db import _execute, _fetchall


async def save_gate_ab(rec: dict, arms: list[str]) -> None:
    """对给定 arms 各写一条(INSERT IGNORE → 同股同日同档只留首次命中)。"""
    for arm in arms:
        await _execute(
            "INSERT IGNORE INTO cfzy_biz_gate_ab "
            "(code, trade_date, arm, name, trigger_time, trigger_price, trigger_level, gap_pct, amount_est_yi, sealed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (rec["code"], rec["trade_date"], arm, rec.get("name", ""),
             rec["trigger_time"], rec["trigger_price"], rec["trigger_level"],
             rec["gap_pct"], rec["amount_est_yi"], rec.get("sealed", 0)),
        )


async def get_gate_ab(start_date: str, end_date: str) -> list[dict]:
    return await _fetchall(
        "SELECT code, trade_date, arm, name, trigger_time, trigger_price, trigger_level, gap_pct, amount_est_yi, sealed "
        "FROM cfzy_biz_gate_ab WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date, code, arm",
        (start_date, end_date),
    )
