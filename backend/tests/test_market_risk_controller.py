# -*- coding: utf-8 -*-
"""市场风险状态机 + 历史指标口径修复 (v1.7.570). 纯函数, 不连库不联网。"""
from backend.services.market_risk_controller import _run_state_machine, _hist_indicators


# ── RED→YELLOW 降级(补推送分支的前提: 该迁移确实会发生) ──

def test_state_machine_red_to_yellow_on_recovery():
    """RED 态下广度>25%、涨跌比>40%、5日均收益>0 → 降级 YELLOW(触发新的降级卡)。"""
    today = {"advance_ratio": 60.0, "avg_ret_ma5": 0.5, "low52_ratio": 2.0,
             "zha_rate": 10.0, "breadth_ma20": 50.0}
    assert _run_state_machine("RED", today, breadth=50.0) == "YELLOW"


def test_state_machine_red_stays_red_when_still_weak():
    today = {"advance_ratio": 30.0, "avg_ret_ma5": -1.5, "low52_ratio": 20.0,
             "zha_rate": 40.0, "breadth_ma20": 18.0}
    assert _run_state_machine("RED", today, breadth=18.0) == "RED"


def test_state_machine_red_needs_all_three_to_exit():
    """只满足部分回暖条件(涨跌比够但广度不够)仍留 RED。"""
    today = {"advance_ratio": 60.0, "avg_ret_ma5": 0.5, "low52_ratio": 2.0,
             "zha_rate": 10.0, "breadth_ma20": 20.0}
    assert _run_state_machine("RED", today, breadth=20.0) == "RED"   # 广度20<25


# ── 历史指标: 脏 low=0 行不再炸整轮 EOD(v1.7.570 crash guard) ──

def test_hist_indicators_no_crash_on_zero_low_rows():
    """某票 low 全为 0(脏数据)时, 原 min([]) 抛 ValueError 会炸掉整轮评估; 修复后安全跳过。"""
    rows = [
        ("000001", "2026-06-10", 10.0, 10.5, 0.0),
        ("000001", "2026-06-11", 10.2, 10.6, 0.0),
        ("000001", "2026-06-12", 10.1, 10.3, 0.0),
    ]
    out = _hist_indicators(rows, need_days=6)   # 不应抛异常
    assert isinstance(out, list)                # 小样本(n<1000)输出为空, 但不炸
