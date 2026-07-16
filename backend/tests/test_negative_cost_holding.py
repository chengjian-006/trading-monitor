# -*- coding: utf-8 -*-
"""摊薄成本≤0 持仓仍保留非成本类卖点保护 (v1.7.567 修复).

背景: v1.7.535 成本口径改摊薄后, 高抛低吸落袋超额的持仓成本可为负(holdings.py 明确"可为负")。
旧 is_holding=entry_cost>0 判定把这类真实持仓当"非持仓", 静默丢掉跌破MA5/10/20、弱势极限T+15清仓
等不依赖成本的卖点保护。修复=持仓判定改"entry_cost 非 None"(净持股>0 即持仓)。
不连库、不打外网。
"""
import numpy as np
import pandas as pd

from backend.services.signal_engine import _detect_short_signals, get_merged_config


def _make_indicator_df() -> pd.DataFrame:
    """3 根合成日K(已算好指标): 末根收盘向下击穿 MA10(未破 MA5/MA20), 昨日尚未破 → 新鲜击穿。"""
    return pd.DataFrame({
        "date": ["2026-06-09", "2026-06-10", "2026-06-11"],
        "open":  [10.0, 10.0, 9.8],
        "high":  [10.2, 10.1, 9.9],
        "low":   [9.9, 9.8, 9.6],
        "close": [10.0, 10.0, 9.75],       # 末根跌到 9.75
        "volume": [1_000_000.0, 1_000_000.0, 1_000_000.0],
        "ma5":   [10.0, 10.0, 9.90],       # 9.90×0.98=9.70 < 9.75 → 未破 MA5
        "ma10":  [10.0, 10.0, 10.00],      # 10.00×0.98=9.80 ≥ 9.75 → 破 MA10
        "ma20":  [9.6, 9.6, 9.60],         # 9.60×0.98=9.408 < 9.75 → 未破 MA20
        "ma60":  [9.0, 9.0, 9.00],
        "pct_change": [0.0, 0.0, -2.5],    # 末根下跌日
    })


def _run(entry_cost):
    d = _make_indicator_df()
    # SELL_BREAK_MA* 默认带盘中确认时间闸(MA5=14:30/v1.7.403, MA10·MA20=09:26/v1.7.594),
    # 读墙上时钟 → 工作日凌晨~闸点前跑测试会被跳过(0717 曾在 00:30 假失败)。
    # 本组测试验证的是持仓判定与时钟无关, 显式归零三条闸门去掉时间依赖。
    cfg = get_merged_config({
        "SELL_BREAK_MA5": {"confirm_after_minute": 0},
        "SELL_BREAK_MA10": {"confirm_after_minute": 0},
        "SELL_BREAK_MA20": {"confirm_after_minute": 0},
    })
    return _detect_short_signals(
        d, d.iloc[-1], d.iloc[-2], cfg,
        entry_cost=entry_cost, entry_date=None, entry_model=None,
    )


def test_negative_cost_holding_still_breaks_ma10():
    """摊薄成本为负的持仓, 跌破MA10仍应触发卖点(修复前会被 >0 判定静默丢掉)。"""
    ids = [s.signal_id for s in _run(entry_cost=-1.5)]
    assert "SELL_BREAK_MA10" in ids


def test_zero_cost_holding_still_breaks_ma10():
    """摊薄成本恰为 0 的持仓同样保留破位保护。"""
    ids = [s.signal_id for s in _run(entry_cost=0.0)]
    assert "SELL_BREAK_MA10" in ids


def test_non_holding_does_not_emit_break_ma():
    """entry_cost=None(非持仓) 不应触发任何持仓破位卖点。"""
    ids = [s.signal_id for s in _run(entry_cost=None)]
    assert "SELL_BREAK_MA10" not in ids
    assert "SELL_BREAK_MA5" not in ids
    assert "SELL_BREAK_MA20" not in ids
