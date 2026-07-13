# -*- coding: utf-8 -*-
"""股票池均线口径 (v1.7.606): 今日均线 = (最近 k-1 根历史收盘 + 今日现价)/k。

修的是: 原来直接取 kline_cache 最近 k 根收盘, 但当日K线盘中不在 cache 里(盘后才回填),
存进去的其实是【昨日均线】, 页面却拿【今日现价】去比 → 系统性偏差落在"碰线判定"临界区。
"""
from backend.services.quote_refresher import ma_with_today


def test_today_price_replaces_missing_today_bar():
    # 19根历史全10.0 + 今日9.0 → MA20 = (19*10 + 9)/20 = 9.95, 而非纯历史的 10.0
    closes = [10.0] * 19                       # 最新在前(全平, 顺序无所谓)
    assert abs(ma_with_today(closes, 20, 15, 9.0) - 9.95) < 1e-9


def test_only_uses_k_minus_1_history():
    # 给足30根历史, MA20 只该吃前19根 + 今日
    closes = [10.0] * 19 + [99.0] * 11         # 第20根之后是噪声, 不该进 MA20
    assert abs(ma_with_today(closes, 20, 15, 9.0) - 9.95) < 1e-9


def test_lagged_vs_today_diff_is_material():
    """回归护栏: 旧口径(纯历史)和新口径在临界区会给出相反的"站上/跌破"结论。"""
    closes = [10.0] * 19
    price = 9.98
    lagged = sum(closes[:20]) / len(closes[:20])        # 旧: 昨日MA20 = 10.0
    today = ma_with_today(closes, 20, 15, price)        # 新: 今日MA20 = 9.999
    assert price < lagged                                # 旧口径判"跌破"
    assert price < today                                 # 新口径也判跌破, 但线更低
    assert today < lagged                                # 今日价拉低了均线 → 破得没那么夸张


def test_insufficient_history_returns_none():
    assert ma_with_today([10.0] * 13, 20, 15, 9.0) is None    # 不足14根历史
    assert ma_with_today([10.0] * 14, 20, 15, 9.0) is not None  # 14根历史+今日=15根 ≥ floor


def test_partial_history_averages_actual_count():
    # 只有14根历史 → 按 14+1=15 根取均, 不补齐到20
    closes = [10.0] * 14
    assert abs(ma_with_today(closes, 20, 15, 5.0) - (140 + 5) / 15) < 1e-9


def test_zero_price_returns_none():
    assert ma_with_today([10.0] * 19, 20, 15, 0) is None
