"""backtester.run_backtest 回归测试.

Bug 1: 持仓期去重逻辑失效 — in_trade 标志在内层循环找到出场日后被重置为 False,
       导致同一持仓期内每天都能重复开仓, 重叠交易重复计收益.
Bug 2: run_backtest 未像 run_holding_curve 一样注入 amount_est(量×收盘 EOD 近似),
       导致 BUY_STRONG_START 的 est_amount≥10亿 门槛恒读 0, 该模型在此路径恒不触发.
"""

import pandas as pd
import pytest

from backend.services import backtester


# ---------------------------------------------------------------- helpers

def _make_df(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    """构造日线 df: date 为 ISO 字符串(字典序=时间序), OHLC 全取 close."""
    n = len(closes)
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", periods=n)]
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": volumes,
    })


def _patch_data(monkeypatch, df: pd.DataFrame):
    """把行情/股票名册两个外部依赖打成假的."""
    async def fake_kline(code, days=0, prefer_cache=True, **kwargs):
        return df.copy()

    async def fake_stocks(include_deleted=True, **kwargs):
        return []

    monkeypatch.setattr(backtester.data_fetcher, "get_daily_kline", fake_kline)
    monkeypatch.setattr(backtester.repository, "list_all_stocks", fake_stocks)


def _exit_date(trade: dict) -> str:
    return trade["actions"][-1]["date"]


# ---------------------------------------------------------------- Bug 1

async def test_no_overlapping_trades_while_holding(monkeypatch):
    """持仓未出场期间不得重复开仓: 每天都出信号 + 永不触发卖出 → 只该有 1 笔交易."""
    n = 120
    # 恒价横盘: close==MA5==MA10, 不破 MA5×0.98 / 不达 +7% / 不满足 MA10 止损 → 持仓到回测结束
    df = _make_df([10.0] * n, [1_000_000.0] * n)
    _patch_data(monkeypatch, df)
    # 每天都报信号
    monkeypatch.setattr(backtester, "_detect_buy_signal", lambda *a, **k: "stub-signal")

    res = await backtester.run_backtest(["000001"], signal_id="S3_BUY", lookback_days=30)

    trades = res["trades"]
    # 第一笔在窗口首日开仓且持有到数据末尾, 期间其余 29 天的信号必须被拦截
    assert len(trades) == 1, (
        f"持仓期内重复开仓: 应只有1笔, 实际 {len(trades)} 笔, "
        f"buy_dates={[t['buy_date'] for t in trades]}"
    )


async def test_reentry_allowed_after_exit(monkeypatch):
    """出场之后允许再次开仓(防过度修复): 中途破MA5清仓 → 应恰好 2 笔且不重叠."""
    n = 120
    closes = [10.0] * n
    closes[90] = 9.4  # 跌 -6%(非跌停), 收盘 9.4 ≤ MA5(9.88)×0.98 → 当日清仓
    df = _make_df(closes, [1_000_000.0] * n)
    _patch_data(monkeypatch, df)
    monkeypatch.setattr(backtester, "_detect_buy_signal", lambda *a, **k: "stub-signal")

    res = await backtester.run_backtest(["000001"], signal_id="S3_BUY", lookback_days=40)

    trades = res["trades"]
    assert len(trades) == 2, (
        f"应为2笔(第80日开仓→第90日清仓, 第91日再开仓→持有到底), 实际 {len(trades)} 笔"
    )
    # 逐笔校验: 后一笔的买入日必须严格晚于前一笔的出场日
    trades_sorted = sorted(trades, key=lambda t: t["buy_date"])
    for prev, cur in zip(trades_sorted, trades_sorted[1:]):
        assert cur["buy_date"] > _exit_date(prev), (
            f"重叠交易: {cur['buy_date']} 开仓早于上一笔出场日 {_exit_date(prev)}"
        )


# ---------------------------------------------------------------- Bug 2

async def test_run_backtest_injects_amount_est(monkeypatch):
    """run_backtest 传给检测器的 window 必须带非零 amount_est(=volume×close), 对齐 run_holding_curve."""
    n = 80
    df = _make_df([10.0 + 0.02 * i for i in range(n)], [50_000_000.0] * n)
    _patch_data(monkeypatch, df)

    captured: list[pd.DataFrame] = []

    def spy_detect(signal_id, window, row, cfg, signal_cfg):
        captured.append(window)
        return None

    monkeypatch.setattr(backtester, "_detect_buy_signal", spy_detect)

    await backtester.run_backtest(["000001"], signal_id="BUY_STRONG_START", lookback_days=15)

    assert captured, "检测器根本没被调用, 测试构造有误"
    win = captured[-1]
    assert "amount_est" in win.columns, "run_backtest 未注入 amount_est 列"
    expected = win["volume"] * win["close"]
    assert (win["amount_est"] > 0).all()
    assert (win["amount_est"] - expected).abs().max() < 1e-6


async def test_strong_start_triggers_in_run_backtest(monkeypatch):
    """端到端: 构造 弱势极限地量→次日放量起爆 数据, BUY_STRONG_START 在 run_backtest 路径应真触发.

    修复前 amount_est 缺失 → est_amount 恒 0 < 10亿门槛 → 恒不触发, 本测试失败.
    """
    n = 80
    closes = [10.0 + 0.02 * i for i in range(n)]
    volumes = [50_000_000.0] * n
    # 第 n-2 天(倒数第2天): 弱势极限 — 地量(近10日最低且 ≤ 均量×0.70), 贴 MA10, 站上 MA20/MA60
    volumes[n - 2] = 20_000_000.0
    # 第 n-1 天(最后一天): 放量起爆 — +3% 涨幅, 量 6×地量 / 2.55×近10日均量,
    #   成交额 = 120e6 × ~11.9 ≈ 14.3亿 ≥ 10亿门槛(修复后才读得到)
    closes[n - 1] = closes[n - 2] * 1.03
    volumes[n - 1] = 120_000_000.0
    df = _make_df(closes, volumes)
    _patch_data(monkeypatch, df)

    # 仅放开"前置主升浪"要求(构造平缓上升趋势没有≥15%主升浪), 其余门槛全走默认真实配置
    user_config = {"BUY_WEAK_EXTREME": {"require_prior_rally": False}}

    res = await backtester.run_backtest(
        ["000001"], signal_id="BUY_STRONG_START",
        lookback_days=15, user_config=user_config,
    )

    trades = res["trades"]
    assert len(trades) == 1, f"强势起点应在最后一天触发1笔, 实际 {len(trades)} 笔"
    assert trades[0]["buy_date"] == df.iloc[-1]["date"]
    assert "成交额" in trades[0]["signal_detail"]
