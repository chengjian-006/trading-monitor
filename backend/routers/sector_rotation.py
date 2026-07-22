"""板块轮动·弱强转换 API — 短线盯盘 v1.7.x.

  GET /api/sector-rotation   当日盘中题材轮动状态(看板) + 14:30 次日预测
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/sector-rotation", tags=["sector-rotation"])


def _has_data(row: dict | None) -> bool:
    if not row:
        return False
    rot = row.get("rotation_data") or {}
    pred = row.get("predict_data") or {}
    return bool(rot.get("items") or pred.get("groups"))


@router.get("")
async def get_sector_rotation(_: Annotated[dict, Depends(get_current_user)]):
    row = await repository.get_sector_rotation()
    # 当天还没算出(盘前/非交易日/当日首扫前): 回退到最近一个交易日的快照, 对齐面板
    # "非交易日保留上一交易日结果"的承诺。stale=True 时前端标注显示的是哪一天。
    stale = False
    if not _has_data(row):
        latest = await repository.get_latest_sector_rotation()
        if _has_data(latest):
            row = latest
            stale = str(latest.get("trade_date")) != datetime.now().strftime("%Y-%m-%d")
    if not row:
        return {"trade_date": None, "computed_at": None, "transitions": [],
                "items": [], "predict_at": None, "predict": None, "stale": False}
    rotation = row.get("rotation_data") or {}
    predict = row.get("predict_data") or {}
    return {
        "trade_date": str(row.get("trade_date")) if row.get("trade_date") else None,
        "computed_at": rotation.get("computed_at"),
        "transitions": rotation.get("transitions") or [],
        "items": rotation.get("items") or [],
        "predict_at": predict.get("computed_at"),
        "predict": predict.get("groups") or None,
        "stale": stale,
    }
