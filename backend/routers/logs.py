from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def get_logs(
    user: Annotated[dict, Depends(get_current_user)],
    page: int = 1,
    page_size: int = 50,
    action: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    query_user_id = None if user["role"] == "admin" else user["id"]
    offset = (page - 1) * page_size
    total = await repository.count_logs(query_user_id, action=action, keyword=keyword, date_from=date_from, date_to=date_to)
    logs = await repository.get_logs(query_user_id, page_size, offset, action=action, keyword=keyword, date_from=date_from, date_to=date_to)
    return {"total": total, "page": page, "page_size": page_size, "logs": logs}


@router.get("/actions")
async def get_log_actions(user: Annotated[dict, Depends(get_current_user)]):
    actions = await repository.get_log_actions()
    return {"actions": actions}
