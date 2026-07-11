"""5分钟K线每日追加任务 (v1.7.598, 胜率5分钟诚实口径的数据前提).

覆盖: 逐票增量窗口规划 / 已最新则跳过 / 新票默认回填一年 / 任务注册+长超时挂表。
baostock 网络路径不在单测范围(复用已验证的一次性回填脚本逻辑)。
"""
from datetime import date

from backend.services.kline_5m_appender import _plan_windows, _to_bs, _UPSERT


class TestPlanWindows:
    def test_incremental_from_last_day(self):
        plans = _plan_windows({"600519": "2026-06-18"}, ["600519"], end="2026-07-10")
        assert plans["sh.600519"] == ("2026-06-19", "2026-07-10")

    def test_up_to_date_skipped(self):
        plans = _plan_windows({"600519": "2026-07-10"}, ["600519"], end="2026-07-10")
        assert "sh.600519" not in plans

    def test_new_code_gets_default_lookback(self):
        plans = _plan_windows({}, ["300750"], end="2026-07-10", default_days=366)
        start, end = plans["sz.300750"]
        assert end == "2026-07-10"
        assert start == (date(2026, 7, 10) - __import__("datetime").timedelta(days=366)).isoformat()

    def test_non_baostock_codes_dropped(self):
        plans = _plan_windows({}, ["830799", "920099"], end="2026-07-10")
        assert plans == {}


class TestToBs:
    def test_prefixes(self):
        assert _to_bs("600519") == "sh.600519"
        assert _to_bs("000725") == "sz.000725"
        assert _to_bs("300750") == "sz.300750"
        assert _to_bs("688981") == "sh.688981"
        assert _to_bs("830799") is None


class TestWiring:
    def test_upsert_is_insert_on_duplicate(self):
        assert _UPSERT.strip().upper().startswith("INSERT INTO CFZY_SYS_KLINE_5M")
        assert "ON DUPLICATE KEY UPDATE" in _UPSERT

    def test_registered_in_task_handlers(self):
        from backend.services.task_registry import TASK_HANDLERS
        assert "append_kline_5m" in TASK_HANDLERS

    def test_long_timeout(self):
        from backend.services.task_registry import get_task_timeout, LONG_TASK_TIMEOUTS
        assert "append_kline_5m" in LONG_TASK_TIMEOUTS
        assert get_task_timeout("append_kline_5m") >= 3600
