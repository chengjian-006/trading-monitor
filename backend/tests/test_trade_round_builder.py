"""FIFO 回合切分纯函数测试 — build_rounds_from_trades 的边界行为."""
from backend.services.trade_round_builder import build_rounds_from_trades


def _t(tid, d, tm, direction, qty, price, fee=0.0):
    return {
        "id": tid, "trade_date": d, "trade_time": tm, "code": "600000",
        "name": "浦发银行", "direction": direction, "quantity": qty,
        "price": price, "amount": round(qty * price, 2), "fee_total": fee,
    }


class TestBuildRounds:
    def test_empty_returns_empty(self):
        assert build_rounds_from_trades([]) == []

    def test_single_buy_is_open_round(self):
        rounds = build_rounds_from_trades([_t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0)])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "open"
        assert r["open_date"] == "2026-01-05"
        assert r["entry_price"] == 10.0
        assert r["exit_price"] is None
        assert r["peak_qty"] == 1000
        assert r["close_date"] is None
        assert len(r["legs"]) == 1
        assert r["legs"][0]["running_qty"] == 1000

    def test_buy_then_full_sell_is_closed_round(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0, fee=5.0),
            _t(2, "2026-01-08", "14:00:00", "sell", 1000, 11.0, fee=6.0),
        ])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "closed"
        assert r["close_date"] == "2026-01-08"
        assert r["exit_price"] == 11.0
        # 已实现 = 卖额(11000) - 买额(10000) - 费(11) = 989
        assert r["realized_pnl"] == 989.0
        assert round(r["realized_pnl_pct"], 4) == round(989.0 / 10000 * 100, 4)
        assert r["peak_qty"] == 1000

    def test_two_independent_rounds(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0),
            _t(2, "2026-01-06", "14:00:00", "sell", 1000, 11.0),
            _t(3, "2026-01-09", "10:00:00", "buy", 500, 12.0),
        ])
        assert len(rounds) == 2
        assert rounds[0]["status"] == "closed"
        assert rounds[1]["status"] == "open"
        assert rounds[1]["open_date"] == "2026-01-09"

    def test_scaled_in_and_out_flags_and_avg_price(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0),
            _t(2, "2026-01-06", "09:31:00", "buy", 1000, 12.0),
            _t(3, "2026-01-08", "14:00:00", "sell", 500, 13.0),
            _t(4, "2026-01-09", "14:00:00", "sell", 1500, 13.0),
        ])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "closed"
        assert r["is_scaled_in"] is True
        assert r["is_scaled_out"] is True
        assert r["entry_price"] == 11.0          # (10*1000+12*1000)/2000
        assert r["peak_qty"] == 2000
        assert r["legs"][2]["running_qty"] == 1500   # 第3腿卖500后剩1500

    def test_partial_sell_open_round_realized_only_on_sold(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0),
            _t(2, "2026-01-08", "14:00:00", "sell", 400, 11.0),
        ])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "open"
        # 卖400股, FIFO成本 400*10=4000, 卖额 400*11=4400 → 已实现 400
        assert r["realized_pnl"] == 400.0
        assert r["legs"][1]["running_qty"] == 600

    def test_oversell_books_only_matched_shares(self):
        # 卖出多于持仓(交割单从持仓中途开始): 只记可匹配的1000股, 不虚增
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0),
            _t(2, "2026-01-08", "14:00:00", "sell", 1500, 11.0),
        ])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "closed"
        # 匹配1000股: 卖额=11000, 已实现=11000-10000=1000(非6500)
        assert r["realized_pnl"] == 1000.0
        assert r["total_sell_amount"] == 11000.0
        assert r["exit_price"] == 11.0
        assert r["legs"][1]["qty"] == 1000
        assert r["legs"][1]["running_qty"] == 0


from backend.services.trade_round_builder import attach_entry_signal


def _sig(sid, name, d, price=10.0, pk=1):
    return {"id": pk, "signal_id": sid, "signal_name": name, "price": price, "date": d}


class TestAttachEntrySignal:
    def _round(self, open_date="2026-01-05", entry_price=10.5):
        return {"open_date": open_date, "entry_price": entry_price,
                "entry_signal_pk": None, "entry_signal_id": None,
                "entry_model_name": None, "entry_deviation_pct": None}

    def test_no_signals_leaves_none(self):
        r = self._round()
        attach_entry_signal(r, [])
        assert r["entry_signal_id"] is None

    def test_matches_nearest_within_window(self):
        r = self._round(open_date="2026-01-05", entry_price=10.5)
        attach_entry_signal(r, [
            _sig("BUY_WEAK_EXTREME", "弱势极限", "2026-01-03", price=10.0, pk=7),
            _sig("BUY_RALLY_MA20", "回踩20MA缩量后突破昨高", "2026-01-20", price=9.0, pk=9),
        ])
        assert r["entry_signal_pk"] == 7
        assert r["entry_signal_id"] == "BUY_WEAK_EXTREME"
        assert r["entry_model_name"] == "弱势极限"
        # 偏离 = (10.5-10.0)/10.0*100 = +5.0
        assert round(r["entry_deviation_pct"], 2) == 5.0

    def test_outside_window_no_match(self):
        r = self._round(open_date="2026-01-05")
        attach_entry_signal(r, [_sig("BUY_WEAK_EXTREME", "弱势极限", "2025-12-01")],
                            window_days=7)
        assert r["entry_signal_id"] is None

    def test_tie_prefers_on_or_before_open(self):
        r = self._round(open_date="2026-01-10")
        attach_entry_signal(r, [
            _sig("BUY_A", "A", "2026-01-08", pk=1),   # 距2天, 之前
            _sig("BUY_B", "B", "2026-01-12", pk=2),   # 距2天, 之后
        ])
        assert r["entry_signal_pk"] == 1


from backend.services.trade_round_builder import group_trades_by_code


class TestGroupByCode:
    def test_groups_preserve_order(self):
        trades = [
            {"code": "600000", "trade_date": "2026-01-05", "trade_time": "09:31:00"},
            {"code": "000001", "trade_date": "2026-01-05", "trade_time": "09:32:00"},
            {"code": "600000", "trade_date": "2026-01-06", "trade_time": "09:31:00"},
        ]
        grouped = group_trades_by_code(trades)
        assert set(grouped.keys()) == {"600000", "000001"}
        assert len(grouped["600000"]) == 2
        assert grouped["600000"][0]["trade_date"] == "2026-01-05"
