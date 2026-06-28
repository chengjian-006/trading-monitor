"""AI 真受益核查路由."""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services.substance_analyzer import (
    analyze_substance,
    save_substance_result,
    update_substance_score,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/substance", tags=["substance"])


class AnalyzeRequest(BaseModel):
    code: str
    theme: str
    persist: bool = True   # 是否把 AI 报告保存到 DB


class ScoreRequest(BaseModel):
    code: str
    score: int             # 0-5
    note: str = ""


@router.post("/analyze")
async def analyze(req: Annotated[AnalyzeRequest, Body()],
                  user: Annotated[dict, Depends(get_current_user)]):
    """调 AI 生成真受益核查报告(可选自动入库)。"""
    code = req.code.strip()
    theme = req.theme.strip()
    if not code or not theme:
        raise HTTPException(400, "code 和 theme 必填")

    # 自动从 stock_pool 取 name 和 industry
    user_id = user["id"]
    stocks = await repository.list_stocks(user_id)
    stock = next((s for s in stocks if s["code"] == code), None)
    name = stock["name"] if stock else code
    industry = (stock.get("industry") or "") if stock else ""

    logger.info(f"[substance] user={user_id} 核查 {name}({code}) 题材={theme}")
    result = await analyze_substance(code=code, name=name, theme=theme, industry=industry)

    if result["ok"] and req.persist and stock:
        try:
            await save_substance_result(code=code, user_id=user_id, analysis=result["report"])
            await repository.add_log(user_id, user["username"], "substance_analyze", code)
        except Exception as e:
            logger.warning(f"[substance] 入库失败: {e}")

    return result


@router.post("/score")
async def score(req: Annotated[ScoreRequest, Body()],
                user: Annotated[dict, Depends(get_current_user)]):
    """更新用户对某只股票真受益的人工打分(0-5星)。"""
    if not (0 <= req.score <= 5):
        raise HTTPException(400, "score 必须在 0~5 范围内")
    ok = await update_substance_score(
        code=req.code.strip(),
        user_id=user["id"],
        score=req.score,
        note=req.note,
    )
    await repository.add_log(user["id"], user["username"], "substance_score", f"{req.code}:{req.score}")
    return {"ok": ok}


@router.get("/{code}")
async def get_substance(code: str, user: Annotated[dict, Depends(get_current_user)]):
    """读取某只股票的真受益核查报告 + 人工评分."""
    stocks = await repository.list_stocks(user["id"])
    stock = next((s for s in stocks if s["code"] == code.strip()), None)
    if not stock:
        raise HTTPException(404, "股票不在自选池中")
    return {
        "code": stock["code"],
        "name": stock.get("name", ""),
        "substance_score": stock.get("substance_score", 0),
        "substance_note": stock.get("substance_note") or "",
        "substance_analysis": stock.get("substance_analysis") or "",
        "substance_updated_at": (
            str(stock["substance_updated_at"]) if stock.get("substance_updated_at") else None
        ),
    }
