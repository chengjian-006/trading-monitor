"""信号引擎检测器单测 - v1.7.x.

覆盖核心算法 (signal_engine_detectors 内):
  _detect_s0_weak_extreme    : 6 条 AND 判定每条独立 negative case
  _detect_strong_start_right : 5 条 AND 判定 + S0 baseline 依赖
  _intraday_after            : 集合竞价 / 连续竞价 / 真盘后 三档时间门槛
  _nearest_ma_label          : 最接近均线标注
  compute_indicators         : 基本输入产出 column 完整性

不连真数据库, 也不打外网. 使用 pandas in-memory 构造 K 线.
"""
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backend.services.signal_engine_detectors import (
    _detect_platform_breakout,
    _detect_s0_weak_extreme,
    _detect_strong_start_right,
    _detect_vol_breakout,
    _intraday_after,
    _nearest_ma_label,
)
from backend.services.signal_engine import detect_signals
from backend.services.signal_engine_indicators import compute_indicators


# ── 测试数据工厂 ──

def _make_kline(n: int = 30, base_close: float = 10.0, base_vol: float = 1_000_000.0) -> pd.DataFrame:
    """生成 n 根稳定向上的日 K (close 微增, vol 稳定)."""
    closes = np.linspace(base_close, base_close * 1.2, n)
    opens = closes * 0.99
    highs = closes * 1.01
    lows = closes * 0.98
    vols = np.full(n, base_vol)
    return pd.DataFrame({
        "date": [f"2026-01-{i+1:02d}" for i in range(n)],
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols,
    })


def _make_s0_eligible_kline() -> pd.DataFrame:
    """构造一份"应该命中弱势极限"的 K 线:
    前面有主升浪 (+15%), 最近回踩到 MA10 附近, 当日地量(<近10日最低×1.1).
    """
    # 0-15 天: 横盘 9.5-10.5
    closes_a = np.linspace(9.5, 10.5, 16)
    # 15-25 天: 主升浪 +20% (10.5 → 12.6)
    closes_b = np.linspace(10.5, 12.6, 10)
    # 25-29 天: 回踩 (12.6 → 11.5, 贴近 MA10)
    closes_c = np.linspace(12.6, 11.5, 5)
    closes = np.concatenate([closes_a, closes_b, closes_c])

    # vol: 主升浪期间放大, 回踩期间地量
    vols = np.concatenate([
        np.full(16, 1_000_000),    # 横盘期
        np.full(10, 1_500_000),    # 主升期
        np.full(4,  600_000),      # 回踩缩量期 (略低于近10日均量)
        np.array([300_000]),       # 当日地量 (<近10日最低×1.1)
    ])

    n = len(closes)
    return pd.DataFrame({
        "date": [f"2026-01-{i+1:02d}" for i in range(n)],
        "open":   closes * 0.995,
        "high":   closes * 1.005,
        "low":    closes * 0.99,
        "close":  closes,
        "volume": vols,
    })


# ── compute_indicators ──

class TestComputeIndicators:
    def test_adds_all_required_columns(self):
        df = _make_kline(60)
        d = compute_indicators(df)
        expected = {"ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma20",
                    "vol_ratio_5", "vol_ratio_20", "rsi", "dif", "dea", "macd_hist",
                    "pct_change", "amplitude", "prev_close", "prev_volume"}
        missing = expected - set(d.columns)
        assert not missing, f"compute_indicators 缺列: {missing}"

    def test_ma60_nan_when_less_than_60_rows(self):
        df = _make_kline(30)
        d = compute_indicators(df)
        # 前 59 行 ma60 必为 NaN
        assert d["ma60"].iloc[:-1].isna().any()

    def test_pct_change_first_row_is_nan(self):
        df = _make_kline(30)
        d = compute_indicators(df)
        assert pd.isna(d["pct_change"].iloc[0])


# ── _nearest_ma_label ──

class TestNearestMaLabel:
    def test_picks_smallest_deviation(self):
        row = pd.Series({"ma5": 10.0, "ma10": 11.0, "ma20": 12.0, "ma60": 15.0})
        result = _nearest_ma_label(10.05, row)
        assert "MA5" in result

    def test_handles_close_above_ma(self):
        # close=10.05, MA5=10 (dev +0.5% 最近), 其他 MA 都更远
        row = pd.Series({"ma5": 10.0, "ma10": 11.0, "ma20": 12.0, "ma60": 15.0})
        result = _nearest_ma_label(10.05, row)
        assert "MA5" in result
        assert "+" in result  # 上方偏离正号

    def test_handles_close_below_ma(self):
        # close=9.95, MA5=10 (dev -0.5% 最近)
        row = pd.Series({"ma5": 10.0, "ma10": 11.0, "ma20": 12.0, "ma60": 15.0})
        result = _nearest_ma_label(9.95, row)
        assert "MA5" in result
        assert "-" in result  # 下方偏离负号

    def test_empty_row_returns_blank(self):
        row = pd.Series({"ma5": np.nan, "ma10": np.nan, "ma20": np.nan, "ma60": np.nan})
        assert _nearest_ma_label(10.0, row) == ""


# ── _intraday_after ──

class TestIntradayAfter:
    """v1.7.x: _intraday_after 增 now= 参数, 测试可直接注入时间, 不用 monkeypatch datetime."""

    def test_weekend_returns_true(self):
        # 周六 12:00 — 非工作日, 走 is_intraday=False 分支放行
        assert _intraday_after(600, now=datetime(2026, 1, 3, 12, 0)) is True

    def test_after_market_close_returns_true(self):
        """周一 16:00 (盘后) → True 给 EOD 路径放行."""
        assert _intraday_after(600, now=datetime(2026, 1, 5, 16, 0)) is True

    def test_lunch_break_applies_gate_not_bypass(self):
        """v1.7.596: 午休(11:30~13:00)走门槛, 不再无条件放行 —— 半场收盘非真收盘.
        earliest=600 的信号 12:00(=720)已过门槛 → True; 但尾盘确认型 earliest=880(14:40)
        在午休 12:00 → False (此前 bug: is_intraday=False 误当盘后放行, 拿11:30半场快照当收盘价误发)."""
        assert _intraday_after(600, now=datetime(2026, 1, 5, 12, 0)) is True
        assert _intraday_after(880, now=datetime(2026, 1, 5, 12, 0)) is False

    def test_lunch_edge_1130_seconds_blocks_late_gate(self):
        """核心 bug 窗口: 周三 11:30:30 (扫描器按分钟串仍判开跑, 但已过11:30:00进午休).
        尾盘门槛 880 必须 False —— 正是 0708 瑞芯微 11:30 中继平台突破误发的那几十秒."""
        assert _intraday_after(880, now=datetime(2026, 7, 8, 11, 30, 30)) is False

    def test_after_close_gate_bypass_for_eod(self):
        """周一 15:05 (真盘后) + 尾盘门槛880 → True, 给 EOD 复核/回测放行."""
        assert _intraday_after(880, now=datetime(2026, 1, 5, 15, 5)) is True


# ── 中继平台突破 chase_limit (v1.7.596) ──

class TestPlatformBreakoutChaseLimit:
    """收盘确认突破时现价逼近涨停板不发 — 收盘价=涨停价挂不进(0708瑞芯微涨停仍推的次要坑)."""

    _SC = {"L": 4, "REQ_PRIOR": False, "REQ_RISE": False, "REQ_VOL": False,
           "REQ_HOLD": False, "A": 0.15, "BUF": 0.005, "min_full_day_amount": 0,
           "chase_limit_skip": True, "chase_limit_buffer_pct": 1.0}

    def _platform_df(self, today_close):
        # 6 根: 索引1-4 = 窄平台(收盘/最高≈100), 索引5 = 今日突破
        rows = [{"close": 100.0, "high": 100.0, "low": 99.0, "volume": 1000.0, "amount_est": 2e9}
                for _ in range(5)]
        rows.append({"close": today_close, "high": today_close, "low": 100.0,
                     "volume": 1000.0, "amount_est": 2e9})
        return pd.DataFrame(rows)

    def test_breakout_not_at_limit_fires(self):
        """今日 +0.5% 突破上沿(100×1.005) 未涨停 → 正常出信号."""
        d = self._platform_df(100.5)
        assert _detect_platform_breakout(d, d.iloc[-1], self._SC, code="600000", name="测试") is not None

    def test_breakout_at_limit_blocked(self):
        """今日 +10% 主板涨停封板, 收盘=涨停价挂不进 → chase_limit 拦下不发."""
        d = self._platform_df(110.0)
        assert _detect_platform_breakout(d, d.iloc[-1], self._SC, code="600000", name="测试") is None

    def test_no_code_backtest_path_ignores_chase_limit(self):
        """回测无 code → 不判 chase_limit(与历史回测口径一致), 涨停也照常触发."""
        d = self._platform_df(110.0)
        assert _detect_platform_breakout(d, d.iloc[-1], self._SC, code=None) is not None

    def test_pre_auction_below_threshold_blocked(self):
        """工作日 09:25 集合竞价 + earliest=600 → False (修复 v1.7.106 漏洞)."""
        # 09:25 = 565 分钟 < earliest(600)
        assert _intraday_after(600, now=datetime(2026, 1, 5, 9, 25)) is False

    def test_pre_auction_above_threshold_allowed(self):
        """工作日 09:25 集合竞价 + earliest=500 → True (565 ≥ 500)."""
        assert _intraday_after(500, now=datetime(2026, 1, 5, 9, 25)) is True

    def test_after_intraday_threshold(self):
        """工作日 10:30 (连续竞价) + earliest=600 → True."""
        assert _intraday_after(600, now=datetime(2026, 1, 5, 10, 30)) is True

    def test_before_intraday_threshold(self):
        """工作日 09:45 (连续竞价但 10:00 之前) + earliest=600 → False."""
        assert _intraday_after(600, now=datetime(2026, 1, 5, 9, 45)) is False


# ── _detect_s0_weak_extreme negative cases ──
# (positive case 需要 mock trading_concepts.detect_main_rally, 复杂; 这里覆盖各条 AND 的 negative)

class TestS0WeakExtremeNegatives:
    def test_too_short_returns_none(self):
        """长度<12 不触发."""
        df = _make_kline(10)
        d = compute_indicators(df)
        sc = {"require_prior_rally": False}
        result = _detect_s0_weak_extreme(d, d.iloc[-1], sc)
        assert result is None

    def test_no_drought_volume_returns_none(self):
        """量没缩到地量 → return None."""
        df = _make_s0_eligible_kline()
        # 把当日量放大到 2_000_000 (远超地量阈值)
        df.loc[df.index[-1], "volume"] = 2_000_000
        d = compute_indicators(df)
        sc = {"require_prior_rally": False}
        result = _detect_s0_weak_extreme(d, d.iloc[-1], sc)
        assert result is None

    def test_close_below_ma60_returns_none(self):
        """长期未破要求 close > MA60, 砸下去就 None."""
        df = _make_s0_eligible_kline()
        d = compute_indicators(df)
        # 把当日 close 砸到 MA60 之下 (用极小价格)
        latest = d.iloc[-1].copy()
        latest["close"] = 1.0  # 远低于 MA60
        sc = {"require_prior_rally": False}
        result = _detect_s0_weak_extreme(d, latest, sc)
        assert result is None

    def test_close_far_from_ma_returns_none(self):
        """close 距 MA10 和 MA20 都 >2% → None."""
        df = _make_s0_eligible_kline()
        d = compute_indicators(df)
        latest = d.iloc[-1].copy()
        ma10 = latest["ma10"]
        # 把 close 拉到 MA10 上方 5% (超 ma10_above_max=2%)
        latest["close"] = ma10 * 1.05
        sc = {"require_prior_rally": False}
        result = _detect_s0_weak_extreme(d, latest, sc)
        assert result is None


# ── _detect_strong_start_right negative cases ──

class TestStrongStartNegatives:
    def test_below_pct_change_returns_none(self):
        """涨幅 < min_pct_change (默认2%) → None."""
        df = _make_kline(30)
        d = compute_indicators(df)
        latest = d.iloc[-1].copy()
        latest["pct_change"] = 0.01  # 仅 1%
        latest["amount_est"] = 3e9
        sc = {"min_pct_change": 2.0, "lookback_days": 5, "vol_multiplier": 3.0,
              "min_full_day_amount": 2e9}
        result = _detect_strong_start_right(d, latest, sc, {})
        assert result is None

    def test_below_ma10_and_ma20_returns_none(self):
        """close 同时 < MA10 和 < MA20 → None."""
        df = _make_kline(30)
        d = compute_indicators(df)
        latest = d.iloc[-1].copy()
        latest["pct_change"] = 0.03
        latest["close"] = min(latest["ma10"], latest["ma20"]) * 0.95
        sc = {"min_pct_change": 2.0, "lookback_days": 5, "vol_multiplier": 3.0,
              "min_full_day_amount": 2e9}
        result = _detect_strong_start_right(d, latest, sc, {})
        assert result is None

    def test_no_s0_baseline_returns_none(self):
        """前 5 天没有 S0 命中 → None."""
        df = _make_kline(30)  # 平稳 K 线, 不会命中 S0
        d = compute_indicators(df)
        latest = d.iloc[-1].copy()
        latest["pct_change"] = 0.03
        latest["amount_est"] = 3e9
        # close 站上 ma10
        latest["close"] = latest["ma10"] * 1.01
        sc = {"min_pct_change": 2.0, "lookback_days": 5, "vol_multiplier": 3.0,
              "min_full_day_amount": 2e9}
        result = _detect_strong_start_right(d, latest, sc, {"require_prior_rally": False})
        assert result is None  # 没有 S0 baseline


# ── BUY_STRONG_START 绝对量门槛 (v1.7.179) ──

def _make_strong_start_kline(today_vol: float, today_gain: float = 0.04) -> pd.DataFrame:
    """在"弱势极限"K 线末尾追加 1 根今日放量启动 bar.
    追加后倒数第 2 根=S0 地量日(量 30万, 作 baseline), 末根=今日(放量启动).
    今日量入参可控, 用于分别测"过相对量门槛但卡绝对量门槛"与"两道都过".

    前置 prepend 36 根平台期: S0 判定含 close>MA60, 短 K 线 MA60=NaN 会恒不命中,
    故补足至 60+ 根让 MA60 有效 (prepend 不影响尾部短周期均线与近10日均量)."""
    pre = pd.DataFrame({
        "date": [f"2025-12-{i+1:02d}" for i in range(36)],
        "open": [9.5 * 0.995] * 36, "high": [9.5 * 1.005] * 36,
        "low": [9.5 * 0.99] * 36, "close": [9.5] * 36, "volume": [1_000_000.0] * 36,
    })
    base = pd.concat([pre, _make_s0_eligible_kline()], ignore_index=True)
    last_close = float(base["close"].iloc[-1])
    today_close = last_close * (1 + today_gain)  # 默认 +4% (≥ min_pct_change 2%)
    today = pd.DataFrame({
        "date": ["2026-02-01"],
        "open": [last_close], "high": [today_close * 1.005],
        "low": [last_close * 0.99], "close": [today_close], "volume": [today_vol],
    })
    return pd.concat([base, today], ignore_index=True)


class TestStrongStartVolAbsGate:
    """绝对量门槛: 今日量须 ≥ 近N日均量×k, 挡住"地量×3 但绝对量仍小"的伪启动.
    构造里 baseline(地量)=30万, 今日之前近10日均量≈102万 → k=1.5 阈值≈153万."""

    _SC = {"min_pct_change": 2.0, "lookback_days": 5, "vol_multiplier": 3.0,
           "min_full_day_amount": 2e9, "vol_avg_window": 10, "min_vol_vs_avgN": 1.5}
    _S0 = {"require_prior_rally": False}

    def _run(self, today_vol, sc_override=None):
        sc = {**self._SC, **(sc_override or {})}
        df = _make_strong_start_kline(today_vol)
        d = compute_indicators(df)
        latest = d.iloc[-1].copy()
        latest["amount_est"] = 3e9  # 绝对成交额门槛单独满足, 隔离出量比门槛
        return _detect_strong_start_right(d, latest, sc, self._S0)

    def test_passes_relative_but_fails_absolute(self):
        """今日 100万: 相对地量 3.3x 过关, 但仅 ~0.98x 近10日均量 < 1.5 → None."""
        assert self._run(1_000_000) is None

    def test_passes_both_gates(self):
        """今日 200万: 相对地量 6.7x 且 ~1.96x 近10日均量 ≥ 1.5 → 命中."""
        result = self._run(2_000_000)
        assert result is not None
        assert "近10日均" in result  # detail 标注绝对量倍数

    def test_gate_disabled_lets_small_abs_vol_pass(self):
        """min_vol_vs_avgN=0 关闭绝对量门槛: 100万仅靠相对地量 3.3x 即命中 (旧行为)."""
        result = self._run(1_000_000, sc_override={"min_vol_vs_avgN": 0})
        assert result is not None


# ── BUY_STRONG_START 实时累计成交额门槛 (v1.7.417: 去10点时间限制, 改 amount_now≥5亿) ──

class TestStrongStartAmountNowGate:
    """实时累计成交额(amount_now, 未外推)硬闸门替代10:00时间门槛.
    量两道门槛都过(今日200万), 隔离出 amount_now 门槛单独验证."""

    _SC = {"min_pct_change": 2.0, "lookback_days": 5, "vol_multiplier": 3.0,
           "min_full_day_amount": 2e9, "vol_avg_window": 10, "min_vol_vs_avgN": 1.5,
           "min_amount_now": 5e8}
    _S0 = {"require_prior_rally": False}

    def _run(self, amount_now, sc_override=None):
        sc = {**self._SC, **(sc_override or {})}
        df = _make_strong_start_kline(2_000_000)  # 量门槛两道都过
        d = compute_indicators(df)
        latest = d.iloc[-1].copy()
        latest["amount_est"] = 3e9  # 预估全天额门槛单独满足, 隔离出 amount_now 门槛
        if amount_now is not None:
            latest["amount_now"] = amount_now
        return _detect_strong_start_right(d, latest, sc, self._S0)

    def test_amount_now_below_floor_returns_none(self):
        """实时累计额 3亿 < 5亿 → None (开盘前段真实成交不足, 防早盘外推伪起爆)."""
        assert self._run(3e8) is None

    def test_amount_now_above_floor_hits(self):
        """实时累计额 6亿 ≥ 5亿 → 命中."""
        assert self._run(6e8) is not None

    def test_gate_disabled_skips_amount_now(self):
        """min_amount_now=0 关闭该门槛: 不看 amount_now 即命中 (回测/旧行为)."""
        assert self._run(None, sc_override={"min_amount_now": 0}) is not None

    def test_backtest_fallback_uses_amount_est(self):
        """回测无 amount_now → 回退 amount_est(3e9) ≥ 5亿 → 命中 (回测口径不变)."""
        assert self._run(None) is not None


# ── BUY_STRONG_START 距基准涨幅上限 (v1.7.419: 现价距弱势极限基准日收盘 >X% 不报, 挡追高) ──

class TestStrongStartGainFromBaseGate:
    """现价相对弱势极限基准日收盘的涨幅封顶 — 挡"地量在很多天前、已大涨"的晚到追高.
    today_gain=0.30: 今日较 last_close +30%, 基准日(弱势极限)收盘 ≈ last_close 附近,
    故现价距基准涨幅在 +18%~+30% 之间, 必 >10% 且 <50%, 用于隔离测涨幅上限门槛."""

    _SC = {"min_pct_change": 2.0, "lookback_days": 5, "vol_multiplier": 3.0,
           "min_full_day_amount": 2e9, "vol_avg_window": 10, "min_vol_vs_avgN": 1.5}
    _S0 = {"require_prior_rally": False}

    def _run(self, max_gain):
        sc = {**self._SC, "max_gain_from_base_pct": max_gain}
        df = _make_strong_start_kline(2_000_000, today_gain=0.30)  # 现价距基准 +18%~+30%
        d = compute_indicators(df)
        latest = d.iloc[-1].copy()
        latest["amount_est"] = 3e9
        return _detect_strong_start_right(d, latest, sc, self._S0)

    def test_gain_exceeds_cap_returns_none(self):
        """现价距基准 >10% > 上限10% → None (已大涨, 不追)."""
        assert self._run(10.0) is None

    def test_gain_within_cap_hits(self):
        """上限放到 50%: 距基准(<30%) < 50% → 命中."""
        assert self._run(50.0) is not None

    def test_cap_disabled_skips(self):
        """max_gain_from_base_pct=0 关闭该门槛: 不看距基准涨幅即命中 (旧行为)."""
        assert self._run(0) is not None


# ── BUY_VOL_BREAKOUT 流动性双闸 (v1.7.428: 去10点窗 + 实时累计额≥5亿 + 外推全天额≥20亿) ──

def _make_vol_breakout_kline() -> pd.DataFrame:
    """构造一份命中"缩量突破昨高"的 K 线:
    昨日(倒2)缩量(50万 < 近10日均量×0.8), 今日(倒1)放量(200万 ≥昨2倍且≥均量1.5倍)、
    最高11.0突破昨高10.5×1.02、收盘10.5站上MA10/MA20。隔离出流动性门槛单独验证。"""
    n = 25
    closes = np.full(n, 10.0); closes[-1] = 10.5
    highs = np.full(n, 10.1); highs[-2] = 10.5; highs[-1] = 11.0
    vols = np.full(n, 1_000_000.0); vols[-2] = 500_000.0; vols[-1] = 2_000_000.0
    return pd.DataFrame({
        "date": [f"2026-04-{i + 1:02d}" for i in range(n)],
        "open": closes, "high": highs, "low": closes * 0.98,
        "close": closes, "volume": vols,
    })


class TestVolBreakoutAmountGate:
    """缩量突破流动性双闸: 外推全天额(amount_est)≥10亿 且 实时累计额(amount_now)≥5亿.
    形态/放量/突破/站均线四条都满足, 隔离出流动性门槛. (v1.7.430: 外推额门槛10亿, 回测优于20亿)"""

    _SC = {"shrink_ratio": 0.8, "vol_mult_prev": 2.0, "vol_mult_avg10": 1.5,
           "breakout_pct": 2.0, "min_full_day_amount": 1e9, "min_amount_now": 5e8}

    def _run(self, amount_est, amount_now):
        d = compute_indicators(_make_vol_breakout_kline())
        latest = d.iloc[-1].copy()
        latest["amount_est"] = amount_est
        if amount_now is not None:
            latest["amount_now"] = amount_now
        return _detect_vol_breakout(d, latest, self._SC)

    def test_est_below_10yi_returns_none(self):
        """外推全天额 8亿 < 10亿 → None."""
        assert self._run(0.8e9, 8e8) is None

    def test_amount_now_below_5yi_returns_none(self):
        """外推够(15亿)但实时累计额 3亿 < 5亿 → None (早盘真实成交不足, 防外推伪突破)."""
        assert self._run(1.5e9, 3e8) is None

    def test_both_gates_pass_hits(self):
        """外推15亿≥10亿 且 实时6亿≥5亿 → 命中."""
        assert self._run(1.5e9, 6e8) is not None

    def test_backtest_fallback_uses_amount_est(self):
        """回测无 amount_now → 回退 amount_est(15亿)≥5亿 → 命中 (回测口径仅受10亿外推额约束)."""
        assert self._run(1.5e9, None) is not None


def _make_sealed_zt_setup_kline() -> pd.DataFrame:
    """缩量设置日(倒2)是涨停封板: 倒3收9.55 → 倒2收=高=10.50(+9.9%封死)、量缩(50万)。
    今日(倒1)放量突破封板高点。模拟株冶集团06-22高开秒板缩量被误判的形态。"""
    n = 25
    closes = np.full(n, 10.0); closes[-3] = 9.55; closes[-2] = 10.50; closes[-1] = 10.80
    highs = np.full(n, 10.1); highs[-2] = 10.50; highs[-1] = 11.20   # 倒2 收=高=封板
    lows = closes * 0.98; lows[-2] = 10.20
    vols = np.full(n, 1_000_000.0); vols[-2] = 500_000.0; vols[-1] = 2_000_000.0
    return pd.DataFrame({
        "date": [f"2026-06-{i + 1:02d}" for i in range(n)],
        "open": closes, "high": highs, "low": lows, "close": closes, "volume": vols,
    })


class TestVolBreakoutSealedZtSetup:
    """封板假缩量过滤 (v1.7.519): 缩量设置日若是涨停封板(收=高+涨幅≥9.5%),
    低换手系封死非整理, 次日突破=加速段非启动 → 不触发。zt_setup_skip 关时恢复旧行为。"""

    _BASE = {"shrink_ratio": 0.8, "vol_mult_prev": 2.0, "vol_mult_avg10": 1.5,
             "breakout_pct": 2.0, "min_full_day_amount": 1e9, "min_amount_now": 5e8,
             "zt_setup_pct_min": 9.5}

    def _run(self, sc):
        d = compute_indicators(_make_sealed_zt_setup_kline())
        latest = d.iloc[-1].copy()
        latest["amount_est"] = 3e9
        latest["amount_now"] = 6e8
        return _detect_vol_breakout(d, latest, sc)

    def test_sealed_zt_setup_filtered(self):
        """昨日封板假缩量 → 默认开启过滤 → None."""
        assert self._run({**self._BASE, "zt_setup_skip": True}) is None

    def test_filter_off_restores_old_behavior(self):
        """zt_setup_skip=False → 旧行为, 照常命中(证明拦截来自该闸而非其他条件)."""
        assert self._run({**self._BASE, "zt_setup_skip": False}) is not None

    def test_normal_shrink_still_fires(self):
        """普通缩量日(收≠高, 非封板) 不受影响, 照常命中."""
        d = compute_indicators(_make_vol_breakout_kline())   # 缩量日 收10.0≠高10.5
        latest = d.iloc[-1].copy()
        latest["amount_est"] = 3e9; latest["amount_now"] = 6e8
        assert _detect_vol_breakout(d, latest, {**self._BASE, "zt_setup_skip": True}) is not None


def _make_vol_breakout_at_limit_kline() -> pd.DataFrame:
    """命中缩量突破, 但今日收盘 11.0 = 昨收 10.0×1.10 (主板涨停): 验证触发侧防追涨停闸。
    缩量日(倒2)收10.0≠高10.5(非封板, 不被 zt_setup 过滤)。"""
    n = 25
    closes = np.full(n, 10.0); closes[-1] = 11.0
    highs = np.full(n, 10.1); highs[-2] = 10.5; highs[-1] = 11.0
    vols = np.full(n, 1_000_000.0); vols[-2] = 500_000.0; vols[-1] = 2_000_000.0
    return pd.DataFrame({
        "date": [f"2026-05-{i + 1:02d}" for i in range(n)],
        "open": closes, "high": highs, "low": closes * 0.98,
        "close": closes, "volume": vols,
    })


class TestVolBreakoutChaseLimit:
    """触发侧防追涨停 (v1.7.520): 现价逼近今日涨停板(距板 ≤ buffer)不发买点, 板幅感知。
    实例: 洪田股份603800 06-25 09:44冲涨停84.95(=昨收×1.10炸板)被误发缩量突破买点。
    回测无 code(realtime=None)→ 跳过此闸, 与历史回测口径一致。"""

    _BASE = {"shrink_ratio": 0.8, "vol_mult_prev": 2.0, "vol_mult_avg10": 1.5,
             "breakout_pct": 2.0, "min_full_day_amount": 1e9, "min_amount_now": 5e8,
             "chase_limit_buffer_pct": 1.0}

    def _run(self, sc, code=None, name=""):
        d = compute_indicators(_make_vol_breakout_at_limit_kline())
        latest = d.iloc[-1].copy()
        latest["amount_est"] = 3e9; latest["amount_now"] = 6e8
        return _detect_vol_breakout(d, latest, sc, code=code, name=name)

    def test_main_board_at_limit_filtered(self):
        """主板(10%板)现价+10%=涨停 → 默认开启 → None."""
        assert self._run({**self._BASE, "chase_limit_skip": True}, code="600000") is None

    def test_filter_off_restores_old_behavior(self):
        """chase_limit_skip=False → 旧行为照常命中(证明拦截来自该闸而非其他条件)."""
        assert self._run({**self._BASE, "chase_limit_skip": False}, code="600000") is not None

    def test_chinext_plus10_still_fires(self):
        """创业板(20%板)现价+10%离涨停尚远 → 照常命中(板幅感知, 非一刀切)."""
        assert self._run({**self._BASE, "chase_limit_skip": True}, code="300001") is not None

    def test_backtest_no_code_not_blocked(self):
        """回测无 code(realtime=None)→ 不拦, 命中(与历史回测口径一致)."""
        assert self._run({**self._BASE, "chase_limit_skip": True}, code=None) is not None


# ── 成本类硬止损"上涨日不报" (v1.7.422: 现价≥昨收 当日上涨/平盘 → 不发止损催卖) ──

def _make_holding_kline(prev_close: float, last_close: float, n: int = 30) -> pd.DataFrame:
    """平缓上行的持仓 K 线: 倒数第二根=prev_close, 末根=last_close(控当日涨跌方向).
    均线落在收盘下方, 不触发跌破均线类卖点, 隔离出止损 + 上涨日闸门。"""
    body = np.linspace(last_close * 0.85, prev_close, n - 1)
    closes = np.append(body, last_close)
    return pd.DataFrame({
        "date": [f"2026-05-{i + 1:02d}" for i in range(n)],
        "open": closes, "high": closes * 1.005, "low": closes * 0.97,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    })


def _has(signals, sig_id: str) -> bool:
    return any(s.signal_id == sig_id for s in signals)


class TestHardStopUpDayGate:
    """SELL_LOSS_10 / SELL_WEAK_STOP: 现价≥昨收(当日上涨)不报, 持仓回血中不催卖."""

    def test_loss10_up_day_suppressed(self):
        """浮亏-10%但当日上涨(90 vs 昨88, +2.3%) → 不报 SELL_LOSS_10."""
        df = _make_holding_kline(prev_close=88.0, last_close=90.0)
        sigs = detect_signals(df, "short", None, None, entry_cost=100.0)  # 浮亏 -10%
        assert not _has(sigs, "SELL_LOSS_10")

    def test_loss10_down_day_fires(self):
        """浮亏-10%且当日下跌(90 vs 昨92, -2.2%) → 报 SELL_LOSS_10."""
        df = _make_holding_kline(prev_close=92.0, last_close=90.0)
        sigs = detect_signals(df, "short", None, None, entry_cost=100.0)
        assert _has(sigs, "SELL_LOSS_10")

    def test_loss10_gate_off_fires_on_up_day(self):
        """skip_on_up_day=False 关闭闸门: 上涨日也照报 (旧行为)."""
        df = _make_holding_kline(prev_close=88.0, last_close=90.0)
        sigs = detect_signals(df, "short", None, {"SELL_LOSS_10": {"skip_on_up_day": False}},
                              entry_cost=100.0)
        assert _has(sigs, "SELL_LOSS_10")

    def test_weak_stop_up_day_suppressed(self):
        """弱势极限持仓浮亏-13%但当日上涨(87 vs 昨85) → 不报 SELL_WEAK_STOP."""
        df = _make_holding_kline(prev_close=85.0, last_close=87.0)
        sigs = detect_signals(df, "short", None, None, entry_cost=100.0,
                              entry_model="BUY_WEAK_EXTREME")
        assert not _has(sigs, "SELL_WEAK_STOP")

    def test_weak_stop_down_day_fires(self):
        """弱势极限持仓浮亏-13%且当日下跌(87 vs 昨89) → 报 SELL_WEAK_STOP."""
        df = _make_holding_kline(prev_close=89.0, last_close=87.0)
        sigs = detect_signals(df, "short", None, None, entry_cost=100.0,
                              entry_model="BUY_WEAK_EXTREME")
        assert _has(sigs, "SELL_WEAK_STOP")


# ── SS 卖点去重 (v1.7.178) ──

def _make_breakdown_kline() -> pd.DataFrame:
    """多头排列(MA5>MA10>MA20)后末日急跌, 收盘跌破 MA20×0.98 (同时破 MA5/MA10)."""
    closes = np.append(np.linspace(10.0, 13.0, 29), 11.0)  # 末日由 ~13 急跌到 11
    return pd.DataFrame({
        "date": [f"2026-03-{i+1:02d}" for i in range(30)],
        "open": closes * 1.0, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(30, 1_000_000.0),
    })


def _make_downtrend_kline() -> pd.DataFrame:
    """空头排列(MA5<MA10<MA20): 单边下跌, 收盘跌破全部三根均线 ≥2%.
    用于验证去重按"均线周期"而非"最低 anchor 值"取最深 — 否则会误挑 SS1."""
    closes = np.linspace(14.0, 10.0, 30)  # 单边下跌, 均线呈空头排列且高于收盘
    return pd.DataFrame({
        "date": [f"2026-04-{i+1:02d}" for i in range(30)],
        "open": closes * 1.0, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(30, 1_000_000.0),
    })


class TestSSSellDedup:
    """持仓股一次破位跌破 MA5/MA10/MA20 时, 默认只推最深破位 (对齐 PLOSS 去重)."""

    def _ss_signals(self, user_config=None):
        df = _make_breakdown_kline()
        d = compute_indicators(df)
        close = float(d.iloc[-1]["close"])
        # SELL_BREAK_MA* 默认带盘中确认时间闸(MA5=14:30/v1.7.403, MA10·MA20=09:26/v1.7.594),
        # 读墙上时钟; 本组测试验证的是去重/emit_all, 与时钟无关 → 三条闸门全部显式归零,
        # 避免工作日闸点前(尤其凌晨)跑测试被跳过(0717 曾在 00:30 假失败)。
        cfg = {"SELL_BREAK_MA5": {"confirm_after_minute": 0},
               "SELL_BREAK_MA10": {"confirm_after_minute": 0},
               "SELL_BREAK_MA20": {"confirm_after_minute": 0}}
        for k, v in (user_config or {}).items():
            cfg[k] = {**cfg.get(k, {}), **v}
        # entry_cost = 当前价 → 无浮亏(不触发 PLOSS), 也不触发 SR1, 隔离出 SS
        sigs = detect_signals(df, entry_cost=close, user_config=cfg)
        return [s for s in sigs if s.signal_id.startswith("SELL_BREAK_MA")]

    def test_setup_breaks_all_three_ma(self):
        """前置: 构造的 K 线末日确实同时跌破 MA5/MA10/MA20 ≥2%."""
        d = compute_indicators(_make_breakdown_kline())
        latest = d.iloc[-1]
        for ma in ("ma5", "ma10", "ma20"):
            assert latest["close"] <= latest[ma] * 0.98, f"{ma} 未被跌破"

    def test_default_emits_only_deepest(self):
        """默认 emit_all=False: 只推 1 条, 且为最低支撑 MA20 → 短线卖三."""
        ss = self._ss_signals()
        assert len(ss) == 1
        assert ss[0].signal_id == "SELL_BREAK_MA20"

    def test_emit_all_pushes_all_three(self):
        """emit_all=True: 三条卖点全推 (恢复旧行为)."""
        ss = self._ss_signals(user_config={"SELL_BREAK_MA5": {"emit_all": True}})
        assert {s.signal_id for s in ss} == {"SELL_BREAK_MA5", "SELL_BREAK_MA10", "SELL_BREAK_MA20"}

    # ── 非多头排列(空头排列)场景 ──

    def _ss_signals_from(self, df, user_config=None):
        close = float(compute_indicators(df).iloc[-1]["close"])
        # 同 _ss_signals: 归零三条确认时间闸, 去掉墙上时钟依赖
        cfg = {"SELL_BREAK_MA5": {"confirm_after_minute": 0},
               "SELL_BREAK_MA10": {"confirm_after_minute": 0},
               "SELL_BREAK_MA20": {"confirm_after_minute": 0}}
        for k, v in (user_config or {}).items():
            cfg[k] = {**cfg.get(k, {}), **v}
        sigs = detect_signals(df, entry_cost=close, user_config=cfg)
        return [s for s in sigs if s.signal_id.startswith("SELL_BREAK_MA")]

    def test_downtrend_setup_is_bearish_and_breaks_all(self):
        """前置: 空头排列 MA5<MA10<MA20, 且收盘同时跌破三线 ≥2%."""
        latest = compute_indicators(_make_downtrend_kline()).iloc[-1]
        assert latest["ma5"] < latest["ma10"] < latest["ma20"]  # 空头排列
        for ma in ("ma5", "ma10", "ma20"):
            assert latest["close"] <= latest[ma] * 0.98

    def test_downtrend_continuation_suppressed(self):
        """v1.7.415: 已是空头排列的单边下跌(昨日早已在均线下方)= 破位延续, 非新鲜击穿,
        不再天天重复报。此前会每天推 SELL_BREAK_MA20, 现应不报。"""
        ss = self._ss_signals_from(_make_downtrend_kline())
        assert ss == []


def _make_below_ma_rebound_kline() -> pd.DataFrame:
    """0615京东方A 复现: 前期上涨抬高均线 → 急跌跌破 → 末日大涨反弹但收盘仍在 MA5 下方。
    末日是上涨日(+5%)且昨日已破 → 两道闸门都应拦住, 不报"跌破MA5卖出"。"""
    closes = np.append(np.linspace(10.0, 13.0, 28), [10.8, 11.4])  # 28日升至13 → 急跌10.8 → 反弹11.4
    return pd.DataFrame({
        "date": [f"2026-06-{i+1:02d}" for i in range(30)],
        "open": closes * 1.0, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(30, 1_000_000.0),
    })


class TestSSSellDirectionGuards:
    """v1.7.415: "跌破"= 向下击穿, 不是"停在均线下方"。上涨反弹日 / 破位延续日不报。"""

    def _ss_signals_from(self, df):
        close = float(compute_indicators(df).iloc[-1]["close"])
        sigs = detect_signals(df, entry_cost=close)
        return [s for s in sigs if s.signal_id.startswith("SELL_BREAK_MA")]

    def test_setup_is_up_day_below_ma5(self):
        """前置: 末日收盘在 MA5 下方(≥1%), 但当天是上涨日(pct_change>0)."""
        latest = compute_indicators(_make_below_ma_rebound_kline()).iloc[-1]
        assert latest["close"] < latest["ma5"] * 0.99   # 在 MA5 下方
        assert latest["pct_change"] > 0                 # 当天上涨

    def test_up_day_rebound_does_not_fire(self):
        """上涨反弹日(从下方弹回均线)不应报任何"跌破MAx卖出"(闸门①)."""
        ss = self._ss_signals_from(_make_below_ma_rebound_kline())
        assert ss == []


# ── 成本健全性兜底: 持仓成本与现价偏离过大(脏数据)时不触发成本类卖点 ──

def _make_uptrend_kline() -> pd.DataFrame:
    """温和上涨且收盘站在所有均线上方(不触发破位卖点), 末根轻微回落=当日下跌
    (避开 v1.7.422 成本类止损"上涨日不报"闸门), 隔离成本兜底逻辑本身."""
    closes = np.concatenate([np.linspace(10.0, 13.0, 28), [13.3, 13.1]])  # 末根 13.1<13.3=下跌日
    return pd.DataFrame({
        "date": [f"2026-05-{i+1:02d}" for i in range(30)],
        "open": closes * 1.0, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(30, 1_000_000.0),
    })


class TestCostSanityGuard:
    """成本/现价偏离 >5 倍 视为脏数据, 跳过浮亏止损等成本类卖点 (防 11332 假成本误报)."""

    def _loss_signals(self, entry_cost):
        df = _make_uptrend_kline()
        sigs = detect_signals(df, entry_cost=entry_cost)
        return [s for s in sigs if s.signal_id.startswith("SELL_LOSS")]

    def test_absurd_cost_suppresses_loss_stop(self):
        """成本=现价×50(脏数据) → 不该报 -98% 浮亏止损."""
        close = float(compute_indicators(_make_uptrend_kline()).iloc[-1]["close"])
        assert self._loss_signals(close * 50) == []

    def test_real_loss_still_fires(self):
        """成本=现价×1.2(真实浮亏 -16.7%) → SELL_LOSS_10 正常触发."""
        close = float(compute_indicators(_make_uptrend_kline()).iloc[-1]["close"])
        ids = {s.signal_id for s in self._loss_signals(close * 1.2)}
        assert "SELL_LOSS_10" in ids
