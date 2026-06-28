"""全市场广度指标 repo - v1.7.x.

cfzy_sys_market_breadth: 每日"站上MA20/MA10/MA60"个股比例, 盘后定时写入.
仅作大盘环境参考(温度计), 非买卖触发.
"""
from backend.models.repo._db import _execute, _fetchone


async def save_market_breadth(trade_date: str, ma20_ratio: float, ma10_ratio: float,
                              ma60_ratio: float, total_count: int):
    await _execute(
        "INSERT INTO cfzy_sys_market_breadth "
        "(trade_date, ma20_ratio, ma10_ratio, ma60_ratio, total_count) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE ma20_ratio=%s, ma10_ratio=%s, ma60_ratio=%s, total_count=%s",
        (trade_date, ma20_ratio, ma10_ratio, ma60_ratio, total_count,
         ma20_ratio, ma10_ratio, ma60_ratio, total_count),
    )


async def get_latest_breadth() -> dict | None:
    return await _fetchone(
        "SELECT * FROM cfzy_sys_market_breadth ORDER BY trade_date DESC LIMIT 1"
    )
