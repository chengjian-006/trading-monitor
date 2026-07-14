"""模拟盘回放重建 + 「+7%只卖半一次」止盈闸 + 回放时钟注入 单测 (v1.7.614)。

不连库不打网, 纯内存构造 K 线 / 账本。覆盖三件事:
  1. took_half 闸: 本轮建仓已卖过半 → SELL_TAKE_PROFIT 静音 (对齐回测口径「只卖半一次」);
     没有这道闸, 卖半后每股成本不变 → 次日又触发又卖一半, 赢家被一路碾成碎仓。
  2. now 注入: SELL_BREAK_MA5 的 14:30 尾盘确认闸按注入时刻判, 不是墙上时钟。
     回放在盘后跑, 不注入则闸门恒放行 → 破位卖点在早盘 bar 就误触发。
  3. _Book 账本: 卖半后置 half_done, 清仓后复位(同股再建仓能重新止盈)。
"""
from datetime import datetime

import pandas as pd
import pytest

from backend.services.signal_engine import detect_signals
from backend.services.paper_replay import _Book


def _make_profit_kline(n: int = 30, cost: float = 10.0, gain_pct: float = 9.0) -> pd.DataFrame:
    """平稳上行的持仓K线, 末日收盘 = cost × (1+gain_pct%) → 足以触发 +7% 止盈。"""
    rows = []
    last = cost * (1 + gain_pct / 100)
    for i in range(n):
        # 前 n-1 根贴着成本价小幅震荡, 末根拉到目标涨幅
        c = cost * (1 + 0.001 * i) if i < n - 1 else last
        rows.append({
            "date": f"2026-06-{i + 1:02d}",
            "open": c * 0.995, "high": c * 1.01, "low": c * 0.99,
            "close": c, "volume": 1_000_000.0,
        })
    return pd.DataFrame(rows)


def _take_profit_ids(df, *, cost, took_half):
    sigs = detect_signals(df, "short", None, None, entry_cost=cost, took_half=took_half)
    return [s.signal_id for s in sigs if s.signal_id == "SELL_TAKE_PROFIT"]


class TestTakeProfitHalfOnce:
    """+7% 止盈只卖半一次 (v1.7.614): took_half 闸。"""

    def test_setup_is_above_target(self):
        """前置: 构造的K线末日确实浮盈 ≥ +7%(否则整组测试是空转)。"""
        df = _make_profit_kline(cost=10.0, gain_pct=9.0)
        assert float(df.iloc[-1]["close"]) >= 10.0 * 1.07

    def test_fires_when_not_taken_half(self):
        """本轮还没卖过半 → 正常触发 +7% 止盈减仓。"""
        df = _make_profit_kline(cost=10.0, gain_pct=9.0)
        assert _take_profit_ids(df, cost=10.0, took_half=False) == ["SELL_TAKE_PROFIT"]

    def test_silenced_after_taken_half(self):
        """本轮已卖过半 → 静音, 不再重复止盈(剩半交给破MA5/止损)。"""
        df = _make_profit_kline(cost=10.0, gain_pct=9.0)
        assert _take_profit_ids(df, cost=10.0, took_half=True) == []

    def test_default_is_not_taken_half(self):
        """默认 took_half=False —— 老调用方(near_buy/EOD/回测)行为不变。"""
        df = _make_profit_kline(cost=10.0, gain_pct=9.0)
        sigs = detect_signals(df, "short", None, None, entry_cost=10.0)
        assert any(s.signal_id == "SELL_TAKE_PROFIT" for s in sigs)

    def test_took_half_does_not_silence_stop_loss(self):
        """took_half 只管止盈: 已卖半后跌到 -10%, 浮亏止损照常触发(别把保护也关掉)。"""
        df = _make_profit_kline(cost=10.0, gain_pct=-12.0)   # 末日 8.8, 相对成本 10 浮亏 -12%
        df.loc[df.index[-1], "close"] = 8.8
        df.loc[df.index[-2], "close"] = 9.9                  # 末日下跌 → 不被 skip_on_up_day 挡
        sigs = detect_signals(df, "short", None, None, entry_cost=10.0, took_half=True)
        assert any(s.signal_id == "SELL_LOSS_10" for s in sigs)


class TestReplayClockInjection:
    """now 注入: SELL_BREAK_MA5 的 14:30 确认闸按注入时刻判, 不看墙上时钟。"""

    def _breakdown_df(self) -> pd.DataFrame:
        """末日向下击穿 MA5 ≥2% 且当日下跌, 昨日尚未破 → 满足 SELL_BREAK_MA5 全部条件。"""
        rows = []
        for i in range(29):
            c = 10.0
            rows.append({"date": f"2026-06-{i + 1:02d}", "open": c, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": 1_000_000.0})
        rows.append({"date": "2026-06-30", "open": 9.9, "high": 9.9, "low": 9.3,
                     "close": 9.4, "volume": 1_000_000.0})   # 破 MA5(≈10) 6%, 且是下跌日
        return pd.DataFrame(rows)

    def _ma5_fired(self, now):
        # emit_all=True: 默认 emit_all=False 只推「最深破位」(同时破三线时只留 MA20),
        # MA5 压根进不了输出 —— 那样本组会「因为去重而不触发」假通过, 验不到时钟闸。
        sigs = detect_signals(self._breakdown_df(), "short", None,
                              {"SELL_BREAK_MA5": {"emit_all": True}},
                              entry_cost=10.0, now=now)
        return any(s.signal_id == "SELL_BREAK_MA5" for s in sigs)

    def test_fires_after_1430_on_weekday(self):
        """工作日 14:35(注入) → 过了确认闸(confirm_after_minute=870), MA5 破位触发。
        本条同时是前置检查: 它若失败, 说明K线根本没满足MA5破位, 下面两条"被挡"是假通过。"""
        assert self._ma5_fired(datetime(2026, 6, 30, 14, 35)) is True   # 周二

    def test_suppressed_before_1430_on_weekday(self):
        """工作日 10:00(注入) → 未到尾盘确认闸, MA5 破位不判。"""
        assert self._ma5_fired(datetime(2026, 6, 30, 10, 0)) is False

    def test_injection_beats_wall_clock(self):
        """注入 09:35 必须被挡 —— 证明读的是注入值而非墙上时钟。
        (若引擎仍读 datetime.now(), 盘后跑测试 >870 分钟会放行 → 此断言失败)"""
        assert self._ma5_fired(datetime(2026, 6, 30, 9, 35)) is False


class TestBookHalfDone:
    """_Book 账本: 止盈闸状态随建仓/清仓正确翻转。"""

    def _book(self):
        return _Book({"id": 1, "user_id": 1, "account_key": "default", "cash": 100000.0,
                      "initial_capital": 100000.0, "max_positions": 10, "buy_position_pct": 0.2,
                      "unlimited_bullets": 0, "commission_rate": 0.00025, "min_commission": 5.0,
                      "stamp_rate": 0.001, "transfer_rate": 0.00001})

    def _buy(self, book, code="000001", qty=1000, price=10.0):
        book.apply({"side": "buy", "qty": qty, "price": price, "amount": qty * price, "fee": 5.0},
                   code=code, name="测试股", signal_id="BUY_VOL_BREAKOUT",
                   signal_name="缩量突破", direction="buy", when=datetime(2026, 6, 1, 10, 0))

    def test_take_profit_sets_half_done(self):
        """+7%卖半成交 → 该票进 half_done, 后续 bar 不再触发止盈。"""
        book = self._book()
        self._buy(book)
        book.apply({"side": "sell", "qty": 500, "price": 11.0, "amount": 5500.0, "fee": 10.0,
                    "close_position": False, "cost_basis_sold": 5002.5, "realized_pnl": 487.5,
                    "realized_pnl_pct": 9.7},
                   code="000001", name="测试股", signal_id="SELL_TAKE_PROFIT",
                   signal_name="止盈减仓 +7%", direction="reduce", when=datetime(2026, 6, 2, 10, 0))
        assert "000001" in book.half_done
        assert book.positions["000001"]["qty"] == 500

    def test_close_position_resets_half_done(self):
        """清仓 → half_done 复位, 同股日后再建仓能重新止盈(不然新仓永远卖不出止盈)。"""
        book = self._book()
        self._buy(book)
        book.half_done.add("000001")
        book.apply({"side": "sell", "qty": 1000, "price": 9.0, "amount": 9000.0, "fee": 15.0,
                    "close_position": True, "cost_basis_sold": 10005.0, "realized_pnl": -1020.0,
                    "realized_pnl_pct": -10.2},
                   code="000001", name="测试股", signal_id="SELL_LOSS_10",
                   signal_name="浮亏止损 -10%", direction="sell", when=datetime(2026, 6, 3, 10, 0))
        assert "000001" not in book.half_done
        assert "000001" not in book.positions

    def test_rebuy_reopens_gate(self):
        """同股再次建仓 → 闸重新打开。"""
        book = self._book()
        book.half_done.add("000001")
        self._buy(book)
        assert "000001" not in book.half_done

    def test_cash_and_equity_cost_track_fills(self):
        """现金与成本口径总资产随成交正确变动(定仓基准别算错)。"""
        book = self._book()
        assert book.cash == 100000.0
        self._buy(book, qty=1000, price=10.0)          # 花 10000 + 5 手续费
        assert book.cash == pytest.approx(89995.0)
        assert book.equity_cost() == pytest.approx(100000.0)   # 现金 + 持仓成本 = 本金(费用已含在成本里)
