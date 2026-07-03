# -*- coding: utf-8 -*-
"""行情自检"恢复宽限窗"判定 (v1.7.562) — 开盘/午休回来前几分钟不判陈旧。"""
from datetime import datetime

from backend.services.data_sanity import _in_resume_grace


def _t(h, m, s=0):
    return datetime(2026, 7, 3, h, m, s)


def test_lunch_resume_in_grace():
    # 13:00 恢复后前 3 分钟内 → 宽限
    assert _in_resume_grace(_t(13, 0, 5)) is True
    assert _in_resume_grace(_t(13, 2, 59)) is True


def test_lunch_resume_after_grace():
    assert _in_resume_grace(_t(13, 3, 0)) is False
    assert _in_resume_grace(_t(13, 10)) is False


def test_open_resume_in_grace():
    # 09:25 开盘(含集合竞价撮合)后前 3 分钟 → 宽限
    assert _in_resume_grace(_t(9, 25, 30)) is True


def test_normal_intraday_not_grace():
    assert _in_resume_grace(_t(10, 30)) is False
    assert _in_resume_grace(_t(14, 0)) is False


def test_before_resume_not_grace():
    # 时段开始之前(午休中)不算宽限 — 本就不在交易时段, 自检另有闸门
    assert _in_resume_grace(_t(12, 59)) is False
