# -*- coding: utf-8 -*-
"""全天成交额/量预测系数测试 (v1.7.518 实测标定).

旧版手填经验系数早盘偏低(10:00 填 0.24), 把开盘累计额外推放大约1.4倍 → 预测离谱。
现整表换全市场 5min 实测均值, 锁定关键锚点与单调性, 防回归。
"""
from datetime import datetime

import pytest

from backend.services import intraday_estimator as ie


def test_table_monotonic_and_bounded():
    vals = [v for _, v in ie._TIME_COEF_TABLE]
    assert vals[0] == 0.0 and vals[-1] == 1.0
    assert all(b >= a for a, b in zip(vals, vals[1:])), "累计占比必须单调不减"


def test_lunch_break_flat():
    # 11:30 与 13:00 累计占比相同(午休不成交)
    assert ie._interp_coef(11 * 60 + 30) == ie._interp_coef(13 * 60)


def test_morning_coef_matches_calibration():
    # 实测早盘高度前置: 10:00 已成交约1/3(旧经验表仅0.24)
    assert ie._interp_coef(10 * 60) == pytest.approx(0.328, abs=0.01)
    assert ie._interp_coef(9 * 60 + 45) == pytest.approx(0.220, abs=0.01)


def test_projection_not_inflated_at_open():
    # 回归: 10:01 累计1.30万亿, 旧表外推5.17万亿(离谱), 实测系数应落在~3.9万亿
    proj = ie.project_full_day_amount(1.30e12, datetime(2026, 6, 23, 10, 1))
    assert proj is not None
    assert 3.5e12 < proj < 4.3e12, f"开盘外推被放大: {proj/1e12:.2f}万亿"


def test_non_trading_returns_none():
    assert ie.project_full_day_amount(1e12, datetime(2026, 6, 23, 9, 20)) is None
    assert ie.project_full_day_amount(1e12, datetime(2026, 6, 23, 15, 30)) is None


def test_zero_or_none_amount():
    assert ie.project_full_day_amount(0, datetime(2026, 6, 23, 10, 0)) is None
    assert ie.project_full_day_amount(None, datetime(2026, 6, 23, 10, 0)) is None
