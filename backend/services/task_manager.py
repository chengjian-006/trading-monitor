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


_STAGGER_SEC = 7          # 补跑任务之间的错峰间隔, 防启动瞬间一拥而上
_stagger_slot = {"n": 0}


def _next_run_for(task: dict, secs: int):
    """interval 任务的首次触发时刻 = 上次真实运行 + 间隔; 已超期则尽快补跑(错峰)。

    不这样做的话, 每次重启都把计时归零 —— 重启比间隔频繁时任务永远饿死(见 register_task
    注释里的实测)。返回 None 表示交回 APScheduler 默认行为。
    """
    from datetime import datetime, timedelta
    last = task.get("last_run_at")
    now = datetime.now()
    if not isinstance(last, datetime):
        return None                      # 从没跑过 → 用默认(启动+间隔), 不抢启动资源
    due = last + timedelta(seconds=secs)
    if due > now:
        return due                       # 未到点: 按原节奏接续, 重启不重置
    # 已超期 → 尽快补跑, 但错峰排开, 避免一次重启后几十个任务同时冲
    _stagger_slot["n"] += 1
    return now + timedelta(seconds=_STAGGER_SEC * _stagger_slot["n"])


def register_task(task: dict):
    job_id = task["job_id"]
    handler_name = task["handler"]
    stype = task["schedule_type"]
    sconfig = task["schedule_config"]
    if isinstance(sconfig, str):
        sconfig = json.loads(sconfig)

    fn = partial(wrapped_handler, job_id=job_id, handler_name=handler_name)

    if stype == "interval":
        secs = int(sconfig.get("seconds", 30))
        scheduler.add_job(
            fn, "interval",
            seconds=secs,
            id=job_id, replace_existing=True,
            max_instances=1,
            misfire_grace_time=max(secs, 10),
            # v1.7.714 修「高频重启饿死 interval 任务」: APScheduler 默认把首次触发排在
            # "启动 + 间隔"之后。若重启比间隔更频繁, 任务永远轮不到 —— 实测 0719 晚
            # 20:00 后部署 16 次(最短间隔 3 分钟), cross_check(60min)自 20:37 起、
            # stock_tags_refresh(20min)自 22:28 起**一次都没跑**。这与台账里"模型胜率
            # 静默停写 9 天"是同一个根因(服务高频重启杀长任务)。
            # 改为按**上次真实运行时刻**接续排期: 已超期的立刻补跑, 未超期的按原节奏走,
            # 重启不再重置计时。
            next_run_time=_next_run_for(task, secs),
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
