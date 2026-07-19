"""调度任务 CRUD + 运行状态更新 - cfzy_sys_scheduled_tasks 表."""
from backend.models.repo._db import _execute, _fetchall, _fetchone


async def list_scheduled_tasks() -> list[dict]:
    return await _fetchall("SELECT * FROM cfzy_sys_scheduled_tasks ORDER BY id")


async def get_scheduled_task(job_id: str) -> dict | None:
    return await _fetchone("SELECT * FROM cfzy_sys_scheduled_tasks WHERE job_id = %s", (job_id,))


async def update_scheduled_task(job_id: str, **kwargs):
    import json
    allowed = {"name", "description", "schedule_type", "schedule_config", "handler", "enabled"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return
    if "schedule_config" in fields and isinstance(fields["schedule_config"], dict):
        fields["schedule_config"] = json.dumps(fields["schedule_config"])
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values()) + [job_id]
    await _execute(f"UPDATE cfzy_sys_scheduled_tasks SET {sets} WHERE job_id = %s", tuple(vals))


async def toggle_scheduled_task(job_id: str, enabled: bool):
    await _execute(
        "UPDATE cfzy_sys_scheduled_tasks SET enabled = %s WHERE job_id = %s",
        (int(enabled), job_id),
    )


async def update_task_run_status(job_id: str, last_run_at, last_status: str, error_msg: str = "") -> int:
    """更新任务运行状态; 成功时清零 consecutive_failures, 失败时 +1。返回新的 consecutive_failures。

    'skipped'(非交易日等主动跳过): 只更 last_run_at + 状态, 不动 consecutive_failures / last_error_msg
    —— 防止周末空跑把工作日真实失败的计数和错误信息抹掉(静默失败掩盖事故的观测根因)。"""
    if last_status == "skipped":
        await _execute(
            "UPDATE cfzy_sys_scheduled_tasks SET last_run_at = %s, last_status = %s "
            "WHERE job_id = %s",
            (last_run_at, last_status, job_id),
        )
        row = await _fetchone(
            "SELECT consecutive_failures FROM cfzy_sys_scheduled_tasks WHERE job_id = %s",
            (job_id,),
        )
        return int(row["consecutive_failures"]) if row else 0
    if last_status == "success":
        await _execute(
            "UPDATE cfzy_sys_scheduled_tasks SET last_run_at = %s, last_status = %s, "
            "consecutive_failures = 0, last_error_msg = '' WHERE job_id = %s",
            (last_run_at, last_status, job_id),
        )
        return 0
    await _execute(
        "UPDATE cfzy_sys_scheduled_tasks SET last_run_at = %s, last_status = %s, "
        "consecutive_failures = consecutive_failures + 1, last_error_msg = %s WHERE job_id = %s",
        (last_run_at, last_status, (error_msg or "")[:500], job_id),
    )
    row = await _fetchone(
        "SELECT consecutive_failures FROM cfzy_sys_scheduled_tasks WHERE job_id = %s",
        (job_id,),
    )
    return int(row["consecutive_failures"]) if row else 0


async def count_scheduled_tasks() -> int:
    row = await _fetchone("SELECT COUNT(*) AS cnt FROM cfzy_sys_scheduled_tasks")
    return row["cnt"] if row else 0
