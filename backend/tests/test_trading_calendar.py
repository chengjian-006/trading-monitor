"""交易日历法定节假日剔除测试 — 端午休市日仍推送行情的根因整改.

is_workday() 此前只看周一~周五, 不剔法定节假日, 导致节假日(落在工作日时)
盘中扫描/行情刷新照常开闸, 数据源吐出上一交易日残留快照→误推行情提醒.
"""
from datetime import datetime

from backend.core import trading_calendar as tc


class TestHolidayExclusion:
    def test_dragon_boat_2026_not_workday(self):
        # 2026-06-19 周五 = 端午节, 虽是工作日但休市
        assert tc.is_workday(datetime(2026, 6, 19, 10, 0)) is False

    def test_dragon_boat_not_trading_time(self):
        # 即便落在交易时段也不应开闸
        assert tc.is_trading_time(datetime(2026, 6, 19, 10, 0)) is False
        assert tc.is_continuous_auction(datetime(2026, 6, 19, 10, 0)) is False

    def test_normal_weekday_still_workday(self):
        # 2026-06-18 周四, 正常交易日
        assert tc.is_workday(datetime(2026, 6, 18, 10, 0)) is True

    def test_weekend_still_not_workday(self):
        # 2026-06-20 周六(端午调休假期), 本就非工作日
        assert tc.is_workday(datetime(2026, 6, 20, 10, 0)) is False

    def test_makeup_workday_weekend_still_closed(self):
        # 调休补班的周末: 股市不交易, 因 weekday>=5 仍被排除(用国庆节前后的补班周末验证)
        # 2026-09-27 周日 调休补班 → 上班但股市休市
        assert tc.is_workday(datetime(2026, 9, 27, 10, 0)) is False
