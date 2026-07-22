"""板块(题材)弱转强/强转弱预判 快照 CRUD - cfzy_sys_sector_rotation 表.

每日一行: rotation_data(盘中轮动状态, scan 每3分钟覆盖) + predict_data(14:30次日预测, 写一次)。
两段各自 upsert, 互不覆盖对方; 读取时各自 JSON 解析。
"""
import json
from datetime import datetime

from backend.models.repo._db import _execute, _fetchone


async def upsert_sector_rotation(trade_date: str, data: dict) -> None:
    """写/更新当日盘中题材轮动状态快照(覆盖 rotation_data)。"""
    payload = json.dumps(data, ensure_ascii=False)
    now = datetime.now()
    await _execute(
        "INSERT INTO cfzy_sys_sector_rotation (trade_date, rotation_data, rotation_at) "
        "VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE rotation_data=VALUES(rotation_data), rotation_at=VALUES(rotation_at)",
        (trade_date, payload, now),
    )


async def upsert_sector_prediction(trade_date: str, data: dict) -> None:
    """写/更新当日 14:30 次日预测(覆盖 predict_data)。"""
    payload = json.dumps(data, ensure_ascii=False)
    now = datetime.now()
    await _execute(
        "INSERT INTO cfzy_sys_sector_rotation (trade_date, predict_data, predict_at) "
        "VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE predict_data=VALUES(predict_data), predict_at=VALUES(predict_at)",
        (trade_date, payload, now),
    )


def _parse_rotation_row(row: dict | None) -> dict | None:
    if not row:
        return None
    for key in ("rotation_data", "predict_data"):
        if isinstance(row.get(key), str):
            try:
                row[key] = json.loads(row[key])
            except (ValueError, TypeError):
                row[key] = None
    return row


async def get_sector_rotation(trade_date: str | None = None) -> dict | None:
    """取某日(默认今日)的板块轮动+次日预测行, JSON 字段已解析。"""
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    row = await _fetchone(
        "SELECT * FROM cfzy_sys_sector_rotation WHERE trade_date = %s", (trade_date,)
    )
    return _parse_rotation_row(row)


async def get_latest_sector_rotation() -> dict | None:
    """取最近一个有数据的板块轮动行(trade_date 倒序第一条), JSON 已解析。
    供盘前/非交易日/当日首扫前回退展示上一交易日快照(对齐面板"非交易日保留上一交易日结果"的承诺)。"""
    row = await _fetchone(
        "SELECT * FROM cfzy_sys_sector_rotation ORDER BY trade_date DESC LIMIT 1"
    )
    return _parse_rotation_row(row)
