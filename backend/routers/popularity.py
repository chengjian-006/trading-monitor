from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services.popularity_refresher import (
    analyze_stock_on_demand,
    refresh_popularity_now,
)

router = APIRouter(prefix="/api/popularity", tags=["popularity"])


@router.get("/dates")
async def get_popularity_dates(
    _: Annotated[dict, Depends(get_current_user)],
):
    dates = await repository.get_recent_popularity_dates(8)
    return {"dates": dates}


@router.get("/hot-concepts")
async def get_hot_concepts_history(
    _: Annotated[dict, Depends(get_current_user)],
):
    return await repository.get_recent_hot_concepts(5)


@router.get("")
async def get_popularity(
    _: Annotated[dict, Depends(get_current_user)],
    refresh: bool = Query(False),
    date: str | None = Query(None),
):
    if not refresh and not date:
        row = await repository.get_popularity_snapshot()
        if row and row.get("data"):
            data = row["data"]
            data["updated_at"] = str(row.get("updated_at", ""))
            return data
    if date:
        row = await repository.get_popularity_snapshot(date)
        if row and row.get("data"):
            data = row["data"]
            data["updated_at"] = str(row.get("updated_at", ""))
            return data
        return {"stocks": [], "hot_concepts": []}
    await refresh_popularity_now()
    row = await repository.get_popularity_snapshot()
    if row and row.get("data"):
        data = row["data"]
        data["updated_at"] = str(row.get("updated_at", ""))
        return data
    return {"stocks": [], "hot_concepts": []}


@router.post("/stocks/{code}/ai-analyze")
async def ai_analyze_single_stock(
    code: str,
    _: Annotated[dict, Depends(get_current_user)],
    date: str | None = Query(None),
):
    """按需触发单只人气榜个股的 AI 解读, 结果回写最新快照 JSON.

    返回 ai_analysis 文本 + ai_analysis_at 刷新时间 (前端用以展示"何时生成").
    """
    analysis, refreshed_at = await analyze_stock_on_demand(code, date)
    if not analysis:
        raise HTTPException(404, "未找到该股票或 AI 调用失败")
    return {"code": code, "ai_analysis": analysis, "ai_analysis_at": refreshed_at}
