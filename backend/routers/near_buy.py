"""临近买点榜 API — 短线盯盘 v1.7.x.

  GET /api/near-buy   当前用户的临近买点快照(触发/接近四买点的自选清单)
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/near-buy", tags=["near-buy"])


@router.get("")
async def get_near_buy(user: Annotated[dict, Depends(get_current_user)]):
    snap = await repository.get_near_buy_snapshot(user["id"])
    if not snap:
        return {"trade_date": None, "computed_at": None, "scanned": 0, "items": []}
    return {
        "trade_date": snap.get("trade_date"),
        "computed_at": snap.get("computed_at"),
        "scanned": snap.get("scanned", 0),
        "near_count": snap.get("near_count", 0),
        "items": snap.get("items") or [],
    }
