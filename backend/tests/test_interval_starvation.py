# -*- coding: utf-8 -*-
"""interval 任务「高频重启饿死」修复 (v1.7.714) — 纯函数, 不连库不起调度器.

实测背景: 0719 晚部署 16 次(最短间隔 3 分钟)。APScheduler 默认把 interval 任务的首次
触发排在"启动 + 间隔"之后, 于是重启比间隔频繁时任务**永远轮不到**:
  cross_check(60min)  自 20:37 起没跑过
  stock_tags_refresh(20min) 自 22:28 起没跑过
这与台账里"模型胜率静默停写 9 天"是同一根因(服务高频重启杀长任务)。
修法: 按上次真实运行时刻接续排期, 重启不再重置计时。
"""
from datetime import datetime, timedelta

from backend.services import task_manager as tm


def _reset_stagger():
    tm._stagger_slot["n"] = 0


def test_never_ran_uses_default():
    """从没跑过的任务交回默认行为, 不抢启动资源。"""
    _reset_stagger()
    assert tm._next_run_for({"last_run_at": None}, 1200) is None
    assert tm._next_run_for({}, 1200) is None


def test_not_due_yet_keeps_original_cadence():
    """未到点: 按 上次运行+间隔 接续 —— 重启不重置计时, 这是修复的核心。"""
    _reset_stagger()
    last = datetime.now() - timedelta(seconds=300)
    nxt = tm._next_run_for({"last_run_at": last}, 1200)
    assert nxt == last + timedelta(seconds=1200)
    assert nxt > datetime.now(), "还没到点, 不该立刻跑"


def test_overdue_runs_soon():
    """已超期: 尽快补跑, 而不是再等一个完整间隔(否则频繁重启下永远跑不了)。"""
    _reset_stagger()
    last = datetime.now() - timedelta(seconds=5000)
    nxt = tm._next_run_for({"last_run_at": last}, 1200)
    assert nxt is not None
    delay = (nxt - datetime.now()).total_seconds()
    assert 0 < delay < 60, f"超期任务应尽快补跑, 实际延迟 {delay}s"


def test_overdue_tasks_are_staggered():
    """多个超期任务错峰排开, 避免一次重启后几十个任务同时冲。"""
    _reset_stagger()
    last = datetime.now() - timedelta(seconds=9999)
    times = [tm._next_run_for({"last_run_at": last}, 600) for _ in range(4)]
    deltas = [(times[i + 1] - times[i]).total_seconds() for i in range(3)]
    assert all(d >= tm._STAGGER_SEC - 1 for d in deltas), f"未错峰: {deltas}"


def test_restart_storm_does_not_starve():
    """回归实测场景: 20分钟间隔的任务, 在每3分钟重启一次下仍能跑起来。

    旧行为 = 每次重启都排到"现在+20分", 而 3 分钟后又重启 → 永远不触发。
    新行为 = 依据上次真实运行时刻, 超期就立刻补跑。
    """
    _reset_stagger()
    last_real_run = datetime.now() - timedelta(minutes=105)   # 实测 stock_tags_refresh
    for _ in range(10):                                        # 模拟连续 10 次重启
        nxt = tm._next_run_for({"last_run_at": last_real_run}, 1200)
        assert (nxt - datetime.now()).total_seconds() < 120, "重启后仍应尽快补跑"


# ── 体检项: 刚重启不该怪任务没跑 ──

def test_health_check_has_proc_start_anchor():
    from backend.services import health_checks as hc
    assert isinstance(hc._PROC_START, datetime)
    assert (datetime.now() - hc._PROC_START).total_seconds() < 3600, \
        "_PROC_START 应是本进程启动时刻(模块导入时捕获)"
