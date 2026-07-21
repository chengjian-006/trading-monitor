"""藏龙岛观点 API (v1.7.x).

  GET  /api/lark-coach/posts   分页取藏龙岛消息(按发布时间倒序)
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/lark-coach", tags=["lark-coach"])


@router.get("/posts")
async def list_posts(_user: Annotated[dict, Depends(get_current_user)],
                     limit: int = 100, offset: int = 0):
    limit = max(1, min(limit, 300))
    offset = max(0, offset)
    rows = await repository.list_coach_posts(limit=limit, offset=offset)
    posts = [{
        "id": r["id"],
        "message_id": r["message_id"],
        "coach_name": r["coach_name"],
        "posted_at": r["posted_at"].strftime("%Y-%m-%d %H:%M") if r.get("posted_at") else "",
        "content": r["content"],
        "msg_type": r["msg_type"],
    } for r in rows]
    return {"posts": posts}
