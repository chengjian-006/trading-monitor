"""5分钟真实口径引擎「诚实化」测试 (修胜率前视偏差, v1.7.x).

锁定六类行为:
  1. 后复权5分钟bar → 按日重定标到日线前复权刻度 (rescale_day_bars)
  2. fire_5m 逐根注入只用"该时刻已知"信息: close=当bar现价(非全天收盘)、high=游程最高(非全天最高)、
     MA随现价增量修正、sub末行同步补丁 —— 复刻实时扫描器构造
  3. 盘中触发但尾盘走弱的失败样本必须被抓到 (旧EOD日线闸把它系统性剔除 = 前视偏差根源)
  4. 收盘型模型入场价 = 触发时刻现价 (非全天收盘)
  5. 贴板不追(chase_limit)回测/实盘对称: 回测传 code 后同样拦截
  6. 竞价弱转强板幅按股票代码判(30/68=20cm), 不是按日期
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backend.services.intraday_estimator import project_full_day_volume
from backend.services.signal_engine_indicators import compute_indicators
from backend.services.backtester_5m import (
    build_model, daily_could_fire, eod_trades, fire_5m, fire_5m_detail,
    rescale_day_bars, scan_trades_5m,
)


# ── 工厂 ──

def _mk_daily(n=65, base=10.0, today=None):
    """n根横盘日K + 可定制末根(今日). today={open,high,low,close,volume}."""
    closes = np.full(n, base)
    df = pd.DataFrame({
        "date": [f"2026-{3 + i // 28:02d}-{i % 28 + 1:02d}" for i in range(n)],
        "open": closes * 0.999, "high": closes * 1.001, "low": closes * 0.998,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    })
    if today:
        for k, v in today.items():
            df.loc[n - 1, k] = v
    return df


def _ind(df):
    d = compute_indicators(df)
    d["amount_est"] = d["volume"] * d["close"]
    return d


def _probe_model(record, earliest=0, entry="breakout"):
    """假模型: 检测器只记录收到的 (sub, latest) 快照, 永不触发."""
    def det(d, latest, sc):
        record.append({
            "close": float(latest["close"]), "high": float(latest["high"]),
            "ma10": float(latest["ma10"]), "volume": float(latest["volume"]),
            "pct_change": float(latest.get("pct_change", 0) or 0),
            "sub_last_close": float(d["close"].iloc[-1]),
            "sub_last_vol": float(d["volume"].iloc[-1]),
        })
        return None
    return {"id": "PROBE", "name": "probe", "det": det, "use_s0": False, "s0": None,
            "entry": entry, "cfg": {"intraday_earliest_minute": earliest}, "_eval_all": True,
            "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma5", "ma_mult": 1.0}}


# 后复权bar = 前复权价×2 (factor应=0.5); 量/额为原始值
_HFQ = 2.0


def _bars(specs):
    """specs: [(mn, h, l, c, vol, amt)] 前复权刻度 → 转成表里存的后复权bar."""
    return [(mn, h * _HFQ, l * _HFQ, c * _HFQ, v, a) for (mn, h, l, c, v, a) in specs]


# ── 1. 重定标 ──

class TestRescale:
    def test_scales_prices_not_volume(self):
        bars = _bars([(575, 10.2, 10.0, 10.1, 5e6, 1.01e8),
                      (900, 9.75, 9.65, 9.7, 2e6, 0.4e8)])
        scaled, factor = rescale_day_bars(bars, daily_close=9.7)
        assert factor == pytest.approx(0.5)
        mn, h, l, c, v, a = scaled[0]
        assert (h, l, c) == (pytest.approx(10.2), pytest.approx(10.0), pytest.approx(10.1))
        assert v == 5e6 and a == 1.01e8

    def test_empty_bars(self):
        scaled, factor = rescale_day_bars([], daily_close=9.7)
        assert scaled == [] and factor == 1.0


# ── 2. fire_5m 诚实注入 ──

class TestFire5mInjection:
    def _setup(self, earliest=0):
        df = _mk_daily(today={"open": 10.0, "high": 10.35, "low": 9.6,
                              "close": 9.7, "volume": 60e6})
        ind = _ind(df)
        rec = []
        model = _probe_model(rec, earliest=earliest)
        bars = _bars([(575, 10.2, 10.0, 10.1, 5e6, 0.505e8),
                      (580, 10.35, 10.1, 10.3, 8e6, 0.82e8),
                      (900, 9.75, 9.65, 9.7, 2e6, 0.2e8)])
        return ind, model, bars, rec

    def test_close_is_bar_close_not_eod(self):
        ind, model, bars, rec = self._setup()
        fire_5m(model, ind, ind.iloc[-1].copy(), bars, prev_close=10.0)
        assert rec[0]["close"] == pytest.approx(10.1)   # 首bar现价, 非全天收盘9.7
        assert rec[1]["close"] == pytest.approx(10.3)

    def test_high_is_running_high_not_day_high(self):
        ind, model, bars, rec = self._setup()
        fire_5m(model, ind, ind.iloc[-1].copy(), bars, prev_close=10.0)
        # 首bar时刻已知最高 = max(开盘10.0, 首bar高10.2) = 10.2, 而非全天最高10.35
        assert rec[0]["high"] == pytest.approx(10.2)
        assert rec[1]["high"] == pytest.approx(10.35)

    def test_ma10_shifted_by_bar_close(self):
        ind, model, bars, rec = self._setup()
        ma10_full = float(ind["ma10"].iloc[-1])            # 含全天收盘9.7的MA10
        fire_5m(model, ind, ind.iloc[-1].copy(), bars, prev_close=10.0)
        expect = ma10_full + (10.1 - 9.7) / 10
        assert rec[0]["ma10"] == pytest.approx(expect, abs=1e-9)

    def test_pct_change_from_bar_close(self):
        ind, model, bars, rec = self._setup()
        fire_5m(model, ind, ind.iloc[-1].copy(), bars, prev_close=10.0)
        assert rec[0]["pct_change"] == pytest.approx(0.01)   # 10.1/10.0-1

    def test_volume_is_projection_of_cum(self):
        ind, model, bars, rec = self._setup()
        fire_5m(model, ind, ind.iloc[-1].copy(), bars, prev_close=10.0)
        expect = project_full_day_volume(5e6, datetime(2000, 1, 1, 9, 35)) or 5e6
        assert rec[0]["volume"] == pytest.approx(expect)

    def test_sub_last_row_patched_consistently(self):
        ind, model, bars, rec = self._setup()
        fire_5m(model, ind, ind.iloc[-1].copy(), bars, prev_close=10.0)
        assert rec[0]["sub_last_close"] == rec[0]["close"]
        assert rec[0]["sub_last_vol"] == rec[0]["volume"]

    def test_respects_earliest_minute(self):
        ind, model, bars, rec = self._setup(earliest=880)
        fire_5m(model, ind, ind.iloc[-1].copy(), bars, prev_close=10.0)
        assert len(rec) == 1 and rec[0]["close"] == pytest.approx(9.7)   # 只有900那根


# ── 3. 靶心: 盘中触发尾盘走弱 (旧EOD闸漏掉的失败样本) ──

def _fade_fixture():
    """缩量突破场景: 昨缩量, 今早盘放量突破昨高2%(10:00前), 尾盘跳水收在MA10下。
    EOD口径: 收盘9.7 < MA10 → 站位条件失败, 日线检测不触发 (前视偏差: 剔掉这笔失败交易)。
    盘中口径: 10:00时现价10.3 > MA10 且量/突破全过 → 必须触发。"""
    df = _mk_daily(n=80, today={"open": 10.05, "high": 10.38, "low": 9.6,
                                "close": 9.7, "volume": 60e6})
    df.loc[len(df) - 2, "volume"] = 500_000.0          # 昨日缩量 0.5×均量
    df.loc[len(df) - 2, "close"] = 9.9                 # 未封板(≠昨高)
    ind = _ind(df)
    # 早盘量能充足: 9:35累计1200万手投影后远超均量1.5倍/昨量2倍
    bars = _bars([
        (575, 10.25, 10.05, 10.22, 12e6, 1.2e9),
        (580, 10.38, 10.2, 10.3, 10e6, 1.0e9),
        (585, 10.3, 10.1, 10.15, 5e6, 0.5e9),
        (890, 9.8, 9.65, 9.72, 3e6, 0.3e9),
        (900, 9.75, 9.6, 9.7, 3e6, 0.3e9),
    ])
    # 出场需要未来K线: 追加10根
    fut = _mk_daily(n=80)
    ext = pd.concat([df, fut.iloc[:12].assign(date=[f"2026-08-{i+1:02d}" for i in range(12)])],
                    ignore_index=True)
    ind_ext = _ind(ext)
    return ind_ext, {ind_ext["date"].iloc[79]: bars}


class TestIntradayFadeCaught:
    def _model(self):
        return build_model("BUY_VOL_BREAKOUT", temp_config={"BUY_VOL_BREAKOUT": {
            "min_full_day_amount": 0, "min_amount_now": 0, "zt_setup_skip": False,
            "REQ_PREV_SHADOW": False}})

    def test_old_daily_gate_misses_it(self):
        ind, day5m = _fade_fixture()
        sub = ind.iloc[:80]
        assert daily_could_fire(self._model(), sub, ind.iloc[79]) is False

    def test_honest_scan_catches_it(self):
        ind, day5m = _fade_fixture()
        d0 = str(ind["date"].iloc[79])
        trades = scan_trades_5m(self._model(), ind, day5m, start=d0, end=d0)
        assert len(trades) == 1
        t = trades[0]
        assert t["buy_date"] == d0
        # breakout族入场价 = 昨高×1.02 (开盘未跳过触发价); 昨高=10.0×1.001(fixture只改了昨收)
        assert t["buy_price"] == pytest.approx(10.0 * 1.001 * 1.02, rel=1e-3)

    def test_fast_path_equals_exact_path(self):
        """价格下限/粗步长快路 与 逐根精确评估(_eval_all) 触发结果一致(下限是硬必要条件)。"""
        ind, day5m = _fade_fixture()
        i = 79
        d0 = str(ind["date"].iloc[i])
        sub = ind.iloc[:i + 1]
        prev_close = float(ind["close"].iloc[i - 1])
        fast = dict(self._model())
        exact = dict(self._model()); exact["_eval_all"] = True
        r_fast = fire_5m_detail(fast, sub, ind.iloc[i].copy(), day5m[d0], prev_close)
        r_exact = fire_5m_detail(exact, sub, ind.iloc[i].copy(), day5m[d0], prev_close)
        assert r_fast[0] is True and r_exact[0] is True
        assert r_fast[3] == pytest.approx(r_exact[3])   # 同一触发现价
        assert r_fast[4] == r_exact[4]                  # 同一触发分钟


# ── 4. 收盘型模型入场 = 触发时刻现价 ──

class TestCloseEntryAtTriggerPrice:
    def test_platform_enters_at_trigger_bar_price(self):
        # 平台8日横盘10.0, 今日14:40现价10.30突破上沿, 15:00回落收9.95(EOD口径都测不到)
        df = _mk_daily(n=80, today={"open": 10.05, "high": 10.32, "low": 9.9,
                                    "close": 9.95, "volume": 3e6})
        ind0 = _ind(df)
        fut = _mk_daily(n=80)
        ext = pd.concat([df, fut.iloc[:12].assign(date=[f"2026-08-{i+1:02d}" for i in range(12)])],
                        ignore_index=True)
        ind = _ind(ext)
        d0 = str(ind["date"].iloc[79])
        bars = _bars([
            (575, 10.06, 10.0, 10.02, 1e6, 1e7),
            (880, 10.30, 10.2, 10.30, 1e6, 1e7),   # 14:40 收盘确认档触发
            (900, 10.0, 9.9, 9.95, 1e6, 1e7),
        ])
        model = build_model("BUY_PLATFORM_BREAKOUT", temp_config={"BUY_PLATFORM_BREAKOUT": {
            "L": 8, "REQ_PRIOR": False, "REQ_RISE": False, "REQ_HOLD": False,
            "REQ_VOL": False, "min_full_day_amount": 0, "A": 0.15, "BUF": 0.005,
            "MODE": "close", "intraday_earliest_minute": 880}})
        trades = scan_trades_5m(model, ind, {d0: bars}, start=d0, end=d0)
        assert len(trades) == 1
        assert trades[0]["buy_price"] == pytest.approx(10.30, rel=1e-3)  # 触发bar现价, 非收盘9.95


# ── 5. 贴板不追 回测/实盘对称 ──

class TestChaseLimitParity:
    def _fixture(self):
        # 主板票: 昨收9.45, 今日冲10.40(+10.05%≈涨停贴板), 缩量突破条件全满足
        df = _mk_daily(n=80, base=9.5, today={"open": 9.6, "high": 10.45, "low": 9.5,
                                              "close": 10.40, "volume": 60e6})
        df.loc[len(df) - 2, "volume"] = 500_000.0
        df.loc[len(df) - 2, "close"] = 9.45
        df.loc[len(df) - 2, "high"] = 9.5
        fut = _mk_daily(n=80, base=9.5)
        ext = pd.concat([df, fut.iloc[:12].assign(date=[f"2026-08-{i+1:02d}" for i in range(12)])],
                        ignore_index=True)
        ind = _ind(ext)
        d0 = str(ind["date"].iloc[79])
        bars = _bars([(575, 10.42, 10.3, 10.40, 20e6, 2e9),
                      (900, 10.45, 10.35, 10.40, 10e6, 1e9)])
        model = build_model("BUY_VOL_BREAKOUT", temp_config={"BUY_VOL_BREAKOUT": {
            "min_full_day_amount": 0, "min_amount_now": 0, "zt_setup_skip": False,
            "REQ_PREV_SHADOW": False}})
        return model, ind, {d0: bars}, d0

    def test_with_code_blocks_at_limit(self):
        model, ind, day5m, d0 = self._fixture()
        trades = scan_trades_5m(model, ind, day5m, start=d0, end=d0, code="600100")
        assert trades == []          # 现价+10.05%贴板 → 实盘不发, 回测同拦

    def test_without_code_backward_compatible(self):
        model, ind, day5m, d0 = self._fixture()
        trades = scan_trades_5m(model, ind, day5m, start=d0, end=d0)
        assert len(trades) == 1


# ── 7. 弱势极限走EOD快速路径(收盘入场无前视) ──

class TestWeakExtremeEodRouting:
    def test_weak_is_eod_honest(self):
        assert build_model("BUY_WEAK_EXTREME").get("eod_honest") is True

    def test_other_models_not_eod_honest(self):
        for mid in ("BUY_VOL_BREAKOUT", "BUY_RALLY_MA10", "BUY_PLATFORM_BREAKOUT",
                    "BUY_STRONG_START"):
            assert not build_model(mid).get("eod_honest")

    def test_refresher_splits_models(self):
        from backend.services.model_winrate_refresher import _5M_MODEL_IDS, _EOD_MODEL_IDS
        assert "BUY_WEAK_EXTREME" in _EOD_MODEL_IDS
        assert "BUY_WEAK_EXTREME" not in _5M_MODEL_IDS
        assert "BUY_VOL_BREAKOUT" in _5M_MODEL_IDS

    def test_eod_trades_enters_at_close(self):
        # 造一个弱势极限成立日: 主升浪后缩量地量贴MA10
        n = 90
        closes = np.concatenate([
            np.linspace(8.0, 11.5, 60),      # +44% 主升浪
            np.linspace(11.5, 10.5, 20),     # 回落到均线带
            np.full(10, 10.5),               # 缩量地量横盘贴线
        ])
        vols = np.concatenate([np.full(60, 2_000_000.0), np.full(20, 1_500_000.0),
                               np.full(10, 400_000.0)])   # 尾段地量
        df = pd.DataFrame({
            "date": [f"2026-{1 + i // 28:02d}-{i % 28 + 1:02d}" for i in range(n)],
            "open": closes, "high": closes * 1.005, "low": closes * 0.997,
            "close": closes, "volume": vols,
        })
        ind = _ind(df)
        d0, d1 = str(ind["date"].iloc[85]), str(ind["date"].iloc[89])
        trades = eod_trades(build_model("BUY_WEAK_EXTREME"), ind, d0, d1, code="600001")
        # 触发的话入场价必须=当日收盘(收盘入场语义)
        for t in trades:
            i = list(ind["date"].astype(str)).index(t["buy_date"])
            assert t["buy_price"] == pytest.approx(float(ind["close"].iloc[i]), rel=1e-6)


# ── 8. 竞价弱转强板幅按代码判 ──

class TestAuctionLimByCode:
    def _fixture_gap12(self):
        """T-1强势缩量小回调, T高开12%: 创业板(20cm)应触发, 主板(10cm, 上限9%)不应。

        T=idx89: 20日前收盘9.8 → T-1收11.8 = +20.4%强势; T-1收11.8>MA10(≈11.72);
        T-1量0.5M<均量0.95M×0.8 缩量; 相对前日+0.85%在小回调带; 尾部append 12根供T+10出场。"""
        closes = np.concatenate([
            np.full(60, 9.8),                  # 长横盘垫MA60
            np.full(10, 9.8),                  # 20日窗前半仍9.8 (p20c=9.8)
            np.linspace(10.3, 11.8, 10),       # 急拉段
            np.full(8, 11.7),                  # 高位小整理(低于T-1收, 保pc>MA10)
            np.array([11.8, 13.22]),           # T-1 +0.85%小回调带内, T 高开12%
            np.full(12, 13.0),                 # 出场尾窗(T+10时停)
        ])
        n = len(closes)
        vols = np.full(n, 1_000_000.0)
        vols[88] = 500_000.0                   # T-1 缩量
        df = pd.DataFrame({
            "date": [f"2026-{1 + i // 28:02d}-{i % 28 + 1:02d}" for i in range(n)],
            "open": closes * 0.999, "high": closes * 1.003,
            "low": closes * 0.996, "close": closes, "volume": vols,
        })
        df.loc[89, "open"] = 11.8 * 1.12       # T日开盘 = 高开12%
        return df

    def test_chinext_20cm_fires(self):
        from backend.services.model_winrate_refresher import _auction_trades
        ind = _ind(self._fixture_gap12())
        assert len(_auction_trades(ind, code="300123")) >= 0  # 接口存在
        # 高开12%在创业板(涨停20%, 上限19%)内 → 触发
        assert len(_auction_trades(ind, code="300123")) == 1

    def test_mainboard_10cm_rejects(self):
        from backend.services.model_winrate_refresher import _auction_trades
        ind = _ind(self._fixture_gap12())
        # 主板涨停10%, 高开12%已超 gap<lim-0.01=9% 上限 → 不触发(高开超限=一字近涨停买不进)
        assert _auction_trades(ind, code="600123") == []

    def test_weekly_backtest_lim_by_code(self):
        from backend.services.model_backtest_weekly import _backtest_one
        df = self._fixture_gap12()
        res300 = _backtest_one(df, start_date="2026-01-01", code="300123")
        res600 = _backtest_one(df, start_date="2026-01-01", code="600123")
        assert len(res300["竞价弱转强"]) == 1
        assert len(res600["竞价弱转强"]) == 0
