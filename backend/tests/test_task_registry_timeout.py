"""任务执行包装器统一超时保护测试。

wrapped_handler 用 asyncio.wait_for 包住 handler:
- 超时 = 一次失败, 走既有 logger.exception / 失败计数 / 告警链路
- 默认 30 分钟; LONG_TASK_TIMEOUTS 中的已知长任务 3 小时
- 超时值可注入 (wrapped_handler(timeout=...)) / 可查表 (get_task_timeout)
"""
import asyncio

from backend.services import task_registry
from backend.services.task_registry import (
    DEFAULT_TASK_TIMEOUT,
    LONG_TASK_TIMEOUT,
    LONG_TASK_TIMEOUTS,
    TASK_HANDLERS,
    get_task_timeout,
    wrapped_handler,
)


class _StatusRecorder:
    """替身 repository.update_task_run_status, 记录落库调用。"""

    def __init__(self, count: int = 1):
        self.calls = []
        self.count = count  # 模拟 consecutive_failures 返回值

    async def __call__(self, job_id, run_at, status, err_msg=""):
        self.calls.append({"job_id": job_id, "status": status, "err_msg": err_msg})
        return self.count


async def test_stuck_handler_times_out_and_marked_error(monkeypatch):
    """睡过超时值的假 handler 被 wait_for 中止, 记为一次失败(error 落库)。"""
    from backend.models import repository

    recorder = _StatusRecorder()
    monkeypatch.setattr(repository, "update_task_run_status", recorder)

    async def stuck():
        await asyncio.sleep(5)

    monkeypatch.setitem(task_registry.TASK_HANDLERS, "stuck_handler", stuck)

    await wrapped_handler("job_stuck", "stuck_handler", timeout=0.1)

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["job_id"] == "job_stuck"
    assert call["status"] == "error"
    # 异常信息可读: 含任务名 + 超时秒数 + "超时"
    assert "超时" in call["err_msg"]
    assert "stuck_handler" in call["err_msg"]
    assert "0.1" in call["err_msg"]
    assert "TimeoutError" in call["err_msg"]


async def test_normal_handler_unaffected(monkeypatch):
    """正常 handler 在超时窗口内完成, 照常记 success。"""
    from backend.models import repository

    recorder = _StatusRecorder()
    monkeypatch.setattr(repository, "update_task_run_status", recorder)

    ran = {"flag": False}

    async def quick():
        ran["flag"] = True

    monkeypatch.setitem(task_registry.TASK_HANDLERS, "quick_handler", quick)

    await wrapped_handler("job_quick", "quick_handler", timeout=5)

    assert ran["flag"] is True
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["status"] == "success"


async def test_timeout_walks_existing_alert_path(monkeypatch):
    """超时作为一种失败, 走既有连续失败告警链路(不另起炉灶)。"""
    from backend.models import repository

    recorder = _StatusRecorder(count=3)  # 模拟连续失败达到阈值
    monkeypatch.setattr(repository, "update_task_run_status", recorder)

    alert_calls = []

    async def fake_alert(job_id, handler_name, count, err_msg):
        alert_calls.append((job_id, handler_name, count, err_msg))

    monkeypatch.setattr(task_registry, "_maybe_alert_task_failure", fake_alert)

    async def stuck():
        await asyncio.sleep(5)

    monkeypatch.setitem(task_registry.TASK_HANDLERS, "stuck_handler", stuck)

    await wrapped_handler("job_stuck", "stuck_handler", timeout=0.1)

    assert len(alert_calls) == 1
    job_id, handler_name, count, err_msg = alert_calls[0]
    assert job_id == "job_stuck"
    assert count == 3
    assert "超时" in err_msg


def test_long_task_timeouts_lookup():
    """LONG_TASK_TIMEOUTS 里的名字拿到长超时(3h或通宵6h), 其余拿默认值。"""
    from backend.services.task_registry import OVERNIGHT_TIMEOUT
    assert DEFAULT_TASK_TIMEOUT == 30 * 60
    assert LONG_TASK_TIMEOUT == 3 * 60 * 60
    assert OVERNIGHT_TIMEOUT == 6 * 60 * 60

    # 周回测/前向分布/收益回填 = 3h; 全市场5分钟逐票的胜率重算与5分钟追加 = 通宵6h
    for name in ("run_model_backtest_weekly", "refresh_holding_state_fwd",
                 "backfill_signal_outcomes"):
        assert get_task_timeout(name) == LONG_TASK_TIMEOUT
    for name in ("refresh_model_winrate", "append_kline_5m"):
        assert get_task_timeout(name) == OVERNIGHT_TIMEOUT

    # 普通任务默认 30 分钟
    assert get_task_timeout("scan_stock_pool") == DEFAULT_TASK_TIMEOUT
    assert get_task_timeout("refresh_quotes") == DEFAULT_TASK_TIMEOUT


def test_long_task_names_exist_in_registry():
    """防拼错: 长任务表里的每个名字必须真实注册, 否则超时配置悄悄失效。"""
    for name in LONG_TASK_TIMEOUTS:
        assert name in TASK_HANDLERS, f"LONG_TASK_TIMEOUTS 含未注册 handler: {name}"


async def test_task_skipped_marked_skipped_not_success(monkeypatch):
    """handler 抛 TaskSkipped(非交易日等) → 落 'skipped', 不是 'success' 也不是 'error'。

    这是"周末空跑清零失败计数、掩盖工作日真实失败"观测事故的护栏: 'skipped' 不得复用
    'success' 分支(那会清零 consecutive_failures)。"""
    from backend.models import repository
    from backend.core.task_signals import TaskSkipped

    recorder = _StatusRecorder()
    monkeypatch.setattr(repository, "update_task_run_status", recorder)

    async def skipper():
        raise TaskSkipped("非交易日")

    monkeypatch.setitem(task_registry.TASK_HANDLERS, "skipper_handler", skipper)

    await wrapped_handler("job_skip", "skipper_handler", timeout=5)

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["status"] == "skipped"
    assert recorder.calls[0]["job_id"] == "job_skip"


async def test_task_skipped_does_not_reach_failure_alert(monkeypatch):
    """跳过不得走失败告警链路(跳过≠失败)。"""
    from backend.models import repository
    from backend.core.task_signals import TaskSkipped

    monkeypatch.setattr(repository, "update_task_run_status", _StatusRecorder(count=3))
    alert_calls = []

    async def fake_alert(job_id, handler_name, count, err_msg):
        alert_calls.append(job_id)

    monkeypatch.setattr(task_registry, "_maybe_alert_task_failure", fake_alert)

    async def skipper():
        raise TaskSkipped("非交易日")

    monkeypatch.setitem(task_registry.TASK_HANDLERS, "skipper_handler", skipper)
    await wrapped_handler("job_skip", "skipper_handler", timeout=5)

    assert alert_calls == []
