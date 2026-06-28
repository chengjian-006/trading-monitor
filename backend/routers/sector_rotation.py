"""板块轮动·弱强转换 API — 短线盯盘 v1.7.x.

  GET /api/sector-rotation   当日盘中题材轮动状态(看板) + 14:30 次日预测
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/sector-rotation", tags=["sector-rotation"])


@router.get("")
async def get_sector_rotation(_: Annotated[dict, Depends(get_current_user)]):
    row = await repository.get_sector_rotation()
    if not row:
        return {"trade_date": None, "computed_at": None, "transitions": [],
                "items": [], "predict_at": None, "predict": None}
    rotation = row.get("rotation_data") or {}
    predict = row.get("predict_data") or {}
    return {
        "trade_date": str(row.get("trade_date")) if row.get("trade_date") else None,
        "computed_at": rotation.get("computed_at"),
        "transitions": rotation.get("transitions") or [],
        "items": rotation.get("items") or [],
        "predict_at": predict.get("computed_at"),
        "predict": predict.get("groups") or None,
    }
