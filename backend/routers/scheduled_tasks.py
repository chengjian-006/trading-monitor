import json
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.auth import require_admin
from backend.core.scheduler import scheduler
from backend.models import repository
from backend.services import task_manager
from backend.services.task_registry import wrapped_handler, TASK_HANDLERS

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled-tasks"])


class TaskUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule_config: Optional[dict] = None


class TaskToggleRequest(BaseModel):
    enabled: bool


@router.get("")
async def list_tasks(_: Annotated[dict, Depends(require_admin)]):
    tasks = await repository.list_scheduled_tasks()
    for t in tasks:
        if isinstance(t["schedule_config"], str):
            t["schedule_config"] = json.loads(t["schedule_config"])
        t["enabled"] = bool(t["enabled"])
        job = scheduler.get_job(t["job_id"])
        t["running"] = job is not None
        t["next_run_at"] = str(job.next_run_time) if job and job.next_run_time else None
    return tasks


@router.put("/{job_id}")
async def update_task(job_id: str, req: TaskUpdateRequest, admin: Annotated[dict, Depends(require_admin)]):
    task = await repository.get_scheduled_task(job_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True}

    old_values = {k: task[k] for k in updates}
    if "schedule_config" in old_values and isinstance(old_values["schedule_config"], str):
        old_values["schedule_config"] = json.loads(old_values["schedule_config"])

    await repository.update_scheduled_task(job_id, **updates)

    if task["enabled"]:
        updated = await repository.get_scheduled_task(job_id)
        task_manager.reschedule_task(updated)

    await repository.add_log(
        admin["id"], admin["username"], "update_task", job_id,
        old_value=old_values, new_value=updates,
    )
    return {"ok": True}


@router.post("/{job_id}/toggle")
async def toggle_task(job_id: str, req: TaskToggleRequest, admin: Annotated[dict, Depends(require_admin)]):
    task = await repository.get_scheduled_task(job_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    await repository.toggle_scheduled_task(job_id, req.enabled)

    if req.enabled:
        updated = await repository.get_scheduled_task(job_id)
        task_manager.register_task(updated)
    else:
        task_manager.unregister_task(job_id)

    action = "enable_task" if req.enabled else "disable_task"
    await repository.add_log(admin["id"], admin["username"], action, job_id)
    return {"ok": True}


@router.post("/{job_id}/trigger")
async def trigger_task(job_id: str, admin: Annotated[dict, Depends(require_admin)]):
    task = await repository.get_scheduled_task(job_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    handler_name = task["handler"]
    if handler_name not in TASK_HANDLERS:
        raise HTTPException(400, f"未知处理函数: {handler_name}")

    await repository.add_log(admin["id"], admin["username"], "trigger_task", job_id)
    await wrapped_handler(job_id, handler_name)
    return {"ok": True}
