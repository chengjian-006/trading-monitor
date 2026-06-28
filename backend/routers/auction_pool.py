"""自选股集合竞价成交额 API (v1.7.274).

  GET /api/auction-pool?date=YYYY-MM-DD&min_amount_yi=1
    某交易日自选股集合竞价成交额排行(降序)。
    date 不传 → 取最近有数据的交易日; min_amount_yi 单位"亿元", 默认0=不过滤。
    返回 count(满足阈值的只数) / total(当日采集总数) / items。
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/auction-pool", tags=["auction-pool"])


@router.get("")
async def get_auction_pool(
    user: Annotated[dict, Depends(get_current_user)],
    date: str | None = Query(None, description="交易日 YYYY-MM-DD, 不传取最近有数据的交易日"),
    min_amount_yi: float = Query(0.0, description="竞价成交额下限, 单位亿元"),
):
    trade_date = date or await repository.get_auction_latest_date()
    if not trade_date:
        return {"trade_date": None, "count": 0, "total": 0, "min_amount_yi": min_amount_yi, "items": []}
    min_amount = float(min_amount_yi) * 1e8
    items = await repository.get_auction_snapshots(trade_date, min_amount=min_amount)
    total = len(await repository.get_auction_snapshots(trade_date)) if min_amount > 0 else len(items)
    # 成交额转亿元, 方便前端直接展示
    for it in items:
        it["auction_amount_yi"] = round((it.get("auction_amount") or 0) / 1e8, 4)
    return {
        "trade_date": trade_date,
        "count": len(items),
        "total": total,
        "min_amount_yi": min_amount_yi,
        "items": items,
    }
