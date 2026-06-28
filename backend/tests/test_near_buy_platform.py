"""临近买点榜 — 中继平台突破接近档 (v1.7.447).

触发档要尾盘14:40收盘确认(突破常已涨停), 接近档全天提前报"逼近上沿":
窄平台+前置主升结构成立, 现价距突破价≤3% 即报, 放量/成交额盘中未到不卡门槛。
"""
import numpy as np
import pandas as pd

from backend.services import near_buy
from backend.services import signal_engine_config as cfgmod


def _platform_df(today_close: float) -> pd.DataFrame:
    """主升前置(8→13,+30%) → 12日窄平台缓升(12.6→12.9,上沿13.0) → 今日 = today_close。"""
    N = 90
    close = np.zeros(N)
    close[:57] = np.linspace(8.0, 10.0, 57)
    close[57:77] = np.linspace(10.0, 13.0, 20)   # 前置主升 prior_low~10 PH~13
    close[77:89] = np.linspace(12.6, 12.9, 12)   # 平台 缓升 窄
    close[89] = today_close
    high = close * 1.005
    high[77:89] = np.maximum(high[77:89], 13.0)  # 平台上沿 PH=13.0
    low = close * 0.99
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=N).strftime("%Y-%m-%d"),
        "open": close, "high": high, "low": low, "close": close,
        "volume": np.full(N, 1.0e7),
    })


def test_platform_breakout_near_fires_when_pressing_edge():
    cfg = cfgmod.get_merged_config({})
    df = _platform_df(12.9)                       # 距突破价13.065 还差~1.3%
    res = near_buy.evaluate(df, {"price": 12.9, "volume": 5.0e6}, cfg)
    assert res is not None
    assert res["tier"] == 1                        # 接近(非触发)
    hit = next(h for h in res["hits"] if h["buy_id"] == "BUY_PLATFORM_BREAKOUT")
    assert hit["kind"] == "接近"
    # 放量/成交额盘中未到 → 进"还差"清单, 不挡接近
    miss = "".join(hit["miss"])
    assert "放量" in miss and "成交额" in miss


def test_platform_breakout_silent_when_far_below_edge():
    cfg = cfgmod.get_merged_config({})
    df = _platform_df(12.0)                        # 距突破价~8%, 不算逼近
    res = near_buy.evaluate(df, {"price": 12.0, "volume": 5.0e6}, cfg)
    assert res is None
