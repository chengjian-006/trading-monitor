from backend.services import paper_trader as pt

ACCT = {"cash": 1_000_000.0, "max_positions": 10,
        "commission_rate": 0.00025, "min_commission": 5.0,
        "stamp_rate": 0.001, "transfer_rate": 0.00001}


def _acct(**kw):
    a = dict(ACCT); a.update(kw); return a


def test_buy_fee_min_commission():
    # amount=2000 -> 佣金 max(0.5,5)=5, 过户 0.02 -> 5.02
    assert round(pt.calc_buy_fee(2000.0, _acct()), 2) == 5.02


def test_sell_fee_includes_stamp():
    # amount=100000: 佣金25, 印花税100, 过户1 -> 126.0
    assert round(pt.calc_sell_fee(100000.0, _acct()), 2) == 126.0


def test_decide_buy_normal():
    # 定仓=总资产20%: 100万×20%=20万 / (10元×100) = 200手 = 2万股
    a = pt.decide(_acct(), None, {"direction": "buy", "signal_id": "BUY_RALLY_MA10", "price": 10.0},
                  held_count=0, equity_cost=1_000_000.0)
    assert a["side"] == "buy"
    assert a["qty"] == 20000
    assert a["amount"] == 200000.0
    assert a["cash_after"] == round(1_000_000.0 - 200000.0 - a["fee"], 2)


def test_decide_buy_low_cash_uses_all_remaining():
    # 现金不足一个完整20%份额("没仓位了") → 用剩余全部现金尽量买(含手续费裁到买得起的手数)。
    # equity 50万, 20%=10万; 现金3万, price 50 → 6手(3万)+费超现金, 退到5手=500股(2.5万+费)
    a = pt.decide(_acct(cash=30_000.0), None,
                  {"direction": "buy", "signal_id": "BUY_X", "price": 50.0},
                  held_count=3, equity_cost=500_000.0)
    assert a["side"] == "buy" and a["qty"] == 500


def test_decide_buy_pricey_single_lot_when_cash_ample():
    # 单手已超20%份额, 但现金充足 → 买最接近的1手(不误判资金不足)。
    # equity 10万, 20%=2万; price 300 → 单手3万>2万; 现金10万够 → 买1手100股
    a = pt.decide(_acct(cash=100_000.0), None,
                  {"direction": "buy", "signal_id": "BUY_X", "price": 300.0},
                  held_count=0, equity_cost=100_000.0)
    assert a["side"] == "buy" and a["qty"] == 100


# ── 无限子弹账户(unlimited): 5%/笔, 现金可透支/不限持仓/可加仓 ──

def _unlimited(**kw):
    a = dict(ACCT, buy_position_pct=0.05, unlimited_bullets=1, max_positions=9999)
    a.update(kw); return a


def test_unlimited_buy_uses_5pct():
    # 无限子弹: 每笔=总资产5%。100万×5%=5万 / (10元×100)=50手=5000股
    a = pt.decide(_unlimited(), None, {"direction": "buy", "signal_id": "BUY_X", "price": 10.0},
                  held_count=0, equity_cost=1_000_000.0)
    assert a["side"] == "buy" and a["qty"] == 5000
    assert a["amount"] == 50000.0


def test_unlimited_buy_ignores_low_cash():
    # 现金几乎为0也照买5%份额(现金可透支为负), 不返回"资金不足"。
    a = pt.decide(_unlimited(cash=100.0), None, {"direction": "buy", "signal_id": "BUY_X", "price": 10.0},
                  held_count=0, equity_cost=1_000_000.0)
    assert a["side"] == "buy" and a["qty"] == 5000
    assert a["cash_after"] < 0   # 透支


def test_unlimited_buy_ignores_position_full():
    # 持仓数远超普通上限也照买(不限持仓数)。
    a = pt.decide(_unlimited(), None, {"direction": "buy", "signal_id": "BUY_X", "price": 10.0},
                  held_count=500, equity_cost=1_000_000.0)
    assert a["side"] == "buy"


def test_unlimited_buy_already_held_adds():
    # 已持仓再触发买点 → 加仓(不跳过), note 标"加仓"。
    a = pt.decide(_unlimited(), {"qty": 5000, "cost_amount": 50000.0},
                  {"direction": "buy", "signal_id": "BUY_X", "price": 10.0},
                  held_count=1, equity_cost=1_000_000.0)
    assert a["side"] == "buy" and a["note"] == "加仓"


def test_decide_buy_already_held_skips():
    a = pt.decide(_acct(), {"qty": 10000, "cost_amount": 100000.0},
                  {"direction": "buy", "signal_id": "BUY_RALLY_MA10", "price": 10.0},
                  held_count=1, equity_cost=1_000_000.0)
    assert a["side"] == "skip" and a["reason"] == "已持仓"


def test_decide_buy_positions_full_skips():
    a = pt.decide(_acct(), None, {"direction": "buy", "signal_id": "BUY_RALLY_MA10", "price": 10.0},
                  held_count=10, equity_cost=1_000_000.0)
    assert a["side"] == "skip" and a["reason"] == "仓位满"


def test_decide_buy_insufficient_cash_skips():
    a = pt.decide(_acct(cash=500.0), None, {"direction": "buy", "signal_id": "BUY_X", "price": 10.0},
                  held_count=0, equity_cost=500.0)
    assert a["side"] == "skip"


def test_decide_sell_full_clears_position():
    pos = {"qty": 10000, "cost_amount": 100000.0}
    a = pt.decide(_acct(), pos, {"direction": "sell", "signal_id": "SELL_BREAK_MA10", "price": 11.0},
                  held_count=1, equity_cost=200000.0)
    assert a["side"] == "sell" and a["qty"] == 10000 and a["close_position"] is True
    # 卖出额11万, 费=佣金27.5+印花110+过户1.1=138.6, 净=109861.4, 成本10万 -> 盈9861.4
    assert round(a["realized_pnl"], 1) == 9861.4


def test_decide_sell_half():
    pos = {"qty": 10000, "cost_amount": 100000.0}
    a = pt.decide(_acct(), pos, {"direction": "reduce", "signal_id": "SELL_LOSS_5", "price": 9.0},
                  held_count=1, equity_cost=180000.0)
    assert a["side"] == "sell" and a["qty"] == 5000 and a["close_position"] is False
    assert a["cost_basis_sold"] == 50000.0


def test_decide_sell_not_held_skips():
    a = pt.decide(_acct(), None, {"direction": "sell", "signal_id": "SELL_BREAK_MA10", "price": 11.0},
                  held_count=0, equity_cost=1_000_000.0)
    assert a["side"] == "skip" and a["reason"] == "未持仓"


# ── 板块交易权限(模拟盘"无权限"失败判定; 当前仅开通创业板) ──

def test_permission_main_board_ok():
    assert pt.board_permission_error("600519") is None   # 沪主板
    assert pt.board_permission_error("000725") is None   # 深主板


def test_permission_chinext_ok():
    # 创业板已开通(GRANTED_BOARDS 含 chinext)
    assert pt.board_permission_error("300620") is None   # 光库科技
    assert pt.board_permission_error("301171") is None


def test_permission_star_blocked():
    assert pt.board_permission_error("688981") == "无科创板交易权限"


def test_permission_bse_blocked():
    assert pt.board_permission_error("830799") == "无北交所交易权限"
    assert pt.board_permission_error("920819") == "无北交所交易权限"
