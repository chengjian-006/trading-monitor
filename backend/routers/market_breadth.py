"""全市场广度指标 API - v1.7.x.

GET /api/market-breadth/latest  : 最新广度 + 区间含义(温度计)
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services.market_breadth_refresher import breadth_band

router = APIRouter(prefix="/api/market-breadth", tags=["market-breadth"])


@router.get("/latest")
async def get_latest(_: Annotated[dict, Depends(get_current_user)]):
    row = await repository.get_latest_breadth()
    if not row:
        return {"available": False, "message": "暂无广度数据(盘后15:35生成)"}
    pct = row.get("ma20_ratio")
    label, level, hint = breadth_band(pct)
    return {
        "available": True,
        "trade_date": row["trade_date"],
        "ma20_ratio": pct,
        "ma10_ratio": row.get("ma10_ratio"),
        "ma60_ratio": row.get("ma60_ratio"),
        "total_count": row.get("total_count"),
        "band": label,
        "level": level,
        "hint": hint,
    }
