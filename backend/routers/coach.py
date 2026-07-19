from typing import Annotated
from fastapi import APIRouter, Depends, Query
from backend.core.auth import get_current_user
from backend.services.ai_advisor import trade_coach

router = APIRouter(prefix="/api/coach", tags=["coach"])


@router.get("/report")
async def get_report(
    user: Annotated[dict, Depends(get_current_user)],
    start: str = Query(...), end: str = Query(...),
):
    return await trade_coach.generate_coach_report(user["id"], start, end)
