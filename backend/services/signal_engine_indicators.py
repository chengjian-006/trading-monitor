"""信号引擎指标计算 + Signal dataclass - v1.7.x.

Signal:            统一信号对象 (id/name/direction/detail/strength/used_indicators)
compute_indicators: 给 DataFrame 加 MA5/10/20/60 + vol_ma5/20 + vol_ratio + RSI + MACD + 形态标记等
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Signal:
    signal_id: str
    signal_name: str
    direction: str      # buy / sell / reduce / plunge
    detail: str
    strength: int = 1   # 1-3, higher = stronger
    used_indicators: tuple = ()  # keys actually used by this rule


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_indicators(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """给 DataFrame 加全部指标列. 调用方拿到 d.iloc[-1] 当 latest, 各个 detector 用对应 cfg 段判定."""
    d = df.copy()
    # OHLCV 统一转 float64: 部分数据源/缓存把 volume 推断成 int64, 后续 scanner 回填实时量
    # (d.loc[i,"volume"]=浮点) 会触发 pandas2 "Invalid value for dtype int64" 报错, 在此源头规避
    for _col in ("open", "high", "low", "close", "volume"):
        if _col in d.columns:
            d[_col] = pd.to_numeric(d[_col], errors="coerce").astype("float64")
    d["ma5"] = d["close"].rolling(5).mean()
    d["ma10"] = d["close"].rolling(10).mean()
    d["ma20"] = d["close"].rolling(20).mean()
    d["ma60"] = d["close"].rolling(60).mean()

    d["vol_ma5"] = d["volume"].rolling(5).mean()
    d["vol_ma20"] = d["volume"].rolling(20).mean()

    d["vol_ratio_5"] = d["volume"] / d["vol_ma5"].replace(0, np.nan)
    d["vol_ratio_20"] = d["volume"] / d["vol_ma20"].replace(0, np.nan)

    d["amplitude"] = (d["high"] - d["low"]) / d["close"].shift(1)
    d["pct_change"] = d["close"].pct_change()

    d["prev_close"] = d["close"].shift(1)
    d["prev_volume"] = d["volume"].shift(1)
    d["prev_ma5"] = d["ma5"].shift(1)
    d["prev_ma10"] = d["ma10"].shift(1)
    d["prev_ma20"] = d["ma20"].shift(1)

    d["vol_2d_ago"] = d["volume"].shift(2)
    d["vol_ma5_2d_ago"] = d["vol_ma5"].shift(2)
    d["vol_ratio_5_prev2"] = d["vol_2d_ago"] / d["vol_ma5_2d_ago"].replace(0, np.nan)

    body = (d["close"] - d["open"]).abs()
    d["lower_shadow"] = (d[["open", "close"]].min(axis=1) - d["low"]) / d["close"].shift(1)
    d["is_doji"] = body / d["close"].shift(1) < 0.005
    d["is_small_yang"] = (d["close"] > d["open"]) & (d["pct_change"].abs() < 0.02)

    # MACD / RSI: 信号已下线, 但 EXTRA_FILTERS (filter_rsi_min/max) 仍可能引用 rsi
    d["ema_fast"] = _ema(d["close"], 12)
    d["ema_slow"] = _ema(d["close"], 26)
    d["dif"] = d["ema_fast"] - d["ema_slow"]
    d["dea"] = _ema(d["dif"], 9)
    d["macd_hist"] = (d["dif"] - d["dea"]) * 2

    delta = d["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi"] = 100 - (100 / (1 + rs))

    return d
