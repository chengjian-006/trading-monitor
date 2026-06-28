from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import get_current_user
from backend.services import api_health

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/external")
async def get_external_api_health(
    _: Annotated[dict, Depends(get_current_user)],
):
    state = api_health.get_health_state()
    # v1.7.x: 把调度任务"连续失败 > 0"的也带回前端, 顶栏 popover 一站式展示
    state["failing_tasks"] = await api_health.get_failing_tasks_async()
    return {
        **state,
        "usage_labels": api_health.get_usage_labels(),
    }


@router.post("/external/recheck")
async def recheck_external_api_health(
    _: Annotated[dict, Depends(get_current_user)],
):
    await api_health.check_all_api_health()
    return api_health.get_health_state()
