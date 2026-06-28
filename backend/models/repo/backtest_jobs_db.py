"""模型回测长任务 DB 态 job — cfzy_sys_backtest_jobs (方案C).

全市场/5分钟回测改跑在独立 systemd-run 临时单元里(部署/重启主服务杀不死), 进度/结果落此表,
前端走 model-job 轮询。短任务(自选+日线)仍走内存态 backtest_jobs, 不入此表。

字段约定:
  params_json  = json.dumps(temp_config or {})  —— 临时参数覆盖
  codes_json   = json.dumps(codes)              —— 本次回测范围代码
  progress_json= {done,total,phase,note}        —— 进度心跳, runner 节流写入
  result_json  = run_model_backtest 返回(可能上 MB, 故用 MEDIUMTEXT)
"""
import json

from backend.models.repo._db import _execute, _fetchone


async def create_job(job: dict) -> None:
    """新建一行(status='running')。job 含 job_id/user_id/model_id/scope/koujing/
    lookback_days/window_start/window_end/params(=temp_config)/codes/runner。"""
    sql = (
        "INSERT INTO cfzy_sys_backtest_jobs "
        "(job_id, user_id, model_id, scope, koujing, lookback_days, "
        " window_start, window_end, params_json, codes_json, status, runner) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running',%s)"
    )
    args = (
        job["job_id"], int(job["user_id"]), job["model_id"], job["scope"],
        job["koujing"], int(job["lookback_days"]),
        job["window_start"], job["window_end"],
        json.dumps(job.get("params") or {}, ensure_ascii=False),
        json.dumps(job.get("codes") or [], ensure_ascii=False),
        job.get("runner", "systemd"),
    )
    await _execute(sql, args)


async def get_job(job_id: str) -> dict | None:
    """取一行, JSON 字段反序列化; 带出 updated_at(僵尸保护判心跳用)。"""
    r = await _fetchone(
        "SELECT job_id, user_id, model_id, scope, koujing, lookback_days, "
        "window_start, window_end, params_json, codes_json, status, "
        "progress_json, result_json, error, runner, created_at, updated_at "
        "FROM cfzy_sys_backtest_jobs WHERE job_id=%s", (job_id,))
    if not r:
        return None
    return {
        "job_id": r["job_id"], "user_id": r["user_id"], "model_id": r["model_id"],
        "scope": r["scope"], "koujing": r["koujing"],
        "lookback_days": r["lookback_days"],
        "window_start": r["window_start"], "window_end": r["window_end"],
        "params": _loads(r["params_json"], {}),
        "codes": _loads(r["codes_json"], []),
        "status": r["status"],
        "progress": _loads(r["progress_json"], None),
        "result": _loads(r["result_json"], None),
        "error": r["error"] or "",
        "runner": r["runner"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


async def update_progress(job_id: str, progress: dict) -> None:
    """更新进度心跳(顺带刷新 updated_at, ON UPDATE 自动)。"""
    await _execute(
        "UPDATE cfzy_sys_backtest_jobs SET progress_json=%s, "
        "updated_at=CURRENT_TIMESTAMP WHERE job_id=%s",
        (json.dumps(progress or {}, ensure_ascii=False), job_id))


async def set_done(job_id: str, result: dict) -> None:
    await _execute(
        "UPDATE cfzy_sys_backtest_jobs SET status='done', result_json=%s, "
        "updated_at=CURRENT_TIMESTAMP WHERE job_id=%s",
        (json.dumps(result or {}, ensure_ascii=False), job_id))


async def set_error(job_id: str, msg: str) -> None:
    await _execute(
        "UPDATE cfzy_sys_backtest_jobs SET status='error', error=%s, "
        "updated_at=CURRENT_TIMESTAMP WHERE job_id=%s",
        ((msg or "")[:500], job_id))


async def set_runner(job_id: str, runner: str) -> None:
    """systemd 启动失败回退内存态时, 把 runner 标记成 inproc(留痕用)。"""
    await _execute(
        "UPDATE cfzy_sys_backtest_jobs SET runner=%s WHERE job_id=%s",
        (runner, job_id))


async def delete_job(job_id: str) -> None:
    await _execute("DELETE FROM cfzy_sys_backtest_jobs WHERE job_id=%s", (job_id,))


async def gc_old() -> None:
    """清理 1 小时前的 done/error 行。不动 running(systemd 任务可能还在跑)。"""
    await _execute(
        "DELETE FROM cfzy_sys_backtest_jobs WHERE status IN ('done','error') "
        "AND updated_at < NOW() - INTERVAL 1 HOUR")


def _loads(s, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default
