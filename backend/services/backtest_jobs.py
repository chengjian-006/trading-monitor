# -*- coding: utf-8 -*-
"""模型回测异步任务 —— 内存 job store(单进程 FastAPI 用)。

「点击回测」里慢的组合(全市场 / 5分钟口径)走后台任务: 创建 job → asyncio 后台跑 → 前端轮询进度/结果。
job 存内存(重启即失, 对按需回测可接受)。带过期清理防泄漏。
"""
import asyncio
import time
import uuid

_JOBS: dict[str, dict] = {}
_TTL = 3600          # job 完成后保留 1 小时
_MAX = 100           # 最多保留 job 数


def _gc():
    now = time.time()
    dead = [k for k, v in _JOBS.items()
            if v["status"] in ("done", "error") and now - v.get("ended_at", now) > _TTL]
    for k in dead:
        _JOBS.pop(k, None)
    if len(_JOBS) > _MAX:  # 超量则删最旧的已完成 job
        done = sorted((k for k, v in _JOBS.items() if v["status"] in ("done", "error")),
                      key=lambda k: _JOBS[k].get("ended_at", 0))
        for k in done[: len(_JOBS) - _MAX]:
            _JOBS.pop(k, None)


def new_job(total: int, meta: dict | None = None) -> str:
    _gc()
    jid = uuid.uuid4().hex[:12]
    _JOBS[jid] = {"status": "running",
                  "progress": {"done": 0, "total": total, "phase": "排队中", "note": ""},
                  "result": None, "error": None, "meta": meta or {}, "ended_at": None}
    return jid


def get_job(jid: str) -> dict | None:
    return _JOBS.get(jid)


def _progress(jid: str):
    def cb(done: int, total: int, phase: str | None = None, note: str | None = None):
        j = _JOBS.get(jid)
        if j:
            prev = j["progress"]
            j["progress"] = {
                "done": done, "total": total,
                "phase": phase if phase is not None else prev.get("phase", ""),
                "note": note if note is not None else prev.get("note", ""),
            }
    return cb


def launch(jid: str, coro_factory):
    """coro_factory(progress_cb) -> coroutine; 后台跑, 完成写 result/error。"""
    async def _run():
        try:
            res = await coro_factory(_progress(jid))
            j = _JOBS.get(jid)
            if j:
                j["result"] = res
                j["status"] = "done"
                j["ended_at"] = time.time()
        except Exception as e:  # noqa: BLE001
            j = _JOBS.get(jid)
            if j:
                j["error"] = str(e)
                j["status"] = "error"
                j["ended_at"] = time.time()
    asyncio.create_task(_run())
