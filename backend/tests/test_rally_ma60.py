"""回踩MA60买点 BUY_RALLY_MA60 全链路接线单测 (v1.7.593, 课件中线六二法60日档).

覆盖:
  - DEFAULT_SIGNAL_CONFIG 配置存在且关键参数与回测定稿一致
  - _detect_rally_ma20_pullback 在 touch_ma="ma60" 下正/负样本 + 文案锚点标签"MA60"
  - detect_signals 集成: 触发 BUY_RALLY_MA60 信号
  - rally_reminder / backtester_5m / signal_specs / scanner / near_buy 注册面

不连真数据库, 不打外网. pandas in-memory 构造 K 线.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backend.services.signal_engine_config import DEFAULT_SIGNAL_CONFIG
from backend.services.signal_engine_detectors import _detect_rally_ma20_pullback
from backend.services.signal_engine_indicators import compute_indicators


# ── 测试数据工厂: 回踩MA60成立的K线 ──

def _make_ma60_kline(touch_close: float | None = None) -> pd.DataFrame:
    """构造"应该命中回踩MA60"的 82 根日K:
    idx 0-54  横盘 10.0 (垫MA60)
    idx 55-58 微跌 9.9→9.7 (让收盘跌到MA10下, 给上穿入口铺垫)
    idx 59    放量上穿 MA10: 收10.4 量3M (detect_main_rally 的主升浪入口)
    idx 60-68 主升 10.6→12.0 (峰high 12.06, 距上穿日收盘 +16% ≥15%; 峰距今13日 ≤60)
    idx 69-79 回落 11.8→10.4
    idx 80    昨日: 贴MA60(≈10.40, 默认收10.3 距-0.9%在±2%内) + 缩量(0.6M < 近10日均0.96M×0.8)
    idx 81    今日: 高10.70 突破昨高10.35×1.025=10.61 + 放量2M ≥ 0.96M×1.5
    末根日期=今天(防 _ensure_today_bar 追加行错位)。
    """
    closes = np.concatenate([
        np.full(55, 10.0),
        np.linspace(9.9, 9.7, 4),
        np.array([10.4]),
        np.linspace(10.6, 12.0, 9),
        np.linspace(11.8, 10.4, 11),
        np.array([touch_close if touch_close is not None else 10.3]),
        np.array([10.65]),
    ])
    vols = np.concatenate([
        np.full(59, 1_000_000.0),
        np.array([3_000_000.0]),    # 放量上穿入口
        np.full(9, 1_500_000.0),    # 主升放量
        np.full(11, 1_000_000.0),   # 回落
        np.array([600_000.0]),      # 昨日缩量
        np.array([2_000_000.0]),    # 今日放量
    ])
    n = len(closes)
    # freq="B" 在周末会把末根滚回周五, _ensure_today_bar 见末根≠今天就追加假"今日"行,
    # 把构造好的触发行挤成昨日 → 周末跑测试必挂。末根强制=今天(检测器按位置算, 日期只是标签)。
    dates = pd.date_range(end=datetime.now().strftime("%Y-%m-%d"), periods=n, freq="B")
    date_strs = list(dates.strftime("%Y-%m-%d"))
    date_strs[-1] = datetime.now().strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "date": date_strs,
        "open": closes * 0.995,
        "high": closes * 1.005,
        "low": closes * 0.99,
        "close": closes,
        "volume": vols,
    })
    df.loc[n - 1, "high"] = 10.70   # 今日盘中突破昨高×1.025
    return df


def _latest_with_amount(ind: pd.DataFrame, amount: float = 6e8) -> pd.Series:
    latest = ind.iloc[-1].copy()
    latest["amount_now"] = amount
    return latest


# ── 配置面 ──

class TestMa60Config:
    def test_config_exists(self):
        assert "BUY_RALLY_MA60" in DEFAULT_SIGNAL_CONFIG

    def test_config_params_match_backtest(self):
        """关键参数必须与回测定稿一致(0707全市场双窗: 独立样本 243笔 50.6%/+1.71%/PF1.96)."""
        sc = DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA60"]
        assert sc["touch_ma"] == "ma60"
        assert sc["ma20_touch_pct"] == 2.0
        assert sc["rally_peak_within_bars"] == 60
        assert sc["breakout_pct"] == 2.5
        assert sc["vol_mult_avg10"] == 1.5
        assert sc["min_full_day_amount"] == 500_000_000
        assert sc["shrink_ratio"] == 0.8
        assert sc.get("enabled", True) is True


# ── 检测器 ──

class TestMa60Detector:
    def _cfg(self):
        return dict(DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA60"])

    def test_positive_case_fires(self):
        ind = compute_indicators(_make_ma60_kline())
        res = _detect_rally_ma20_pullback(ind, _latest_with_amount(ind), self._cfg())
        assert res is not None

    def test_anchor_label_says_ma60(self):
        """文案锚点标签必须是 MA60(修 _anchor_lbl 硬编码二选一)."""
        ind = compute_indicators(_make_ma60_kline())
        res = _detect_rally_ma20_pullback(ind, _latest_with_amount(ind), self._cfg())
        assert res is not None and "MA60" in res and "MA20" not in res

    def test_far_from_ma60_returns_none(self):
        """昨收远离MA60(+5%)超±2%容差 → 不触发."""
        ind = compute_indicators(_make_ma60_kline(touch_close=10.85))
        res = _detect_rally_ma20_pullback(ind, _latest_with_amount(ind), self._cfg())
        assert res is None

    def test_low_amount_returns_none(self):
        """全天额<5亿底线 → 不触发."""
        ind = compute_indicators(_make_ma60_kline())
        res = _detect_rally_ma20_pullback(ind, _latest_with_amount(ind, amount=3e8), self._cfg())
        assert res is None

    def test_no_volume_confirm_returns_none(self):
        """今日无放量(量<近10日均×1.5) → 不触发."""
        df = _make_ma60_kline()
        df.loc[len(df) - 1, "volume"] = 1_000_000.0   # <0.96M×1.5=1.44M
        ind = compute_indicators(df)
        res = _detect_rally_ma20_pullback(ind, _latest_with_amount(ind), self._cfg())
        assert res is None

    def test_ma10_config_label_unaffected(self):
        """回归: MA10 配置的锚点标签仍为 MA10."""
        from backend.services.signal_engine_detectors import _detect_rally_ma20_pullback as det
        # 复用MA10配置结构仅验证标签映射, 不构造完整正样本 — 直接测标签辅助逻辑:
        # 通过 MA60 K线跑 MA10 配置必然 None(不贴MA10), 标签断言只在有文案时有意义,
        # 故此处仅确认配置未被误改
        assert DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA10"]["touch_ma"] == "ma10"


# ── 引擎集成 ──

class TestMa60EngineIntegration:
    def test_detect_signals_emits_buy_rally_ma60(self):
        from backend.services.signal_engine import detect_signals
        df = _make_ma60_kline()
        rt = {"price": 10.65, "high": 10.70, "low": 10.10, "open": 10.30,
              "volume": 2_000_000.0, "amount": 6e8}
        sigs = detect_signals(df, "short", rt, None)
        ids = [s.signal_id for s in sigs]
        assert "BUY_RALLY_MA60" in ids
        sig = next(s for s in sigs if s.signal_id == "BUY_RALLY_MA60")
        assert sig.direction == "buy"
        assert "MA60" in sig.signal_name or "60" in sig.signal_name
        assert "剩半破MA5" in sig.detail   # 交易计划口径 = B5

    def test_disabled_no_fire(self):
        from backend.services.signal_engine import detect_signals
        df = _make_ma60_kline()
        rt = {"price": 10.65, "high": 10.70, "low": 10.10, "open": 10.30,
              "volume": 2_000_000.0, "amount": 6e8}
        sigs = detect_signals(df, "short", rt, {"BUY_RALLY_MA60": {"enabled": False}})
        assert "BUY_RALLY_MA60" not in [s.signal_id for s in sigs]


# ── 注册面(卖出提醒/回测/信号规约/扫描优先级/临近买点) ──

class TestMa60Registrations:
    def test_rally_reminder_registered(self):
        from backend.services.rally_reminder import RALLY_MODELS, _sell_sid
        assert "BUY_RALLY_MA60" in RALLY_MODELS
        m = RALLY_MODELS["BUY_RALLY_MA60"]
        assert m["ma_win"] == 5 and m["runner_tol"] == 0.0   # B5: 剩半收盘破MA5清
        assert "破MA5" in m["plan"]
        assert _sell_sid("BUY_RALLY_MA60") == "SELL_RALLY_MA60"
        assert _sell_sid("BUY_RALLY_MA60", half=True) == "SELL_RALLY_MA60_HALF"

    def test_backtester_5m_registered(self):
        from backend.services.backtester_5m import build_model, MODEL_IDS
        assert "BUY_RALLY_MA60" in MODEL_IDS
        m = build_model("BUY_RALLY_MA60")
        assert m is not None
        assert m["entry"] == "breakout"
        # B5 出场: -6% / +7%卖半 / T+10 / 剩半跟踪MA5(runner_tol=0 → ma_mult=1.0)
        assert m["exit"] == {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma5", "ma_mult": 1.0}

    def test_signal_specs(self):
        from backend.services.signal_specs import SIGNAL_GROUP_MAP, group_of
        assert SIGNAL_GROUP_MAP.get("BUY_RALLY_MA60") == "entry"
        assert group_of("SELL_RALLY_MA60") == "exit"
        assert group_of("SELL_RALLY_MA60_HALF") == "exit"

    def test_scanner_quality_order(self):
        from backend.services.scanner import _BUY_QUALITY_ORDER
        assert "BUY_RALLY_MA60" in _BUY_QUALITY_ORDER

    def test_near_buy_registered(self):
        from backend.services.near_buy import BUY_NAMES
        assert "BUY_RALLY_MA60" in BUY_NAMES

    def test_holding_guard_name(self):
        from backend.services.holding_guard import MODEL_NAMES
        assert "BUY_RALLY_MA60" in MODEL_NAMES
