# -*- coding: utf-8 -*-
"""财报披露日历 + 预增榜 纯函数 (v1.7.573). 不连库不联网。"""
from datetime import date

from backend.services.disclosure_reminder import _current_report_date, REPORT_TYPE_CN
from backend.services.earnings_forecast_scan import _amp_txt
from backend.fetcher.earnings_data import GOOD_TYPES, BAD_TYPES


def test_current_report_date_picks_recent_quarter_end():
    assert _current_report_date(date(2026, 7, 3)) == "2026-06-30"    # 半年报窗口
    assert _current_report_date(date(2026, 2, 15)) == "2025-12-31"   # 年报窗口(去年Q4)
    assert _current_report_date(date(2026, 4, 20)) == "2026-03-31"   # 一季报窗口
    assert _current_report_date(date(2026, 10, 30)) == "2026-09-30"  # 三季报窗口


def test_current_report_date_january_falls_back_prev_year():
    assert _current_report_date(date(2026, 1, 10)) == "2025-12-31"


def test_amp_txt_range_and_single():
    assert _amp_txt(30.0, 50.0) == "+30%~+50%"
    assert _amp_txt(None, 40.0) == "+40%"
    assert _amp_txt(20.0, 20.0) == "+20%"
    assert _amp_txt(None, None) == "—"


def test_good_bad_types_disjoint_and_cover_key_labels():
    assert "预增" in GOOD_TYPES and "扭亏" in GOOD_TYPES
    assert "预减" in BAD_TYPES and "首亏" in BAD_TYPES
    assert GOOD_TYPES.isdisjoint(BAD_TYPES)


def test_report_type_cn_map():
    assert REPORT_TYPE_CN["2"] == "半年报"
    assert REPORT_TYPE_CN["4"] == "年报"
