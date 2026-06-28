"""数据源头校验下沉测试 (v1.7.387) — 0612误报普查第2项整改.

把"分时冻结回放丢弃"和"竞价涨跌家数0/0返回空"从消费端(plunge_detector)下沉到
数据入口(get_index_trends / get_market_stats), 让所有下游天然免疫:
1. trading_calendar.trading_minute / trends_stale — 通用陈旧度判断(从 plunge_detector 上移)。
2. ai_analyst._sanitize_stale_index_trends — A股指数分时陈旧则清空该指数 trends(港股不动)。
3. ai_analyst._compute_market_stats — 全场无涨跌幅=无数据返回 None; 最新价0不参与涨跌停判定。
"""
import pandas as pd

from backend.core.trading_calendar import trading_minute, trends_stale
from backend.services.ai_analyst import _compute_market_stats, _sanitize_stale_index_trends


def _timed_trends(prices, end_hhmm):
    eh, em = int(end_hhmm[:2]), int(end_hhmm[3:])
    end_abs = eh * 60 + em
    return [{"time": f"{(end_abs - (len(prices) - 1 - i)) // 60:02d}:{(end_abs - (len(prices) - 1 - i)) % 60:02d}",
             "price": p} for i, p in enumerate(prices)]


class TestTrendsStale:
    def test_empty_trends_not_stale(self):
        # 空序列是"没数据"不是"陈旧", 由各消费方自行处理
        assert trends_stale([], "10:30") is False

    def test_frozen_is_stale(self):
        assert trends_stale(_timed_trends([100] * 5, "10:00"), "10:30") is True

    def test_fresh_is_not_stale(self):
        assert trends_stale(_timed_trends([100] * 5, "10:29"), "10:30") is False

    def test_lunch_gap_not_stale(self):
        assert trends_stale(_timed_trends([100] * 5, "11:30"), "13:03") is False

    def test_no_time_field_not_stale(self):
        assert trends_stale([{"price": 100}], "10:30") is False

    def test_trading_minute_reexported(self):
        assert trading_minute("13:02") == 122


class TestSanitizeStaleIndexTrends:
    def test_frozen_a_share_index_emptied(self):
        result = {"sh000688": {"name": "科创指数", "pre_close": 100,
                               "trends": _timed_trends([100] * 5, "10:00"), "amount": 50}}
        out = _sanitize_stale_index_trends(result, "13:30")
        assert out["sh000688"]["trends"] == []
        assert out["sh000688"]["pre_close"] == 100   # 其余字段保留

    def test_fresh_a_share_index_kept(self):
        result = {"sh000001": {"name": "上证指数", "pre_close": 100,
                               "trends": _timed_trends([100] * 5, "13:29"), "amount": 50}}
        out = _sanitize_stale_index_trends(result, "13:30")
        assert len(out["sh000001"]["trends"]) == 5

    def test_hk_index_untouched(self):
        # 港股交易时段不同(16:00收盘), 不适用A股交易分钟折算, 一律不动
        result = {"r_hkHSI": {"name": "恒生指数", "pre_close": 20000,
                              "trends": _timed_trends([20000] * 5, "10:00"), "amount": 0}}
        out = _sanitize_stale_index_trends(result, "13:30")
        assert len(out["r_hkHSI"]["trends"]) == 5

    def test_no_time_field_kept(self):
        result = {"sz399006": {"name": "创业板指", "pre_close": 100,
                               "trends": [{"price": 100}] * 5, "amount": 50}}
        out = _sanitize_stale_index_trends(result, "13:30")
        assert len(out["sz399006"]["trends"]) == 5


def _spot_df(rows):
    return pd.DataFrame(rows, columns=["代码", "名称", "最新价", "涨跌幅", "昨收"])


class TestComputeMarketStats:
    def test_auction_garbage_returns_none(self):
        # 竞价时段新浪快照: 最新价全0、涨跌幅全0 → 上涨0/下跌0, 原逻辑还会把全场数成跌停
        df = _spot_df([(f"60000{i}", f"股{i}", 0.0, 0.0, 10.0) for i in range(5)])
        assert _compute_market_stats(df) is None

    def test_normal_counts(self):
        df = _spot_df([
            ("600001", "甲", 11.0, 10.0, 10.0),    # 涨停(10%)
            ("600002", "乙", 9.0, -10.0, 10.0),    # 跌停
            ("600003", "丙", 10.5, 5.0, 10.0),     # 上涨
            ("600004", "丁", 9.8, -2.0, 10.0),     # 下跌
        ])
        out = _compute_market_stats(df)
        assert out == {"limit_up": 1, "limit_down": 1, "up_count": 2, "down_count": 2}

    def test_zero_price_rows_excluded_from_limit_counts(self):
        # 个别未成交股(最新价0)不得被"0≤跌停价"数进跌停
        df = _spot_df([
            ("600001", "甲", 10.5, 5.0, 10.0),
            ("600002", "乙", 0.0, 0.0, 10.0),
            ("600003", "丙", 9.7, -3.0, 10.0),
        ])
        out = _compute_market_stats(df)
        assert out["limit_down"] == 0

    def test_none_df_returns_none(self):
        assert _compute_market_stats(None) is None
