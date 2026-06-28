"""Load scheduled tasks from DB and manage their APScheduler lifecycle."""

import json
import logging
from functools import partial

from backend.core.scheduler import scheduler
from backend.models import repository
from backend.services.task_registry import wrapped_handler, TASK_HANDLERS

logger = logging.getLogger(__name__)


async def seed_default_tasks():
    count = await repository.count_scheduled_tasks()
    if count > 0:
        return
    from backend.models.database import _seed_scheduled_tasks, get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        await _seed_scheduled_tasks(conn)


async def load_and_register_all_tasks():
    tasks = await repository.list_scheduled_tasks()
    registered = 0
    db_handlers: set[str] = set()
    for task in tasks:
        db_handlers.add(task["handler"])
        if not task["enabled"]:
            continue
        if task["handler"] not in TASK_HANDLERS:
            logger.warning(f"Unknown handler '{task['handler']}' for job '{task['job_id']}', skipping")
            continue
        register_task(task)
        registered += 1
    logger.info(f"Loaded {registered}/{len(tasks)} scheduled tasks from DB")

    # v1.7.x 启动自检: 双向对账 DB scheduled_tasks <-> TASK_HANDLERS, 暴露孤儿
    registry_handlers = set(TASK_HANDLERS.keys())
    orphan_in_db = db_handlers - registry_handlers       # DB 注册了但 code 没 handler
    unused_in_code = registry_handlers - db_handlers     # code 写了 handler 但 DB 没任务在用
    if orphan_in_db:
        logger.warning(
            f"[task_audit] DB 里有 {len(orphan_in_db)} 个 handler 在 TASK_HANDLERS 找不到 "
            f"(孤儿任务, 永远跑不起来): {sorted(orphan_in_db)}"
        )
    if unused_in_code:
        logger.info(
            f"[task_audit] TASK_HANDLERS 里有 {len(unused_in_code)} 个 handler 没任何 DB 任务在用 "
            f"(死代码或待启用): {sorted(unused_in_code)}"
        )


def register_task(task: dict):
    job_id = task["job_id"]
    handler_name = task["handler"]
    stype = task["schedule_type"]
    sconfig = task["schedule_config"]
    if isinstance(sconfig, str):
        sconfig = json.loads(sconfig)

    fn = partial(wrapped_handler, job_id=job_id, handler_name=handler_name)

    if stype == "interval":
        scheduler.add_job(
            fn, "interval",
            seconds=sconfig.get("seconds", 30),
            id=job_id, replace_existing=True,
            max_instances=1,
            misfire_grace_time=max(sconfig.get("seconds", 30), 10),
        )
    elif stype == "cron":
        scheduler.add_job(
            fn, "cron",
            day_of_week=sconfig.get("day_of_week", "*"),   # 不填=每天(向后兼容); "sat"=每周六
            hour=sconfig.get("hour", 0),
            minute=sconfig.get("minute", 0),
            id=job_id, replace_existing=True,
            misfire_grace_time=60,
        )
    else:
        logger.warning(f"Unknown schedule_type '{stype}' for job '{job_id}'")


def unregister_task(job_id: str):
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def reschedule_task(task: dict):
    unregister_task(task["job_id"])
    if task.get("enabled", True):
        register_task(task)
