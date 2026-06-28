"""信号EOD自动复核测试 (v1.7.387) — 0612误报普查第1项整改.

收盘后用确定的真实日线复核当日触发的信号, 数据层假象自动标记存疑:
1. check_fingerprint — 触发时 indicators 里的"昨日bar日期/昨收"指纹 vs 真实昨日 → 序列错位精确判别。
2. check_price_range — 个股触发价必须落在当日真实价格区间(±2%容差) → 垃圾行情判别。
3. check_breadth_detail / check_speed_detail — 上涨0家且下跌0家 / 跌停家数超合理上限 → 竞价0/0假象。
4. check_index_drop_detail — 当日真实波幅容不下宣称的N分钟急跌 → 分时冻结回放判别。
5. scanner._extract_indicators 落库时写入 prev_bar_date/prev_close 指纹(供1复核)。
"""
import numpy as np
import pandas as pd

from backend.services.signal_eod_audit import (
    check_breadth_detail,
    check_fingerprint,
    check_index_drop_detail,
    check_price_range,
    check_speed_detail,
    parse_index_symbol,
)


class TestFingerprint:
    def test_misaligned_prev_date_flagged(self):
        ind = {"prev_bar_date": "2026-06-06", "prev_close": 10.0}
        note = check_fingerprint(ind, "2026-06-08", 11.0)
        assert note and "错位" in note

    def test_prev_close_mismatch_flagged(self):
        ind = {"prev_bar_date": "2026-06-08", "prev_close": 10.0}
        note = check_fingerprint(ind, "2026-06-08", 11.0)
        assert note and "昨收" in note

    def test_match_passes(self):
        ind = {"prev_bar_date": "2026-06-08", "prev_close": 10.0}
        assert check_fingerprint(ind, "2026-06-08", 10.0) is None

    def test_no_fingerprint_unverifiable(self):
        assert check_fingerprint({"ma5": 10.0}, "2026-06-08", 10.0) is None
        assert check_fingerprint(None, "2026-06-08", 10.0) is None


class TestPriceRange:
    def test_price_outside_range_flagged(self):
        assert check_price_range(418.0, 95.0, 105.0) is not None

    def test_price_inside_range_passes(self):
        assert check_price_range(100.0, 95.0, 105.0) is None

    def test_tolerance_2pct(self):
        assert check_price_range(106.0, 95.0, 105.0) is None      # 105*1.02=107.1 内
        assert check_price_range(93.5, 95.0, 105.0) is None       # 95*0.98=93.1 内

    def test_bad_inputs_skip(self):
        assert check_price_range(0, 95.0, 105.0) is None
        assert check_price_range(100.0, 0, 0) is None


class TestBreadthSpeedDetail:
    def test_zero_zero_flagged(self):
        detail = "下跌/上涨比 = 99.0 (阈值3.0)|下跌0家 / 上涨0家|跌停5523家"
        assert check_breadth_detail(detail) is not None

    def test_real_breadth_passes(self):
        detail = "下跌/上涨比 = 4.2 (阈值3.0)|下跌4200家 / 上涨1000家|跌停45家"
        assert check_breadth_detail(detail) is None

    def test_absurd_limit_down_flagged(self):
        detail = "下跌/上涨比 = 3.5 (阈值3.0)|下跌3500家 / 上涨1000家|跌停5523家"
        assert check_breadth_detail(detail) is not None

    def test_speed_absurd_total_flagged(self):
        detail = "5分钟内新增跌停5473家 (阈值8)|当前跌停共5523家"
        assert check_speed_detail(detail) is not None

    def test_speed_real_passes(self):
        detail = "5分钟内新增跌停12家 (阈值8)|当前跌停共45家"
        assert check_speed_detail(detail) is None


class TestIndexDropDetail:
    def test_replay_drop_exceeds_day_range_flagged(self):
        # 宣称10分钟跌1.16%, 但当日真实高低波幅只有0.4% → 冻结回放
        detail = "科创指数 10分钟内跌幅 -1.16%|日内总跌幅 -1.16%"
        note = check_index_drop_detail(detail, day_high=1004.0, day_low=1000.0, pre_close=1002.0)
        assert note is not None

    def test_real_drop_within_day_range_passes(self):
        detail = "上证指数 10分钟内跌幅 -1.20%|日内总跌幅 -2.10%"
        assert check_index_drop_detail(detail, day_high=4010.0, day_low=3900.0, pre_close=4000.0) is None

    def test_unparseable_or_bad_bar_skips(self):
        assert check_index_drop_detail("无规则文本", 1004.0, 1000.0, 1002.0) is None
        assert check_index_drop_detail("科创指数 10分钟内跌幅 -1.16%", 0, 0, 0) is None

    def test_parse_index_symbol(self):
        assert parse_index_symbol("科创指数 10分钟内跌幅 -1.16%") == "sh000688"
        assert parse_index_symbol("创业板指 10分钟内跌幅 -2.0%") == "sz399006"
        assert parse_index_symbol("上证指数 10分钟内跌幅 -1.0%") == "sh000001"
        assert parse_index_symbol("没有指数名") is None


class TestIndicatorsFingerprint:
    """scanner._extract_indicators 必须写入 prev_bar_date/prev_close 指纹."""

    def _df(self):
        dates = [f"2026-06-{d:02d}" for d in (4, 5, 8, 9, 10, 11)]
        closes = np.linspace(10.0, 11.0, 6)
        return pd.DataFrame({
            "date": dates,
            "open": closes * 0.99, "high": closes * 1.01,
            "low": closes * 0.98, "close": closes,
            "volume": np.full(6, 1_000_000.0),
        })

    def test_fingerprint_written_with_realtime(self):
        from unittest.mock import patch
        from backend.services.scanner import _extract_indicators
        rt = {"price": 11.5, "open": 11.2, "high": 11.6, "low": 11.1, "volume": 500_000.0}
        with patch("backend.services.signal_engine._dt_today", return_value="2026-06-12"):
            ind = _extract_indicators(self._df(), rt)
        assert ind.get("prev_bar_date") == "2026-06-11"
        assert abs(ind["prev_close"] - 11.0) < 1e-6

    def test_no_realtime_no_fingerprint(self):
        from backend.services.scanner import _extract_indicators
        ind = _extract_indicators(self._df(), None)
        assert "prev_bar_date" not in ind
