"""_merge_realtime_bar 单测 — v1.7.382 序列错位修复.

背景(圣泉集团 0611 误触发): detect_signals 把日K最后一根bar无条件当"今日"用实时行情覆盖。
当日K源盘中缺今日bar(典型: 网络源失败回退DB缓存, 缓存末根=昨日)时, 昨日真实K线被覆盖抹掉,
整个序列错位一天 → 涨幅/弱势极限配对/放量倍数全部算错。

修复语义: 末根bar日期 == 今日 → 原地覆盖(原行为); 早于今日 → 追加今日新行, 保留昨日bar。
不连数据库, 不打外网。
"""
from unittest.mock import patch

import numpy as np
import pandas as pd

from backend.services.signal_engine import _merge_realtime_bar


def _make_kline(last_date: str, n: int = 5) -> pd.DataFrame:
    """生成 n 根日K, 末根日期为 last_date (其余日期递减, 仅用于占位)."""
    dates = [f"2026-06-{i+1:02d}" for i in range(n - 1)] + [last_date]
    closes = np.linspace(10.0, 11.0, n)
    return pd.DataFrame({
        "date": dates,
        "open": closes * 0.99, "high": closes * 1.01,
        "low": closes * 0.98, "close": closes,
        "volume": np.full(n, 1_000_000.0),
    })


REALTIME = {"price": 12.5, "open": 12.0, "high": 12.6, "low": 11.9,
            "volume": 2_000_000.0, "amount": 25_000_000.0}


class TestMergeRealtimeBar:
    def test_appends_new_row_when_last_bar_is_stale(self):
        """末根=昨日(源缺今日bar): 追加今日新行, 昨日bar原样保留."""
        d = _make_kline(last_date="2026-06-10")
        with patch("backend.services.signal_engine.is_intraday", return_value=False):
            out = _merge_realtime_bar(d, REALTIME, today="2026-06-11")
        assert len(out) == len(d) + 1
        # 昨日一字板那类真实bar不能被覆盖
        yesterday = out.iloc[-2]
        assert str(yesterday["date"])[:10] == "2026-06-10"
        assert yesterday["close"] == d.iloc[-1]["close"]
        assert yesterday["volume"] == d.iloc[-1]["volume"]
        # 新行 = 今日实时
        today_row = out.iloc[-1]
        assert str(today_row["date"])[:10] == "2026-06-11"
        assert today_row["close"] == 12.5
        assert today_row["volume"] == 2_000_000.0

    def test_overwrites_in_place_when_last_bar_is_today(self):
        """末根=今日(源已带今日bar): 维持原行为, 原地覆盖不加行."""
        d = _make_kline(last_date="2026-06-11")
        with patch("backend.services.signal_engine.is_intraday", return_value=False):
            out = _merge_realtime_bar(d, REALTIME, today="2026-06-11")
        assert len(out) == len(d)
        assert out.iloc[-1]["close"] == 12.5
        assert out.iloc[-2]["close"] == d.iloc[-2]["close"]

    def test_overwrite_keeps_max_high_min_low(self):
        """覆盖路径: high取两者较大, low取两者较小(原行为回归保护)."""
        d = _make_kline(last_date="2026-06-11")
        d.loc[d.index[-1], "high"] = 13.0   # 历史bar盘中冲高过13
        d.loc[d.index[-1], "low"] = 11.0
        with patch("backend.services.signal_engine.is_intraday", return_value=False):
            out = _merge_realtime_bar(d, REALTIME, today="2026-06-11")
        assert out.iloc[-1]["high"] == 13.0     # max(13.0, 12.6)
        assert out.iloc[-1]["low"] == 11.0      # min(11.0, 11.9)

    def test_fallback_overwrite_when_no_date_column(self):
        """无 date 列(异常输入): 退回旧行为原地覆盖, 不崩."""
        d = _make_kline(last_date="2026-06-11").drop(columns=["date"])
        with patch("backend.services.signal_engine.is_intraday", return_value=False):
            out = _merge_realtime_bar(d, REALTIME, today="2026-06-11")
        assert len(out) == len(d)
        assert out.iloc[-1]["close"] == 12.5


class TestSiblingMergeSites:
    """scanner._extract_indicators / near_buy._overlay_rt 与主引擎同病同修 (v1.7.384 横向排查)."""

    def test_near_buy_overlay_rt_appends_when_stale(self):
        from backend.services.near_buy import _overlay_rt
        d = _make_kline(last_date="2026-06-10", n=25)
        with patch("backend.services.signal_engine._dt_today", return_value="2026-06-11"):
            out = _overlay_rt(d, REALTIME)
        # 昨日bar保留, 新行为今日实时价
        assert str(out.iloc[-2]["date"])[:10] == "2026-06-10"
        assert out.iloc[-2]["close"] == d.iloc[-1]["close"]
        assert out.iloc[-1]["close"] == 12.5

    def test_scanner_extract_indicators_keeps_yesterday_when_stale(self):
        from backend.services.scanner import _extract_indicators
        d = _make_kline(last_date="2026-06-10", n=25)
        with patch("backend.services.signal_engine._dt_today", return_value="2026-06-11"):
            ind = _extract_indicators(d, REALTIME, keys=("prev_close",))
        # prev_close 应是昨日真实收盘(末根=新追加的今日行, 其前一根=昨日), 而非前日
        assert ind["close"] == 12.5
        assert ind["prev_close"] == round(float(d.iloc[-1]["close"]), 3)


class TestSaveKlineCacheIntradayFilter:
    """盘中拉到的日K若带今日未收盘bar, 不得作为正式日线落库 (v1.7.384)."""

    async def test_intraday_drops_today_bar_before_save(self):
        from backend.fetcher import klines as klines_mod
        d = _make_kline(last_date="2026-06-11")
        saved = {}

        async def _capture(code, rows):
            saved["rows"] = rows

        with patch("backend.models.repository.cache_klines", new=_capture), \
             patch.object(klines_mod, "_cache_today", return_value="2026-06-11"), \
             patch("backend.services.intraday_estimator.is_intraday", return_value=True):
            await klines_mod._save_kline_cache("605589", d)
        dates = [r[0] for r in saved["rows"]]
        assert "2026-06-11" not in dates
        assert len(dates) == len(d) - 1

    async def test_after_close_keeps_today_bar(self):
        from backend.fetcher import klines as klines_mod
        d = _make_kline(last_date="2026-06-11")
        saved = {}

        async def _capture(code, rows):
            saved["rows"] = rows

        with patch("backend.models.repository.cache_klines", new=_capture), \
             patch.object(klines_mod, "_cache_today", return_value="2026-06-11"), \
             patch("backend.services.intraday_estimator.is_intraday", return_value=False):
            await klines_mod._save_kline_cache("605589", d)
        dates = [r[0] for r in saved["rows"]]
        assert "2026-06-11" in dates
