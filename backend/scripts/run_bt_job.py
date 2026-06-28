# -*- coding: utf-8 -*-
"""模型回测长任务独立 runner (方案C) —— 由 systemd-run 拉的临时单元执行。

入口: python -m backend.scripts.run_bt_job <job_id>

从 cfzy_sys_backtest_jobs 读 job(必须 status='running'), 跑 run_model_backtest,
进度节流写回 DB(供前端 model-job 轮询), 成功落历史 + set_done, 失败 set_error。

关键: **绝不 import backend.main**(会触发整个 app/lifespan/scheduler/调度器), 只 import
init_db/close_db、run_model_backtest/load_daily_many、两个 repo。这样独立进程轻量、与主服务彻底解耦,
部署/重启主服务(KillMode=control-group)杀不到这个独立 systemd 单元。
"""
import asyncio
import logging
import sys
import time

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [bt_job] %(message)s")
_log = logging.getLogger("run_bt_job")

_PROGRESS_MIN_INTERVAL = 1.5   # 进度写库节流: 距上次写 <1.5s 且非收尾则跳过


async def _main(jid: str) -> int:
    from backend.models.database import init_db, close_db
    from backend.models.repo import backtest_jobs_db as jobs_db
    from backend.models.repo import backtest_runs as bt_runs_repo
    from backend.services.backtester_5m import run_model_backtest, load_daily_many

    await init_db()
    try:
        job = await jobs_db.get_job(jid)
        if not job:
            _log.warning("job %s 不存在, 退出", jid)
            return 0
        if job["status"] != "running":
            _log.info("job %s 状态=%s(非 running), 跳过", jid, job["status"])
            return 0

        model_id = job["model_id"]
        scope = job["scope"]
        koujing = job["koujing"]
        temp_config = job["params"] or None
        codes = job["codes"] or []
        start = job["window_start"]
        end = job["window_end"]
        uid = job["user_id"]
        lookback_days = job["lookback_days"]

        # 进度心跳(节流): done==total 或距上次写 >=1.5s 才落库
        _last = {"t": 0.0}

        async def progress_cb(done, total, phase=None, note=None):
            now = time.time()
            if done < total and (now - _last["t"]) < _PROGRESS_MIN_INTERVAL:
                return
            _last["t"] = now
            try:
                await jobs_db.update_progress(jid, {
                    "done": done, "total": total,
                    "phase": phase or "", "note": note or "",
                })
            except Exception as e:  # noqa: BLE001
                _log.warning("写进度失败(忽略): %s", e)

        preloaded = None
        if scope == "pool" and codes:
            await jobs_db.update_progress(jid, {
                "done": 0, "total": len(codes),
                "phase": "准备数据", "note": "批量加载自选股日线…"})
            preloaded = await load_daily_many(codes)

        res = await run_model_backtest(
            model_id, codes, start, end, temp_config,
            koujing=koujing, preloaded_daily=preloaded, progress_cb=progress_cb)

        # 成功 → 自动落历史(失败只 log, 不影响 set_done)
        try:
            await bt_runs_repo.save_run(uid, {
                **res, "scope": scope, "koujing": koujing,
                "lookback_days": lookback_days,
                "window_start": start, "window_end": end,
                "params": (temp_config or {}).get(model_id, {}),
            })
        except Exception as e:  # noqa: BLE001
            _log.warning("回测历史记录保存失败: %s", e)

        await jobs_db.set_done(jid, res)
        _log.info("job %s 完成", jid)
        return 0
    except Exception as e:  # noqa: BLE001
        _log.exception("job %s 失败: %s", jid, e)
        try:
            await jobs_db.set_error(jid, str(e))
        except Exception as e2:  # noqa: BLE001
            _log.warning("写 error 失败: %s", e2)
        return 1
    finally:
        await close_db()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m backend.scripts.run_bt_job <job_id>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(_main(sys.argv[1])))
