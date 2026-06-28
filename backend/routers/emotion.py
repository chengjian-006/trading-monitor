"""短线情绪温度盯盘 API — 短线盯盘 P1。

  GET /api/emotion/current        最近一条情绪快照 (盯盘当前值)
  GET /api/emotion/history?date=  某交易日情绪曲线 (默认最近有数据的交易日)
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/emotion", tags=["emotion"])


@router.get("/current")
async def get_current_emotion(user: Annotated[dict, Depends(get_current_user)]):
    snap = await repository.get_latest_emotion()
    if not snap:
        return {}
    # 连板梯队个股标记是否在该用户自选/持仓池 (快照是全市场的, 自选按人, 故读取时按当前用户打标)
    stocks = snap.get("board_stocks")
    if stocks:
        try:
            pool_codes = {s["code"] for s in await repository.list_stocks(user["id"])}
            for it in stocks:
                it["in_pool"] = it.get("code") in pool_codes
        except Exception:
            pass  # 标记失败不影响主数据返回
    return snap


@router.get("/history")
async def get_emotion_history(
    _: Annotated[dict, Depends(get_current_user)],
    date: str | None = Query(None, description="交易日 YYYY-MM-DD, 缺省取最近有数据的交易日"),
):
    if not date:
        latest = await repository.get_latest_emotion()
        if not latest:
            return {"trade_date": None, "points": []}
        date = latest["trade_date"]
    points = await repository.get_emotion_history(date)
    return {"trade_date": date, "points": points}
