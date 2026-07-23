from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models.repo import backtest_runs as bt_runs_repo
from backend.models.repo import backtest_jobs_db
from backend.services.signal_engine_config import DEFAULT_SIGNAL_CONFIG
from backend.services import backtest_jobs
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import backend

_log = logging.getLogger(__name__)

_ZOMBIE_TIMEOUT_SEC = 20 * 60   # DB job running 但 >20min 无心跳 → 视作进程已中断
from backend.services.backtester_5m import (
    run_model_backtest, universe_codes, load_daily_many, MODEL_IDS, MODEL_NAMES,
)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# ── 模型回测页: 配参 + 点击回测(日线/5分钟 × 自选股/全市场) ──

@router.get("/models")
async def list_backtest_models(_: Annotated[dict, Depends(get_current_user)]):
    """可回测的买点模型清单 + 各自默认参数(数值/开关), 给前端渲染可改表单。"""
    out = []
    for mid in MODEL_IDS:
        params = {k: v for k, v in DEFAULT_SIGNAL_CONFIG.get(mid, {}).items()
                  if isinstance(v, (int, float, bool)) and k != "enabled"}
        out.append({"id": mid, "name": MODEL_NAMES[mid], "params": params})
    return {"ok": True, "models": out}


class ModelRunRequest(BaseModel):
    model_id: str
    scope: str = "pool"        # pool=自选股 | all=全市场
    koujing: str = "daily"     # daily=日线全天量口径(快) | 5m=5分钟真实可成交(慢)
    lookback_days: int = 182
    temp_config: dict | None = None   # 临时参数覆盖 {signal_id:{param:val}}, 不落库


@router.post("/model-run")
async def model_run(req: ModelRunRequest, user: Annotated[dict, Depends(get_current_user)]):
    """点击回测。日线+自选股 → 同步直接出结果; 其余(全市场/5分钟)→ 起后台任务返回 job_id 供轮询。"""
    if req.model_id not in MODEL_IDS:
        raise HTTPException(400, "未知模型")
    if req.koujing not in ("daily", "5m"):
        raise HTTPException(400, "koujing 只能 daily/5m")
    if req.scope not in ("pool", "all"):
        raise HTTPException(400, "scope 只能 pool/all")
    uid = user["id"]
    if (req.scope == "all" or req.koujing == "5m") and user.get("role") != "admin":
        raise HTTPException(403, "全市场或5分钟回测仅限管理员")
    if backtest_jobs.has_active_job(uid) or await backtest_jobs_db.has_active_job(uid):
        raise HTTPException(409, "当前用户已有回测任务运行中，请等待完成后再试")
    lookback = max(30, min(int(req.lookback_days), 1100))
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=lookback)).isoformat()
    spec = f"pool:{user['id']}" if req.scope == "pool" else "all"
    codes = await universe_codes(spec)
    if not codes:
        return {"ok": False, "msg": "范围内无可回测股票(需5分钟数据覆盖)"}

    window = {"start": start, "end": end}
    # 短任务(自选股 + 日线, ~30秒)走内存态后台任务, 现状不动。
    # 长任务(全市场 或 5分钟)走方案C: systemd-run 拉独立临时单元跑, 进度/结果落 DB,
    # 部署/重启主服务杀不死它。systemd 不可用/启动失败 → 回退到内存态(本地无 systemd 仍可用)。
    is_long = (req.scope == "all" or req.koujing == "5m")

    # 内存态后台任务工厂(短任务 + 长任务系统不支持时的回退路径共用)
    def _build_inproc():
        async def _do(cb):
            preloaded = None
            if req.scope == "pool":
                cb(0, len(codes), phase="准备数据", note="批量加载自选股日线…")
                preloaded = await load_daily_many(codes)
            res = await run_model_backtest(req.model_id, codes, start, end, req.temp_config,
                                           koujing=req.koujing, preloaded_daily=preloaded, progress_cb=cb)
            # 成功完成 → 自动落历史记录(失败不影响回测结果返回)
            try:
                await bt_runs_repo.save_run(uid, {
                    **res, "scope": req.scope, "koujing": req.koujing,
                    "lookback_days": lookback, "window_start": start, "window_end": end,
                    "params": (req.temp_config or {}).get(req.model_id, {}),
                })
            except Exception as e:  # noqa: BLE001
                _log.warning("回测历史记录保存失败: %s", e)
            return res
        return _do

    if not is_long:
        jid = backtest_jobs.new_job(len(codes), uid, meta={
            "model_id": req.model_id, "scope": req.scope, "koujing": req.koujing,
            "runner": "inproc"})
        backtest_jobs.launch(jid, _build_inproc())
        return {"ok": True, "status": "running", "job_id": jid, "total": len(codes), "window": window}

    # ── 长任务: DB job + systemd-run 独立单元 ──
    # 内存态那条只为生成短 id + 走 systemd 失败时回退用; meta.runner 控制 model-job 该读内存还是读 DB。
    jid = backtest_jobs.new_job(len(codes), uid, meta={
        "model_id": req.model_id, "scope": req.scope, "koujing": req.koujing,
        "runner": "systemd"})

    await backtest_jobs_db.create_job({
        "job_id": jid, "user_id": uid, "model_id": req.model_id,
        "scope": req.scope, "koujing": req.koujing, "lookback_days": lookback,
        "window_start": start, "window_end": end,
        "params": req.temp_config, "codes": codes, "runner": "systemd",
    })

    started = False
    systemd_run = shutil.which("systemd-run")
    if systemd_run:
        python_exe = sys.executable
        projroot = os.path.dirname(os.path.dirname(os.path.abspath(backend.__file__)))
        cmd = [
            systemd_run, f"--unit=cfzy-bt-{jid}", "--collect",
            f"--working-directory={projroot}",
            f"--setenv=PYTHONPATH={projroot}",
            python_exe, "-m", "backend.scripts.run_bt_job", jid,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                started = True
                _log.info("systemd-run 已拉起长回测单元 cfzy-bt-%s", jid)
            else:
                _log.warning("systemd-run 启动失败(rc=%s): %s | %s",
                             r.returncode, (r.stderr or "").strip(), (r.stdout or "").strip())
        except Exception as e:  # noqa: BLE001
            _log.warning("systemd-run 调用异常: %s", e)
    else:
        _log.warning("未找到 systemd-run, 长回测回退到进程内内存态(本地/无systemd环境)")

    if not started:
        # 回退: 改用内存态 launch 跑这个 jid; 内存 meta.runner 翻成 inproc(让 model-job 读内存那条),
        # DB 行也标 inproc 留痕。本地无 systemd / 生产 systemd 故障时仍能跑完。
        mj = backtest_jobs.get_job(jid, uid)
        if mj:
            mj.setdefault("meta", {})["runner"] = "inproc"
        try:
            await backtest_jobs_db.set_runner(jid, "inproc")
        except Exception as e:  # noqa: BLE001
            _log.warning("标记 runner=inproc 失败(忽略): %s", e)
        backtest_jobs.launch(jid, _build_inproc())

    return {"ok": True, "status": "running", "job_id": jid, "total": len(codes), "window": window}


@router.get("/model-job/{jid}")
async def model_job(jid: str, user: Annotated[dict, Depends(get_current_user)]):
    """轮询后台回测任务进度/结果。

    内存态 job(短任务 + 长任务回退态, meta.runner='inproc') → 读内存。
    内存态 meta.runner='systemd'(长任务走了独立单元) → 进度/结果只在 DB, 读 DB。
    内存态没有(主服务重启过, 内存丢了) → 读 DB。
    """
    uid = user["id"]
    j = backtest_jobs.get_job(jid, uid)
    if j and j.get("user_id") != uid:
        raise HTTPException(404, "任务不存在或已过期")
    if j and j.get("meta", {}).get("runner") != "systemd":
        return {"ok": True, "status": j["status"], "progress": j["progress"],
                "result": j["result"], "error": j["error"]}

    db = await backtest_jobs_db.get_job(jid, uid)
    if db and db.get("user_id") != uid:
        raise HTTPException(404, "任务不存在或已过期")
    if db:
        status = db["status"]
        progress = db["progress"] or {"done": 0, "total": 0, "phase": "排队中", "note": ""}
        # 僵尸保护: running 但 >20min 无心跳(进程崩了没写 error) → 报中断
        if status == "running" and _is_stale(db.get("updated_at")):
            return {"ok": True, "status": "error", "progress": progress,
                    "result": None, "error": "任务可能已中断(进程无心跳超时)"}
        return {"ok": True, "status": status, "progress": progress,
                "result": db["result"], "error": db["error"]}

    # 内存态是 systemd 占位但 DB 行已被 GC 删(极端) → 用内存占位兜底
    if j:
        return {"ok": True, "status": j["status"], "progress": j["progress"],
                "result": j["result"], "error": j["error"]}

    raise HTTPException(404, "任务不存在或已过期")


def _is_stale(updated_at) -> bool:
    """DB updated_at 距今是否超过僵尸超时。updated_at 为 MySQL naive datetime(服务器本地时区)。"""
    if not updated_at:
        return False
    try:
        if isinstance(updated_at, datetime):
            now = datetime.now()
            if updated_at.tzinfo is not None:
                now = datetime.now(timezone.utc)
            return (now - updated_at).total_seconds() > _ZOMBIE_TIMEOUT_SEC
    except Exception:  # noqa: BLE001
        return False
    return False


@router.get("/model-runs")
async def list_model_runs(user: Annotated[dict, Depends(get_current_user)], limit: int = 100):
    """本人模型回测历史(轻量列表, 不含逐笔明细)。"""
    runs = await bt_runs_repo.list_runs(user["id"], limit=max(1, min(int(limit), 300)))
    return {"ok": True, "runs": runs}


@router.get("/model-runs/{run_id}")
async def get_model_run(run_id: int, user: Annotated[dict, Depends(get_current_user)]):
    """单条历史全量(含月度 + 逐笔明细)。"""
    run = await bt_runs_repo.get_run(user["id"], run_id)
    if not run:
        raise HTTPException(404, "记录不存在")
    return {"ok": True, "run": run}


@router.delete("/model-runs/{run_id}")
async def delete_model_run(run_id: int, user: Annotated[dict, Depends(get_current_user)]):
    ok = await bt_runs_repo.delete_run(user["id"], run_id)
    return {"ok": ok}
